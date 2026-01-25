import math
import random
import time
import requests
import pandas as pd
import streamlit as st
from datetime import datetime

# ============================================================
# STREAMLIT SETUP
# ============================================================

st.set_page_config(page_title="NBA SGP Ultimate Builder (Option A)", layout="centered")
st.title("NBA SGP Ultimate Builder (Option A)")
st.caption(
    "Team-only input • API-Sports NBA v2 • Last 5 only • 5/5 gate • Minutes gate • Floor lines • "
    "Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# ============================================================
# AUTO-SEASON (NBA-style label + API year)
# ============================================================

def current_season_label() -> str:
    today = datetime.today()
    y = today.year
    return f"{y}-{str(y+1)[-2:]}" if today.month >= 10 else f"{y-1}-{str(y)[-2:]}"

def season_year_from_label(label: str) -> int:
    # "2025-26" -> 2025
    return int(label.split("-")[0])

SEASON_LABEL = current_season_label()
DEFAULT_SEASON_YEAR = season_year_from_label(SEASON_LABEL)

st.caption(f"Auto season detected: **{SEASON_LABEL}**")

# ============================================================
# LOCKED MODEL RULES (OPTION A)
# ============================================================

# Rules locked (as confirmed):
# - last 5 games only
# - stat eligible only if 5/5 games exist
# - minutes gate: >=28 in all 5 OR >30 in 4 of 5
# - floor line: floor(min(last5) * 0.90)
# - prefer REB/AST, then PRA, avoid PTS unless needed
# - matchup players must be on the two teams
# - main team inferred by more eligible players
# - max 1 opposing player in SGP
# - 3–5 legs
# - SAFE slip removes highest-variance leg (PTS removed first on ties)

VARIANCE_RANK = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}
PREF_ORDER = ["REB", "AST", "PRA", "PTS"]  # PTS last resort

NO_BET_MESSAGES = [
    "❌ No bets here home boy, move to next matchup",
    "🧊 Cold game — zero edge",
    "🚫 Nothing clean here, pass it",
    "📉 Variance too high — bankroll protection engaged",
    "😴 This matchup ain't it",
    "🧯 Nothing but traps — skip it",
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
        rows.append(
            {
                "stat": stat,
                "min_last5": min(arr) if arr else None,
                "line": floor_line(arr),
                "pref": PREF_ORDER.index(stat),
                "variance": VARIANCE_RANK[stat],
                "eligible_5of5": (len(arr) == 5),
            }
        )
    return pd.DataFrame(rows).sort_values(by=["pref", "variance", "stat"])

def choose_main_team(players_meta: list[dict], team_a: str, team_b: str) -> str:
    counts = {team_a: 0, team_b: 0}
    first = None
    for pm in players_meta:
        if not pm["eligible"]:
            continue
        t = pm["team"]
        if t in counts:
            counts[t] += 1
            if first is None:
                first = t
    if counts[team_a] > counts[team_b]:
        return team_a
    if counts[team_b] > counts[team_a]:
        return team_b
    return first or team_a

def build_sgp(cands: list[dict], team_a: str, team_b: str, main_team: str, n_legs: int):
    n_legs = max(3, min(5, int(n_legs)))
    opp_team = team_b if main_team == team_a else team_a

    chosen = []
    used_opp_player = False

    for c in cands:
        if len(chosen) >= n_legs:
            break

        if c["team"] == opp_team:
            if used_opp_player:
                continue
            used_opp_player = True

        if any(x["player"] == c["player"] and x["stat"] == c["stat"] for x in chosen):
            continue

        chosen.append(c)

    return chosen

def make_safe(chosen: list[dict]) -> list[dict]:
    # SAFE: remove highest variance leg; tie-breaker remove PTS first
    if len(chosen) <= 3:
        return chosen

    def key(x):
        pts_bonus = 1 if x["stat"] == "PTS" else 0
        return (x["variance"], pts_bonus)

    worst = max(chosen, key=key)
    return [x for x in chosen if x is not worst]

# ============================================================
# API-SPORTS (NBA v2) CONFIG
# ============================================================

# API-Sports Basketball base (NBA docs v2 live under API-Sports umbrella)
API_BASE = "https://v1.basketball.api-sports.io"

def _get_api_key_and_headers():
    """
    Supports BOTH:
      - api-sports direct key:   st.secrets["API_SPORTS_KEY"]  -> header x-apisports-key
      - RapidAPI key (optional): st.secrets["RAPIDAPI_KEY"]    -> headers x-rapidapi-key + x-rapidapi-host
    """
    api_sports_key = st.secrets.get("API_SPORTS_KEY", None)
    rapid_key = st.secrets.get("RAPIDAPI_KEY", None)

    if api_sports_key:
        return {
            "x-apisports-key": api_sports_key,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        }

    if rapid_key:
        # If you ever use RapidAPI, set these secrets:
        # RAPIDAPI_KEY="..."
        # RAPIDAPI_HOST="v1.basketball.api-sports.io"
        rapid_host = st.secrets.get("RAPIDAPI_HOST", "v1.basketball.api-sports.io")
        return {
            "x-rapidapi-key": rapid_key,
            "x-rapidapi-host": rapid_host,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        }

    return None  # no key present

def api_get(path: str, params: dict, retries: int = 3, timeout: int = 25):
    """
    Guarded requests:
    - retries on timeout / 429 / 5xx
    - raises RuntimeError with useful context (caught in UI)
    """
    headers = _get_api_key_and_headers()
    if not headers:
        raise RuntimeError("Missing API key. Add API_SPORTS_KEY to Streamlit Secrets.")

    url = f"{API_BASE}/{path.lstrip('/')}"
    last_err = None

    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)

            # Retryable statuses
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(1.5 + attempt)
                continue

            r.raise_for_status()
            j = r.json()

            # API-Sports commonly returns {"errors":...} or {"response":...}
            # Keep it permissive but validate basics:
            if isinstance(j, dict) and ("response" in j):
                return j

            return j

        except requests.exceptions.ReadTimeout as e:
            last_err = e
            time.sleep(1.5 + attempt)
        except requests.exceptions.RequestException as e:
            last_err = e
            break

    raise RuntimeError(f"API request failed after retries calling {url} with params={params}") from last_err

