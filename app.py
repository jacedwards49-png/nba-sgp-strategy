import math
import random
import time
import requests
import pandas as pd
import streamlit as st
from datetime import datetime

# ============================
# STREAMLIT SETUP
# ============================

st.set_page_config(page_title="NBA SGP Ultimate Builder (Option A)", layout="centered")
st.title("NBA SGP Ultimate Builder (Option A)")
st.caption(
    "Team-only input • API-SPORTS Basketball/NBA • Last 5 only • 5/5 gate • Minutes gate • Floor lines • "
    "Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# ============================
# AUTO-SEASON (NBA) DISPLAY
# ============================

def current_nba_season_label() -> str:
    today = datetime.today()
    year = today.year
    if today.month >= 10:
        return f"{year}-{str(year + 1)[-2:]}"
    return f"{year - 1}-{str(year)[-2:]}"

def current_season_year_int() -> int:
    # Many APIs use a single year like 2025 for the 2025-26 season
    today = datetime.today()
    return today.year if today.month >= 10 else today.year - 1

SEASON_LABEL = current_nba_season_label()
SEASON_YEAR = current_season_year_int()

# ============================
# LOCKED MODEL RULES (OPTION A)
# ============================

VARIANCE_RANK = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}
PREF_ORDER = ["REB", "AST", "PRA", "PTS"]  # PTS last resort

NO_BET_MESSAGES = [
    "❌ No bets here home boy, move to next matchup",
    "🧊 Cold game — zero edge",
    "🚫 Nothing clean here, pass it",
    "📉 Variance too high — bankroll protection engaged",
    "😴 This matchup ain't it",
]

def parse_matchup(matchup: str):
    parts = matchup.upper().replace("@", "VS").replace("VS.", "VS").split("VS")
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) != 2:
        raise ValueError("Matchup must look like: LAL vs DAL")
    return parts[0], parts[1]

def minutes_gate(last5: list[dict]) -> bool:
    mins = [g["min"] for g in last5]
    if len(mins) != 5:
        return False
    all_28_plus = all(m >= 28 for m in mins)
    over_30_count = sum(1 for m in mins if m > 30)
    return all_28_plus or (over_30_count >= 4)

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
        rows.append({
            "stat": stat,
            "line": floor_line(arr),
            "pref": PREF_ORDER.index(stat),
            "variance": VARIANCE_RANK[stat],
        })
    return pd.DataFrame(rows).sort_values(by=["pref", "variance", "stat"])

def safe_remove_highest_variance(chosen: list[dict]) -> list[dict]:
    # SAFE slip = remove the highest variance leg; tie-breaker remove PTS first
    if len(chosen) <= 3:
        return chosen

    def key(x):
        pts_bonus = 1 if x["stat"] == "PTS" else 0
        return (x["variance"], pts_bonus)

    worst = max(chosen, key=key)
    return [x for x in chosen if x is not worst]

# ============================
# API-SPORTS CONFIG (API-FOOTBALL DASHBOARD KEYS)
# ============================

API_KEY = st.secrets.get("APISPORTS_KEY", "")

# This is the most common API-SPORTS Basketball base.
# If your account uses a different host, change it here:
API_BASE = "https://v1.basketball.api-sports.io"

# NBA league id differs by provider.
# We expose it in the UI so you can adjust without code changes.
DEFAULT_NBA_LEAGUE_ID = 12

HEADERS = {
    "x-apisports-key": API_KEY,
}

def api_get(path: str, params: dict, retries: int = 3, timeout: int = 25) -> dict:
    """
    Safe API wrapper:
    - retries on timeouts/5xx
    - raises RuntimeError with useful context
    """
    url = f"{API_BASE}/{path.lstrip('/')}"
    last_err = None

    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
            # Some API-Sports errors still return 200 with {"errors":...}
            if r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} server error", response=r)

            r.raise_for_status()
            j = r.json()

            # Common API-Sports shape: {"response":[...], "errors":{...}}
            if isinstance(j, dict) and j.get("errors"):
                raise RuntimeError(f"API returned errors: {j.get('errors')}")

            return j

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as e:
            last_err = e
            time.sleep(1.2 * attempt)
            continue

        except requests.exceptions.RequestException as e:
            last_err = e
            # no point retrying some 4xx; still allow one retry for flaky gateways
            if attempt < retries and getattr(e.response, "status_code", 0) in (429, 502, 503, 504):
                time.sleep(1.2 * attempt)
                continue
            break

        except Exception as e:
            last_err = e
            break

    raise RuntimeError(f"API temporarily unavailable calling {url} with params={params}") from last_err


