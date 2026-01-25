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

def current_season():
    today = datetime.today()
    year = today.year
    return f"{year}-{str(year + 1)[-2:]}" if today.month >= 10 else f"{year - 1}-{str(year)[-2:]}"

SEASON = current_season()

# ============================
# LOCKED MODEL RULES (OPTION A)
# ============================

VARIANCE_RANK = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}
PREF_ORDER = ["REB", "AST", "PRA", "PTS"]

NO_BET_MESSAGES = [
    "❌ No bets here home boy, move to next matchup",
    "🧊 Cold game — zero edge",
    "🚫 Nothing clean here, pass it",
    "📉 Variance too high — bankroll protection engaged",
    "😴 This matchup ain't it",
]

def parse_matchup(matchup):
    parts = matchup.upper().replace("@", "VS").replace("VS.", "VS").split("VS")
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) != 2:
        raise ValueError("Matchup must look like: LAL vs DAL")
    return parts[0], parts[1]

def minutes_gate(last5):
    mins = [g["min"] for g in last5]
    return (
        len(mins) == 5
        and (all(m >= 28 for m in mins) or sum(1 for m in mins if m > 30) >= 4)
    )

def floor_line(values):
    return int(math.floor(min(values) * 0.90)) if values else 0

def build_floor_output(last5):
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
    return pd.DataFrame(rows).sort_values(by=["pref", "variance"])

def make_safe(chosen):
    return chosen if len(chosen) <= 3 else chosen[:-1]

# ============================
# 🔒 NBA API (RETRY-SAFE)
# ============================

def _nba_get(endpoint, params, retries=3, timeout=30):
    last_error = None

    for attempt in range(retries):
        try:
            r = requests.get(
                f"{NBA_BASE}/{endpoint}",
                headers=NBA_HEADERS,
                params=params,
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()

        except requests.exceptions.ReadTimeout as e:
            last_error = e
            time.sleep(1.5)

        except requests.exceptions.RequestException as e:
            last_error = e
            break

    raise RuntimeError("NBA Stats API temporarily unavailable") from last_error

@st.cache_data(ttl=86400)
def get_team_map():
    try:
        j = _nba_get(
            "leaguedashteamstats",
            {
                "LeagueID": "00",
                "Season": SEASON,
                "SeasonType": "Regular Season",
                "PerMode": "PerGame",
                "MeasureType": "Base",
            },
        )
    except Exception:
        return {}

    rs = j["resultSets"][0]
    df = pd.DataFrame(rs["rowSet"], columns=rs["headers"])
    return {row["TEAM_ABBREVIATION"]: int(row["TEAM_ID"]) for _, row in df.iterrows()}

@st.cache_data(ttl=21600)
def get_team_roster(team_id):
    j = _nba_get("commonteamroster", {"TeamID": team_id, "Season": SEASON})
    rs = j["resultSets"][0]
    return pd.DataFrame(rs["rowSet"], columns=rs["headers"])

@st.cache_data(ttl=600)
def get_player_gamelog(player_id):
    j = _nba_get(
        "playergamelog",
        {"PlayerID": player_id, "Season": SEASON, "SeasonType": "Regular Season"},
    )
    rs = j["resultSets"][0]
    return pd.DataFrame(rs["rowSet"], columns=rs["headers"])

def last5_games(player_id):
    df = get_player_gamelog(player_id).sort_values("GAME_DATE", ascending=False)
    out = []
    for _, r in df.iterrows():
        out.append({
            "min": int(r["MIN"]),
            "pts": int(r["PTS"]),
            "reb": int(r["REB"]),
            "ast": int(r["AST"]),
            "pra": int(r["PTS"]) + int(r["REB"]) + int(r["AST"]),
        })
        if len(out) == 5:
            break
    return out

# ============================
# UI
# ============================

matchup = st.text_input("Matchup (team acronyms)", value="LAL vs DAL")
legs_n = st.slider("Final slip legs", 3, 5, 4)
st.caption(f"Auto season detected: **{SEASON}**")

run_btn = st.button("Auto-build best SGP", type="primary")

# ============================
# 🚀 EXECUTION BLOCK
# ============================

if run_btn:
    with st.spinner("Crunching the numbers....."):
        team_a, team_b = parse_matchup(matchup)
        team_map = get_team_map()

        if not team_map:
            st.error("🚫 NBA Stats API is temporarily unavailable. Please try again shortly.")
            st.stop()

        candidates = []

        for team in (team_a, team_b):
            roster = get_team_roster(team_map[team])

            for _, row in roster.iterrows():
                last5 = last5_games(row["PLAYER_ID"])

                if len(last5) != 5 or not minutes_gate(last5):
                    continue

                floors = build_floor_output(last5)
                for _, f in floors.iterrows():
                    candidates.append({
                        "player": row["PLAYER"],
                        "team": team,
                        "stat": f["stat"],
                        "line": f["line"],
                        "pref": f["pref"],
                        "variance": f["variance"],
                    })

        candidates.sort(key=lambda x: (x["pref"], x["variance"]))

        if len(candidates) < 3:
            st.warning(random.choice(NO_BET_MESSAGES))
            st.stop()

        final = candidates[:legs_n]
        safe = make_safe(final)

        st.success("✅ SGP built successfully")

        st.subheader("🔥 Final Slip")
        for p in final:
            st.write(f"{p['player']} {p['stat']} ≥ {p['line']} ({p['team']})")

        st.subheader("🛡 SAFE Slip")
        for p in safe:
            st.write(f"{p['player']} {p['stat']} ≥ {p['line']} ({p['team']})")
