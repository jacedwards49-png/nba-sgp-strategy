import math
import random
import time
import requests
import pandas as pd
import streamlit as st
from datetime import datetime

# =========================================================
# STREAMLIT SETUP
# =========================================================
st.set_page_config(page_title="NBA SGP Ultimate Builder (Option A)", layout="centered")
st.title("NBA SGP Ultimate Builder (Option A)")
st.caption(
    "Team-only input • API-Basketball (API-Sports) • Last 5 only • 5/5 gate • Minutes gate • Floor lines • "
    "Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# =========================================================
# API-BASKETBALL (API-SPORTS) CONFIG
# =========================================================
API_BASE = "https://v1.basketball.api-sports.io"

# Pull from Streamlit secrets (preferred) or env var fallback
API_KEY = st.secrets.get("APISPORTS_KEY", None)

if not API_KEY:
    st.error("Missing API key. Add this to Streamlit Secrets:\n\nAPISPORTS_KEY = \"YOUR_KEY\"")
    st.stop()

HEADERS = {
    "x-apisports-key": API_KEY,
    "accept": "application/json",
}

# =========================================================
# SEASON AUTO-DETECTION
# NBA season label like 2025-26 => season_year is 2025
# =========================================================
def current_season_label_and_start_year():
    today = datetime.today()
    year = today.year
    if today.month >= 10:
        start_year = year
        label = f"{year}-{str(year + 1)[-2:]}"
    else:
        start_year = year - 1
        label = f"{year - 1}-{str(year)[-2:]}"
    return label, start_year

SEASON_LABEL, DEFAULT_SEASON_YEAR = current_season_label_and_start_year()

# =========================================================
# LOCKED MODEL RULES (OPTION A)
# =========================================================
VARIANCE_RANK = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}
PREF_ORDER = ["REB", "AST", "PRA", "PTS"]

NO_BET_MESSAGES = [
    "❌ No bets here home boy, move to next matchup",
    "🧊 Cold game — zero edge",
    "🚫 Nothing clean here, pass it",
    "📉 Variance too high — bankroll protection engaged",
    "😴 This matchup ain't it",
    "🤷 Nothing qualifies — next one up",
]

def parse_matchup(matchup: str):
    parts = matchup.upper().replace("@", "VS").replace("VS.", "VS").split("VS")
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) != 2:
        raise ValueError("Matchup must look like: LAL vs DAL")
    return parts[0], parts[1]

def minutes_gate(last5: list[dict]) -> bool:
    mins = [g["min"] for g in last5]
    return (
        len(mins) == 5
        and (all(m >= 28 for m in mins) or sum(1 for m in mins if m > 30) >= 4)
    )

def floor_line(values: list[int]) -> int:
    return int(math.floor(min(values) * 0.90)) if values else 0

def build_floor_output(last5: list[dict]) -> pd.DataFrame:
    stats = {
        "REB": [g["reb"] for g in last5],
        "AST": [g["ast"] for g in last5],
        "PRA": [g["pra"] for g in last5],
        "PTS": [g["pts"] for g in last5],
    }
    rows = []
    for stat, arr in stats.items():
        rows.append(
            {
                "stat": stat,
                "line": floor_line(arr),
                "pref": PREF_ORDER.index(stat),
                "variance": VARIANCE_RANK[stat],
            }
        )
    return pd.DataFrame(rows).sort_values(by=["pref", "variance", "stat"])

def make_safe(chosen: list[dict]) -> list[dict]:
    # SAFE = remove highest-variance leg; tie-breaker: remove PTS first
    if len(chosen) <= 3:
        return chosen

    def key(x):
        pts_bonus = 1 if x["stat"] == "PTS" else 0
        return (x["variance"], pts_bonus)

    worst = max(chosen, key=key)
    return [x for x in chosen if x is not worst]

