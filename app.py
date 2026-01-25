import math
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
    "Team-only input • NBA Stats API • Last 5 only • 5/5 gate • Minutes gate • Floor lines • "
    "Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# ============================
# NBA STATS CONFIG
# ============================

NBA_BASE = "https://stats.nba.com/stats"
NBA_HEADERS = {
    "Host": "stats.nba.com",
    "Connection": "keep-alive",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept-Language": "en-US,en;q=0.9",
}

# ============================
# SEASON AUTO-DETECTION
# ============================

def current_season() -> str:
    today = datetime.today()
    year = today.year
    # NBA season typically starts Oct
    if today.month >= 10:
        return f"{year}-{str(year + 1)[-2:]}"
    return f"{year - 1}-{str(year)[-2:]}"

SEASON = current_season()

# ============================
# MODEL RULES (LOCKED)
# ============================

VARIANCE_RANK = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}
PREF_ORDER = ["REB", "AST", "PRA", "PTS"]  # PTS last resort

def parse_matchup(matchup: str):
    parts = matchup.upper().replace("@", "VS").replace("VS.", "VS").split("VS")
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) != 2:
        raise ValueError("Matchup must look like: LAL vs DAL")
    return parts[0], parts[1]

def minutes_gate(last5: list[dict]) -> dict:
    mins = [g["min"] for g in last5]
    all_28_plus = len(mins) == 5 and all(m >= 28 for m in mins)
    over_30_count = sum(1 for m in mins if m > 30)
    passed = all_28_plus or (over_30_count >= 4)
    return {
        "mins": mins,
        "all_28_plus": all_28_plus,
        "over_30_count": over_30_count,
        "pass": passed,
    }

def floor_line(values: list[int]) -> int:
    return int(math.floor(min(values) * 0.90)) if values else 0

def stat_eligibility(last5: list[dict]) -> bool:
    return len(last5) == 5

def build_floor_output(last5: list[dict]) -> pd.DataFrame:
    vals = {
        "REB": [g["reb"] for g in last5],
        "AST": [g["ast"] for g in last5],
        "PRA": [g["pra"] for g in last5],
        "PTS": [g["pts"] for g in last5],
    }
    rows = []
    for stat, arr in vals.items():
        rows.append(
            {
                "stat": stat,
                "min_last5": min(arr) if arr else None,
                "floor_line": floor_line(arr),
                "eligible_5of5": (len(arr) == 5),
                "variance_rank": VARIANCE_RANK[stat],
                "pref_rank": PREF_ORDER.index(stat),
            }
        )
    return pd.DataFrame(rows).sort_values(by=["pref_rank", "variance_rank", "stat"])

def recommend_legs_for_player(floor_df: pd.DataFrame, per_player: int = 3) -> list[dict]:
    per_player = max(1, min(5, int(per_player)))
    eligible = floor_df[floor_df["eligible_5of5"] == True].copy()
    eligible = eligible.sort_values(by=["pref_rank", "variance_rank", "stat"])

    picks = []
    # non-PTS first
    for _, r in eligible.iterrows():
        if r["stat"] == "PTS":
            continue
        picks.append(r.to_dict())
        if len(picks) == per_player:
            return picks

    # if still short, allow PTS last
    for _, r in eligible.iterrows():
        if r["stat"] == "PTS":
            picks.append(r.to_dict())
            if len(picks) == per_player:
                break
    return picks

def choose_main_team(players_meta: list[dict], team_a: str, team_b: str) -> str:
    counts = {team_a: 0, team_b: 0}
    first = None
    for pm in players_meta:
        if not pm["eligible"]:
            continue
        t = pm["team_abbrev"]
        if t in counts:
            counts[t] += 1
            if first is None:
                first = t
    if counts[team_a] > counts[team_b]:
        return team_a
    if counts[team_b] > counts[team_a]:
        return team_b
    return first or team_a

def flatten_candidates(players_meta: list[dict]) -> list[dict]:
    cands = []
    for pm in players_meta:
        if not pm["eligible"]:
            continue
        for p in pm["picks"]:
            cands.append(
                {
                    "player": pm["name"],
                    "team": pm["team_abbrev"],
                    "stat": p["stat"],
                    "line": int(p["floor_line"]),
                    "variance_rank": int(p["variance_rank"]),
                    "pref_rank": int(p["pref_rank"]),
                }
            )
    # prefer REB/AST, then PRA, then PTS; low variance; then name
    cands.sort(key=lambda x: (x["pref_rank"], x["variance_rank"], x["player"]))
    return cands