# ============================================================
# API-SPORTS NBA HELPERS
# ============================================================

@st.cache_data(ttl=86400)
def get_teams_map(league_id: int, season_year: int) -> dict:
    """
    Returns mapping: "LAL" -> {"team_id":123, "name":"Los Angeles Lakers"}
    """
    j = api_get("teams", {"league": league_id, "season": season_year})
    teams = j.get("response", []) if isinstance(j, dict) else []
    out = {}

    for item in teams:
        # typical shape: {"id":..., "name":..., "code":...} or {"team":{...}}
        t = item.get("team", item)

        code = (t.get("code") or t.get("abbreviation") or "").upper()
        tid = t.get("id")
        name = t.get("name") or t.get("nickname") or t.get("city") or "Unknown"

        if code and tid:
            out[code] = {"team_id": int(tid), "name": str(name)}

    return out

@st.cache_data(ttl=1800)
def get_last_games_for_team(league_id: int, season_year: int, team_id: int, n: int = 5) -> list[dict]:
    """
    Pull recent finished games for team; take last N by date descending.
    """
    # statuses vary; API-Sports commonly uses "status=FT" for finished
    # If your account expects different, we still sort and take last N.
    j = api_get("games", {"league": league_id, "season": season_year, "team": team_id})
    games = j.get("response", []) if isinstance(j, dict) else []

    # sort by date descending
    def game_date(g):
        d = g.get("date") or (g.get("game", {}).get("date")) or ""
        return d

    games_sorted = sorted(games, key=game_date, reverse=True)

    # filter likely-finished games first (if fields exist)
    finished = []
    for g in games_sorted:
        status = (g.get("status") or {}).get("short") if isinstance(g.get("status"), dict) else g.get("status")
        # keep FT/Finished if possible, else keep all
        if status in ("FT", "FIN", "Finished", "FINAL"):
            finished.append(g)

    use = finished if len(finished) >= n else games_sorted
    return use[:n]

@st.cache_data(ttl=1800)
def get_game_player_stats(game_id: int, team_id: int) -> list[dict]:
    """
    Returns player stat lines for a team in a specific game.
    """
    # API-Sports basketball typically: /players/statistics?game=XXX&team=YYY
    j = api_get("players/statistics", {"game": game_id, "team": team_id})
    return j.get("response", []) if isinstance(j, dict) else []

def _parse_minutes(val) -> int:
    """
    minutes could be "34:12" or 34.0 or None
    """
    if val is None:
        return 0
    s = str(val).strip()
    if not s:
        return 0
    try:
        # "34:12"
        if ":" in s:
            return int(float(s.split(":")[0]))
        return int(float(s))
    except Exception:
        return 0