# ============================
# API-SPORTS DATA HELPERS
# ============================

def get_teams_map(league_id: int, season_year: int) -> dict:
    """
    Returns: { "LAL": {"id":123, "name":"Lakers"}, ... }
    NOTE: API fields can vary by product — this is why we keep parsing defensive.
    """
    j = api_get("teams", {"league": league_id, "season": season_year})
    resp = j.get("response", []) if isinstance(j, dict) else []
    out = {}

    for item in resp:
        team = item.get("team", item) if isinstance(item, dict) else {}
        abbr = (team.get("code") or team.get("abbreviation") or team.get("shortName") or "").upper()
        tid = team.get("id")
        name = team.get("name", "")

        if abbr and tid:
            out[abbr] = {"id": int(tid), "name": str(name)}

    return out

def get_team_players(team_id: int, season_year: int) -> list[dict]:
    """
    Returns a list of {"id":..., "name":...}
    """
    j = api_get("players", {"team": team_id, "season": season_year})
    resp = j.get("response", []) if isinstance(j, dict) else []
    players = []

    for item in resp:
        p = item.get("player", item) if isinstance(item, dict) else {}
        pid = p.get("id")
        name = p.get("name") or f"{p.get('firstname','')} {p.get('lastname','')}".strip()
        if pid and name:
            players.append({"id": int(pid), "name": str(name)})

    return players

def get_player_game_logs_last5(player_id: int, season_year: int) -> list[dict]:
    """
    We attempt to fetch per-game stats and take the most recent 5.
    API-SPORTS products differ; common endpoint is "players/statistics".
    If your product uses a different path, change ONLY the path below.
    """
    j = api_get("players/statistics", {"player": player_id, "season": season_year})
    resp = j.get("response", []) if isinstance(j, dict) else []

    # Each item often represents a game line. We'll defensively parse.
    rows = []
    for item in resp:
        # try common nested shapes
        stats = item.get("statistics", item.get("stats", item)) if isinstance(item, dict) else {}
        game = item.get("game", {}) if isinstance(item, dict) else {}

        # date ordering
        gdate = game.get("date") or item.get("date") or ""

        def _to_int(x):
            try:
                return int(float(x))
            except Exception:
                return None

        mins = _to_int(stats.get("minutes") or stats.get("min") or stats.get("MIN"))
        pts = _to_int(stats.get("points") or stats.get("pts") or stats.get("PTS"))
        reb = _to_int(stats.get("rebounds") or stats.get("reb") or stats.get("REB"))
        ast = _to_int(stats.get("assists") or stats.get("ast") or stats.get("AST"))

        if mins is None or pts is None or reb is None or ast is None:
            continue

        rows.append({
            "date": str(gdate),
            "min": mins,
            "pts": pts,
            "reb": reb,
            "ast": ast,
            "pra": pts + reb + ast,
        })

    # Sort by date desc if available; otherwise keep order
    if rows and rows[0].get("date"):
        rows.sort(key=lambda x: x["date"], reverse=True)

    return rows[:5]


# ============================
# UI
# ============================

with st.sidebar:
    st.subheader("API Settings")
    league_id = st.number_input("NBA league id", min_value=1, value=DEFAULT_NBA_LEAGUE_ID, step=1)
    season_year = st.number_input("Season year", min_value=2000, value=int(SEASON_YEAR), step=1)
    max_players_per_team = st.slider(
        "Stability limiter (max players checked per team)",
        5, 25, 12,
        help="Higher = more thorough but more API calls (slower / more rate-limit risk)."
    )
    show_debug = st.toggle("Show debug (API error details)", value=False)

st.caption(f"Auto season detected: **{SEASON_LABEL}** (year param: **{SEASON_YEAR}**)")

matchup = st.text_input("Matchup (team acronyms)", value="LAL vs DAL")
risk_mode = st.radio(
    "Pick style",
    ["Safe", "Ideal", "Higher-risk"],
    horizontal=True,
    help=(
        "Safe: remove highest-variance leg\n"
        "Ideal: standard final slip\n"
        "Higher-risk: allow more PTS exposure + prefer 5 legs when possible"
    ),
)

