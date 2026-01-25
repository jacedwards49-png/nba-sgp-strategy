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

st.set_page_config(page_title="NBA SGP Ultimate Money Making Builder ", layout="centered")
st.title("NBA SGP Ultimate Builder (Option A)")
st.caption(
    "Team-only input • API-NBA (RapidAPI) • Last 5 only • 5/5 gate • Minutes gate • "
    "Floor lines • Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# ============================
# RAPIDAPI CONFIG
# ============================

RAPIDAPI_KEY = st.secrets.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-nba-v1.p.rapidapi.com"

if not RAPIDAPI_KEY:
    st.error("❌ Missing RAPIDAPI_KEY in Streamlit Secrets.")
    st.stop()

API_HEADERS = {
    "X-RapidAPI-Key": RAPIDAPI_KEY,
    "X-RapidAPI-Host": RAPIDAPI_HOST,
}

API_BASE = f"https://{RAPIDAPI_HOST}"

# ============================
# SEASON AUTO-DETECTION
# ============================

def current_season():
    today = datetime.today()
    year = today.year
    return year if today.month >= 10 else year - 1

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

def parse_matchup(matchup: str):
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
# API-NBA HELPERS (STABLE)
# ============================

def api_get(endpoint, params, retries=3, timeout=20):
    last_error = None
    for _ in range(retries):
        try:
            r = requests.get(
                f"{API_BASE}/{endpoint}",
                headers=API_HEADERS,
                params=params,
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_error = e
            time.sleep(1)
    raise RuntimeError("API-NBA temporarily unavailable") from last_error

@st.cache_data(ttl=86400)
def get_teams():
    data = api_get("teams", {})
    return {t["code"]: t["id"] for t in data["response"] if t["code"]}

@st.cache_data(ttl=21600)
def get_team_roster(team_id):
    data = api_get("players", {"team": team_id, "season": SEASON})
    return data["response"]

@st.cache_data(ttl=600)
def get_player_games(player_id):
    data = api_get(
        "players/statistics",
        {"id": player_id, "season": SEASON},
    )
    return data["response"]

def last5_games(player_id):
    games = get_player_games(player_id)
    if not games:
        return []

    games = sorted(games, key=lambda x: x["game"]["date"], reverse=True)

    out = []
    for g in games:
        if g["min"] is None:
            continue
        try:
            mins = int(g["min"])
        except Exception:
            continue

        pts = int(g["points"])
        reb = int(g["rebounds"]["total"])
        ast = int(g["assists"])

        out.append({
            "min": mins,
            "pts": pts,
            "reb": reb,
            "ast": ast,
            "pra": pts + reb + ast,
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
# EXECUTION
# ============================

if run_btn:
    with st.spinner("Crunching the numbers....."):
        try:
            team_a, team_b = parse_matchup(matchup)
            team_map = get_teams()

            if team_a not in team_map or team_b not in team_map:
                st.error("Unknown team abbreviation. Example: LAL vs DAL")
                st.stop()

            candidates = []

            for team_code in (team_a, team_b):
                roster = get_team_roster(team_map[team_code])

                for p in roster:
                    player_id = p["id"]
                    player_name = f'{p["firstname"]} {p["lastname"]}'

                    last5 = last5_games(player_id)

                    if len(last5) != 5:
                        continue
                    if not minutes_gate(last5):
                        continue

                    floors = build_floor_output(last5)
                    for _, f in floors.iterrows():
                        candidates.append({
                            "player": player_name,
                            "team": team_code,
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
                st.write(f'{p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]})')

            st.subheader("🛡 SAFE Slip")
            for p in safe:
                st.write(f'{p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]})')

        except Exception as e:
            st.error("⚠️ Temporary API issue. Try again shortly.")
            st.exception(e)