def build_last5_logs_for_team(league_id: int, season_year: int, team_code: str, team_id: int, max_players: int):
    """
    Uses the team’s last 5 games. For each game, fetch player stats for that team.
    Aggregates per player -> exactly 5 game lines where available.
    Returns dict: player_id -> {"name":..., "games":[{min,pts,reb,ast,pra}, ...]}
    """
    games = get_last_games_for_team(league_id, season_year, team_id, n=5)

    # If games less than 5, still proceed but model requires 5/5 so most will fail.
    player_map = {}  # pid -> {name, games:[]}

    for g in games:
        gid = g.get("id") or (g.get("game", {}).get("id"))
        if not gid:
            continue

        rows = get_game_player_stats(int(gid), team_id)

        for item in rows:
            # shape may be {"player":{id,name}, "statistics":{...}} or flattened
            p = item.get("player", {})
            pid = p.get("id") or item.get("player_id") or item.get("id")
            pname = p.get("name") or item.get("name") or item.get("player") or "Unknown"

            stats = item.get("statistics", item.get("stats", item))

            mins = _parse_minutes(stats.get("minutes") or stats.get("min") or stats.get("MIN"))
            pts = int(stats.get("points") or stats.get("pts") or stats.get("PTS") or 0)
            reb = int(stats.get("totReb") or stats.get("reb") or stats.get("REB") or 0)
            ast = int(stats.get("assists") or stats.get("ast") or stats.get("AST") or 0)

            if not pid:
                continue

            if pid not in player_map:
                player_map[pid] = {"name": str(pname), "team": team_code, "games": []}

            player_map[pid]["games"].append(
                {"min": mins, "pts": pts, "reb": reb, "ast": ast, "pra": pts + reb + ast}
            )

    # Keep only players with at least 1 game, then cap by avg minutes for stability
    players = []
    for pid, info in player_map.items():
        gms = info["games"]
        avg_min = sum(x["min"] for x in gms) / max(1, len(gms))
        players.append((pid, avg_min, info))

    players.sort(key=lambda x: x[1], reverse=True)
    players = players[: int(max_players)]

    return {pid: info for pid, _, info in players}

# ============================================================
# UI
# ============================================================

matchup = st.text_input("Matchup (team acronyms)", value="LAL vs DAL")

colA, colB, colC = st.columns(3)
with colA:
    legs_n = st.slider("Ideal slip legs (3–5)", 3, 5, 4)
with colB:
    risk_mode = st.selectbox("Mode", ["Safe", "Ideal", "Higher-risk"], index=1)
with colC:
    show_debug = st.toggle("Show debug", value=False)

league_id = 12  # NBA on API-Sports is commonly 12; keep fixed unless you want UI for it
season_year = DEFAULT_SEASON_YEAR

max_players_per_team = st.slider(
    "Stability limiter (max players checked per team)",
    5, 20, 12,
    help="Higher = more thorough but can be slower / more API calls."
)

run_btn = st.button("Auto-build best SGP", type="primary")

# ============================================================
# EXECUTION
# ============================================================

def _mode_to_legs(mode: str, ideal_legs: int) -> int:
    if mode == "Safe":
        return 3
    if mode == "Higher-risk":
        return 5
    return int(ideal_legs)

def _mode_candidate_sort_key(mode: str):
    """
    Higher-risk: allow PTS slightly earlier (still last-ish).
    Safe/Ideal: strict pref order.
    """
    if mode == "Higher-risk":
        # bump PTS a bit less punished
        def key(c):
            pts_bonus = 0 if c["stat"] == "PTS" else -1  # non-PTS slightly preferred
            return (c["pref"], c["variance"], pts_bonus, c["player"])
        return key

    def key(c):
        return (c["pref"], c["variance"], c["player"])
    return key