# =========================================================
# API HELPERS (RETRY + REAL ERROR REPORTING)
# =========================================================
def api_get(path: str, params: dict, retries: int = 3, timeout: int = 25) -> dict:
    url = f"{API_BASE}/{path.lstrip('/')}"
    last_err = None

    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)

            # Surface real error details
            if r.status_code >= 400:
                raise RuntimeError(
                    f"HTTP {r.status_code} calling {r.url}\n\nResponse:\n{r.text[:2000]}"
                )

            j = r.json()

            # API-Sports sometimes returns 200 but includes errors
            if isinstance(j, dict) and j.get("errors"):
                raise RuntimeError(f"API returned errors: {j.get('errors')}")

            return j

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as e:
            last_err = e
            time.sleep(1.2 * attempt)
            continue

        except Exception as e:
            last_err = e
            break

    raise RuntimeError(f"API request failed after retries calling {url} with params={params}") from last_err

# =========================================================
# API-BASKETBALL DATA FUNCTIONS
# =========================================================
@st.cache_data(ttl=86400)
def get_teams_map(league_id: int, season_year: int) -> dict:
    j = api_get("teams", {"league": league_id, "season": season_year})
    # expected shape: {"response": [{...team...}]}
    teams = j.get("response", [])
    out = {}
    for item in teams:
        team = item.get("team", {})
        name = team.get("name", "")
        code = (team.get("code") or "").upper()
        tid = team.get("id")

        # code is what we want (LAL, BOS, etc). Keep only those.
        if code and tid:
            out[code] = {"team_id": int(tid), "team_name": name}
    return out

@st.cache_data(ttl=21600)
def get_team_players(team_id: int, season_year: int) -> pd.DataFrame:
    # API-Basketball: /players with team + season
    j = api_get("players", {"team": team_id, "season": season_year})
    rows = j.get("response", [])
    # Flatten to a dataframe of id + name
    flat = []
    for it in rows:
        player = it.get("player", {})
        pid = player.get("id")
        firstname = player.get("firstname", "")
        lastname = player.get("lastname", "")
        name = (firstname + " " + lastname).strip()
        if pid and name:
            flat.append({"player_id": int(pid), "player": name})
    return pd.DataFrame(flat)

@st.cache_data(ttl=600)
def get_player_last_games(player_id: int, season_year: int, last_n: int = 5) -> list[dict]:
    # API-Basketball: /players/statistics supports player + season
    # We pull recent games by sorting on "game.date"
    j = api_get("players/statistics", {"id": player_id, "season": season_year})
    rows = j.get("response", [])

    # Sort newest first (string ISO dates compare ok)
    rows = sorted(rows, key=lambda x: str(x.get("game", {}).get("date", "")), reverse=True)

    out = []
    for r in rows:
        mins_raw = r.get("minutes")
        pts = r.get("points")
        reb = r.get("totReb") if r.get("totReb") is not None else r.get("rebounds")
        ast = r.get("assists")

        # Guard missing values
        if mins_raw is None or pts is None or reb is None or ast is None:
            continue

        # minutes can be "32" or "32:10" depending on provider
        try:
            mins = int(str(mins_raw).split(":")[0])
        except Exception:
            continue

        out.append(
            {
                "min": int(mins),
                "pts": int(pts),
                "reb": int(reb),
                "ast": int(ast),
                "pra": int(pts) + int(reb) + int(ast),
            }
        )
        if len(out) == last_n:
            break

    return out

# =========================================================
# UI
# =========================================================
matchup = st.text_input("Matchup (team acronyms)", value="LAL vs DAL")

c1, c2, c3 = st.columns(3)
with c1:
    legs_n = st.slider("Final slip legs", 3, 5, 4)
with c2:
    risk_mode = st.selectbox("Risk mode", ["Safe", "Ideal", "Higher-risk"])
with c3:
    show_debug = st.toggle("Show debug", value=False)

st.caption(f"Auto season detected: **{SEASON_LABEL}**  (API season year = **{DEFAULT_SEASON_YEAR}**)")