legs_default = 4 if risk_mode != "Higher-risk" else 5
legs_n = st.slider("Final slip legs", 3, 5, legs_default)

run_btn = st.button("Auto-build best SGP", type="primary")


# ============================
# EXECUTION (ONLY RUN ON CLICK)
# ============================

if run_btn:
    if not API_KEY:
        st.error("Missing APISPORTS_KEY in Streamlit Secrets.")
        st.stop()

    with st.spinner("Crunching the numbers....."):
        try:
            team_a, team_b = parse_matchup(matchup)

            # 1) Team map
            teams_map = get_teams_map(int(league_id), int(season_year))
            if not teams_map:
                st.error("Could not load teams from the API. Check league id / season year / API base URL.")
                st.stop()

            if team_a not in teams_map or team_b not in teams_map:
                st.error(
                    f"Unknown team abbreviation(s): '{team_a}' / '{team_b}'. "
                    f"Make sure your API returns team codes like LAL, DAL, etc."
                )
                st.stop()

            # 2) Build candidate pool from both rosters
            candidates = []

            for team in (team_a, team_b):
                tid = teams_map[team]["id"]
                roster = get_team_players(tid, int(season_year))
                if not roster:
                    continue

                roster = roster[: int(max_players_per_team)]

                for p in roster:
                    last5 = get_player_game_logs_last5(int(p["id"]), int(season_year))

                    # gates
                    if len(last5) != 5:
                        continue
                    if not minutes_gate(last5):
                        continue

                    floors = build_floor_output(last5)

                    # Higher-risk: allow PTS earlier; otherwise keep PTS last (already last)
                    if risk_mode == "Higher-risk":
                        # Slightly bump PRA/PTS availability by not changing order,
                        # but we’ll allow full 5 legs and not remove variance.
                        pass

                    for _, f in floors.iterrows():
                        candidates.append({
                            "player": p["name"],
                            "team": team,
                            "stat": str(f["stat"]),
                            "line": int(f["line"]),
                            "pref": int(f["pref"]),
                            "variance": int(f["variance"]),
                        })

            # 3) Sort candidates using your preference rules
            # Prefer REB/AST then PRA then PTS, and lower variance
            candidates.sort(key=lambda x: (x["pref"], x["variance"], x["player"]))

            # 4) Build slip with max 1 opposing player
            if len(candidates) < 3:
                st.warning(random.choice(NO_BET_MESSAGES))
                st.stop()

            main_team = team_a  # default
            # infer main team by which side produced more eligible candidates
            count_a = sum(1 for c in candidates if c["team"] == team_a)
            count_b = sum(1 for c in candidates if c["team"] == team_b)
            if count_b > count_a:
                main_team = team_b
            opp_team = team_b if main_team == team_a else team_a

            chosen = []
            used_opp = False
            used_player_stat = set()

            for c in candidates:
                if len(chosen) >= int(legs_n):
                    break

                if c["team"] == opp_team:
                    if used_opp:
                        continue
                    used_opp = True

                key = (c["player"], c["stat"])
                if key in used_player_stat:
                    continue
                used_player_stat.add(key)

                # If not Higher-risk, only allow PTS as last resort naturally via sorting.
                chosen.append(c)

            if len(chosen) < 3:
                st.warning(random.choice(NO_BET_MESSAGES))
                st.stop()

            # 5) SAFE / IDEAL / HIGHER-RISK output
            final = chosen
            if risk_mode == "Safe":
                safe = safe_remove_highest_variance(final)
            else:
                safe = safe_remove_highest_variance(final)  # still show SAFE for reference

            if risk_mode == "Higher-risk" and len(final) < 5:
                # if we couldn't reach 5, that's ok — still return what we have
                pass

            st.success("✅ SGP built successfully")

            st.subheader("🔥 Final Slip")
            for p in final:
                st.write(f"{p['player']} {p['stat']} ≥ {p['line']} ({p['team']})")

            st.subheader("🛡 SAFE Slip")
            for p in safe:
                st.write(f"{p['player']} {p['stat']} ≥ {p['line']} ({p['team']})")

        except Exception as e:
            st.warning("⚠️ Temporary API issue. Try again shortly.")
            if show_debug:
                st.exception(e)
            else:
                st.info("Turn on **Show debug** in the sidebar to see the exact API error details.")