if run_btn:
    status_box = st.empty()
    with st.spinner("Crunching the numbers....."):
        try:
            team_a, team_b = parse_matchup(matchup)

            teams_map = get_teams_map(int(league_id), int(season_year))
            if not teams_map:
                st.warning("⚠️ Temporary API issue. Try again shortly.")
                st.stop()

            if team_a not in teams_map or team_b not in teams_map:
                st.error(f"Unknown team abbreviation(s): '{team_a}' / '{team_b}'. Example: LAL vs DAL")
                if show_debug:
                    st.write("Known codes sample:", sorted(list(teams_map.keys()))[:25])
                st.stop()

            # Build last5 logs for each team (team last 5 games)
            team_a_id = teams_map[team_a]["team_id"]
            team_b_id = teams_map[team_b]["team_id"]

            teamA_players = build_last5_logs_for_team(int(league_id), int(season_year), team_a, team_a_id, max_players_per_team)
            teamB_players = build_last5_logs_for_team(int(league_id), int(season_year), team_b, team_b_id, max_players_per_team)

            # Evaluate players against model, compute per-player picks
            players_meta = []

            def process_team_players(team_players: dict):
                for pid, info in team_players.items():
                    last5 = info["games"][:5]  # already last 5 games for team; player may have fewer
                    eligible_5of5 = len(last5) == 5
                    mins_ok = minutes_gate(last5) if eligible_5of5 else False
                    ok = eligible_5of5 and mins_ok

                    floor_df = build_floor_output(last5) if ok else None

                    picks = []
                    if ok and floor_df is not None:
                        # per-player candidates (3 is your default)
                        per_player = 3
                        eligible_df = floor_df[floor_df["eligible_5of5"] == True].copy()
                        eligible_df = eligible_df.sort_values(by=["pref", "variance", "stat"])

                        # Prefer non-PTS first; allow PTS last if needed
                        for _, r in eligible_df.iterrows():
                            if r["stat"] == "PTS":
                                continue
                            picks.append(r.to_dict())
                            if len(picks) == per_player:
                                break
                        if len(picks) < per_player:
                            for _, r in eligible_df.iterrows():
                                if r["stat"] == "PTS":
                                    picks.append(r.to_dict())
                                    if len(picks) == per_player:
                                        break

                    players_meta.append(
                        {
                            "player_id": pid,
                            "name": info["name"],
                            "team": info["team"],
                            "eligible": ok,
                            "eligible_5of5": eligible_5of5,
                            "mins_gate": mins_ok,
                            "mins": [g["min"] for g in last5],
                            "last5": last5,
                            "floor_df": floor_df,
                            "picks": picks,
                        }
                    )

            process_team_players(teamA_players)
            process_team_players(teamB_players)

            eligible_players = [p for p in players_meta if p["eligible"]]
            if not eligible_players:
                st.warning(random.choice(NO_BET_MESSAGES))
                st.stop()

            # Main team inferred
            main_team = choose_main_team(
                [{"eligible": p["eligible"], "team": p["team"]} for p in players_meta],
                team_a, team_b
            )
            opp_team = team_b if main_team == team_a else team_a

            # Flatten candidates
            cands = []
            for pm in players_meta:
                if not pm["eligible"]:
                    continue
                for p in pm["picks"]:
                    cands.append(
                        {
                            "player": pm["name"],
                            "team": pm["team"],
                            "stat": p["stat"],
                            "line": int(p["line"]),
                            "pref": int(p["pref"]),
                            "variance": int(p["variance"]),
                        }
                    )

            # Sort based on mode
            cands.sort(key=_mode_candidate_sort_key(risk_mode))

            n_legs = _mode_to_legs(risk_mode, legs_n)
            chosen = build_sgp(cands, team_a, team_b, main_team, n_legs=n_legs)

            if len(chosen) < 3:
                st.warning(random.choice(NO_BET_MESSAGES))
                st.stop()

            safe = make_safe(chosen)

            st.success("✅ SGP built successfully")
            st.markdown("### Team constraint")
            st.write(f"Main side inferred: **{main_team}** (max **1** opposing player from **{opp_team}**)")

            def fmt_leg(x):
                return f"{x['player']} {x['stat']} ≥ {x['line']} ({x['team']})"

            st.subheader("🔥 Final Slip")
            for leg in chosen:
                st.write("• " + fmt_leg(leg))

            st.subheader("🛡 SAFE Slip")
            for leg in safe:
                st.write("• " + fmt_leg(leg))

            if show_debug:
                with st.expander("Debug: eligible players + minutes", expanded=False):
                    dbg = []
                    for p in players_meta:
                        dbg.append(
                            {
                                "player": p["name"],
                                "team": p["team"],
                                "eligible": p["eligible"],
                                "eligible_5of5": p["eligible_5of5"],
                                "mins_gate": p["mins_gate"],
                                "mins_last5": p["mins"],
                            }
                        )
                    st.dataframe(pd.DataFrame(dbg), use_container_width=True)

                with st.expander("Debug: candidate pool (top 50)", expanded=False):
                    st.dataframe(pd.DataFrame(cands[:50]), use_container_width=True)

        except Exception as e:
            st.warning("⚠️ Temporary API issue. Try again shortly.")
            if show_debug:
                st.exception(e)