# Advanced knobs (optional)
with st.expander("Advanced (API / stability)", expanded=False):
    league_id = st.number_input("League ID (NBA)", min_value=1, value=12, step=1)
    season_year = st.number_input(
        "Season start year (IMPORTANT: 2025-26 season => 2025)",
        min_value=2000,
        max_value=2100,
        value=int(DEFAULT_SEASON_YEAR),
        step=1,
    )
    max_players_per_team = st.slider(
        "Stability limiter (max players checked per team)",
        5, 25, 12,
        help="Higher = more thorough but more API calls."
    )

run_btn = st.button("Auto-build best SGP", type="primary")

# =========================================================
# EXECUTION
# =========================================================
if run_btn:
    with st.spinner("Crunching the numbers....."):
        try:
            team_a, team_b = parse_matchup(matchup)

            teams_map = get_teams_map(int(league_id), int(season_year))

            if show_debug:
                st.write("Teams map keys:", sorted(list(teams_map.keys()))[:40])

            if not teams_map:
                st.error("Could not load teams from API-Basketball. Check your key/plan/league/season.")
                st.stop()

            if team_a not in teams_map or team_b not in teams_map:
                st.error(
                    f"Unknown team abbreviation(s): '{team_a}' / '{team_b}'. "
                    f"Try like: LAL vs DAL. (Or open Debug to see valid team codes.)"
                )
                st.stop()

            # Build candidates across both teams (team-only auto)
            candidates = []
            debug_rows = []

            for abbr in (team_a, team_b):
                tid = teams_map[abbr]["team_id"]
                roster_df = get_team_players(tid, int(season_year))

                if roster_df is None or roster_df.empty:
                    continue

                roster_df = roster_df.head(int(max_players_per_team))

                for _, prow in roster_df.iterrows():
                    pid = int(prow["player_id"])
                    pname = str(prow["player"])

                    last5 = get_player_last_games(pid, int(season_year), last_n=5)

                    if len(last5) != 5:
                        if show_debug:
                            debug_rows.append({"player": pname, "team": abbr, "reason": "not 5 games"})
                        continue

                    if not minutes_gate(last5):
                        if show_debug:
                            debug_rows.append({"player": pname, "team": abbr, "reason": "minutes gate fail"})
                        continue

                    floors = build_floor_output(last5)

                    # Risk mode affects which stats get considered
                    # Safe: only REB/AST (prefer low variance)
                    # Ideal: REB/AST/PRA (PTS only if needed)
                    # Higher-risk: allow PTS earlier + allow more PRA
                    if risk_mode == "Safe":
                        floors = floors[floors["stat"].isin(["REB", "AST"])]
                    elif risk_mode == "Higher-risk":
                        # push PTS earlier by slightly improving its pref rank
                        floors = floors.copy()
                        floors.loc[floors["stat"] == "PTS", "pref"] = 2  # treat like PRA
                        floors = floors.sort_values(by=["pref", "variance", "stat"])
                    # Ideal uses default floors

                    for _, f in floors.iterrows():
                        candidates.append(
                            {
                                "player": pname,
                                "team": abbr,
                                "stat": str(f["stat"]),
                                "line": int(f["line"]),
                                "pref": int(f["pref"]),
                                "variance": int(f["variance"]),
                            }
                        )

            # Sort candidates by preference + variance
            candidates.sort(key=lambda x: (x["pref"], x["variance"], x["player"]))

            if show_debug and debug_rows:
                st.subheader("Debug: why players failed")
                st.dataframe(pd.DataFrame(debug_rows).head(50), use_container_width=True)

            if len(candidates) < 3:
                st.warning(random.choice(NO_BET_MESSAGES))
                st.stop()

            # Build final (3–5 legs)
            final = candidates[: int(legs_n)]
            safe = make_safe(final)

            st.success("✅ SGP built successfully")

            st.subheader("🔥 Final Slip")
            for p in final:
                st.write(f"{p['player']} {p['stat']} ≥ {p['line']} ({p['team']})")

            st.subheader("🛡 SAFE Slip")
            for p in safe:
                st.write(f"{p['player']} {p['stat']} ≥ {p['line']} ({p['team']})")

        except Exception as e:
            st.error("⚠️ Temporary API issue. Try again shortly.")
            st.exception(e)