def build_sgp(cands: list[dict], team_a: str, team_b: str, main_team: str, n_legs: int):
    n_legs = max(3, min(5, int(n_legs)))
    opp_team = team_b if main_team == team_a else team_a

    chosen = []
    used_opp_player = False

    for c in cands:
        if len(chosen) >= n_legs:
            break

        # max 1 opposing player total
        if c["team"] == opp_team:
            if used_opp_player:
                continue
            used_opp_player = True

        # no duplicate player+stat
        if any(x["player"] == c["player"] and x["stat"] == c["stat"] for x in chosen):
            continue

        chosen.append(c)

    return chosen

def make_safe(chosen: list[dict]) -> list[dict]:
    # SAFE: remove highest variance leg; tie-breaker: remove PTS first
    if len(chosen) <= 3:
        return chosen

    def key(x):
        pts_bonus = 1 if x["stat"] == "PTS" else 0
        return (x["variance_rank"], pts_bonus)

    worst = max(chosen, key=key)
    return [x for x in chosen if x is not worst]

# ============================
# NBA ENDPOINT HELPERS
# ============================

def _nba_get(endpoint: str, params: dict, timeout: int = 20):
    r = requests.get(f"{NBA_BASE}/{endpoint}", headers=NBA_HEADERS, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=86400)
def get_team_map() -> dict:
    # Use leaguedashteamstats to build ABBR -> TEAM_ID mapping
    j = _nba_get(
        "leaguedashteamstats",
        params={
            "LeagueID": "00",
            "Season": SEASON,
            "SeasonType": "Regular Season",
            "PerMode": "PerGame",
            "MeasureType": "Base",
        },
    )
    rs = j["resultSets"][0]
    df = pd.DataFrame(rs["rowSet"], columns=rs["headers"])
    # TEAM_ABBREVIATION, TEAM_ID, TEAM_NAME
    team_map = {}
    for _, row in df.iterrows():
        abbr = str(row["TEAM_ABBREVIATION"]).upper()
        team_map[abbr] = {
            "team_id": int(row["TEAM_ID"]),
            "team_name": str(row["TEAM_NAME"]),
        }
    return team_map

@st.cache_data(ttl=21600)
def get_team_roster(team_id: int) -> pd.DataFrame:
    j = _nba_get(
        "commonteamroster",
        params={"TeamID": team_id, "Season": SEASON},
    )
    # resultSets[0] typically contains roster
    rs = j["resultSets"][0]
    df = pd.DataFrame(rs["rowSet"], columns=rs["headers"])
    return df

@st.cache_data(ttl=600)
def get_player_gamelog(player_id: int) -> pd.DataFrame:
    j = _nba_get(
        "playergamelog",
        params={"PlayerID": player_id, "Season": SEASON, "SeasonType": "Regular Season"},
    )
    rs = j["resultSets"][0]
    df = pd.DataFrame(rs["rowSet"], columns=rs["headers"])
    return df

def last5_games(player_id: int) -> list[dict]:
    df = get_player_gamelog(player_id)
    if df.empty:
        return []
    # ensure sorted newest -> oldest
    df = df.sort_values("GAME_DATE", ascending=False)

    out = []
    for _, row in df.iterrows():
        # MIN is numeric in NBA stats
        try:
            mins = int(float(row["MIN"])) if row["MIN"] is not None else 0
        except Exception:
            continue

        pts = int(row["PTS"])
        reb = int(row["REB"])
        ast = int(row["AST"])
        out.append(
            {
                "date": str(row["GAME_DATE"]),
                "min": mins,
                "pts": pts,
                "reb": reb,
                "ast": ast,
                "pra": pts + reb + ast,
            }
        )
        if len(out) == 5:
            break
    return out

# ============================
# UI
# ============================

matchup = st.text_input("Matchup (team acronyms)", value="LAL vs DAL")
col1, col2, col3 = st.columns(3)
with col1:
    legs_n = st.number_input("Final slip legs (3–5)", min_value=3, max_value=5, value=4, step=1)
with col2:
    per_player = st.number_input("Candidate legs per player", min_value=1, max_value=5, value=3, step=1)
with col3:
    show_debug = st.toggle("Show debug tables", value=False)

st.caption(f"Auto season: **{SEASON}**")

run_btn = st.button("Auto-build best SGP",_
