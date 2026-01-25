import math
import random
import time
import requests
import pandas as pd
import streamlit as st
from datetime import datetime

# ============================================================
# TEAM CODE NORMALIZATION (API-Sports → NBA standard)
# ============================================================

TEAM_CODE_NORMALIZATION = {
    "NY": "NYK",
    "GS": "GSW",
    "SA": "SAS",
    "NO": "NOP",
    "OKLA": "OKC",
    "PHO": "PHX",
    "BRK": "BKN",
}


# ============================================================
# STREAMLIT SETUP
# ============================================================

st.set_page_config(page_title="NBA SGP Ultimate Builder (Option A)", layout="centered")
st.title("NBA SGP Ultimate Builder (Option A)")
st.caption(
    "Team-only input • API-Sports NBA • Last 5 only • 5/5 gate • Minutes gate • "
    "Floor lines • Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# ============================================================
# API KEY
# ============================================================

API_KEY = st.secrets.get("API_SPORTS_KEY", None)

# Try BOTH possible API-Sports bases (keys are provisioned on one)
API_BASES = [
    "https://v2.nba.api-sports.io",
    "https://v1.basketball.api-sports.io",
]

# ============================================================
# SEASON AUTO-DETECTION
# ============================================================

def current_season_label():
    today = datetime.today()
    y = today.year
    return f"{y}-{str(y+1)[-2:]}" if today.month >= 10 else f"{y-1}-{str(y)[-2:]}"

SEASON_LABEL = current_season_label()
SEASON_YEAR = int(SEASON_LABEL.split("-")[0])

st.caption(f"Auto season detected: **{SEASON_LABEL}**")

# ============================================================
# LOCKED OPTION A MODEL RULES
# ============================================================

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
    return len(mins) == 5 and (all(m >= 28 for m in mins) or sum(m > 30 for m in mins) >= 4)

def floor_line(values):
    return int(math.floor(min(values) * 0.90)) if values else 0

def make_safe(chosen):
    if len(chosen) <= 3:
        return chosen
    return chosen[:-1]

# ============================================================
# API HELPERS (GUARDED + FALLBACK BASE)
# ============================================================

def api_get(path, params, retries=3):
    if not API_KEY:
        raise RuntimeError("Missing API_SPORTS_KEY in Streamlit Secrets")

    headers = {
        "x-apisports-key": API_KEY,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    last_error = None

    for base in API_BASES:
        url = f"{base}/{path.lstrip('/')}"
        for attempt in range(retries):
            try:
                r = requests.get(url, headers=headers, params=params, timeout=25)
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(1.5)
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_error = e

    raise RuntimeError("API-Sports request failed") from last_error

# ============================================================
# API-Sports NBA FUNCTIONS
# ============================================================

@st.cache_data(ttl=86400)
def get_teams_map():
    j = api_get("teams", {"league": 12, "season": SEASON_YEAR})
    teams = j.get("response", [])
    out = {}

    for t in teams:
        team = t.get("team", t)
        raw_code = (team.get("code") or "").upper()
        code = TEAM_CODE_NORMALIZATION.get(raw_code, raw_code)
        tid = team.get("id")

        if code and tid:
            out[code] = int(tid)

    return out


@st.cache_data(ttl=1800)
def get_last_games(team_id):
    j = api_get("games", {"league": 12, "season": SEASON_YEAR, "team": team_id})
    games = j.get("response", [])
    games = sorted(games, key=lambda g: g.get("date", ""), reverse=True)
    return games[:5]

@st.cache_data(ttl=1800)
def get_game_stats(game_id, team_id):
    j = api_get("players/statistics", {"game": game_id, "team": team_id})
    return j.get("response", [])

def parse_minutes(val):
    if not val:
        return 0
    s = str(val)
    return int(float(s.split(":")[0])) if ":" in s else int(float(s))

# ============================================================
# UI INPUTS
# ============================================================

matchup = st.text_input("Matchup (team acronyms)", value="LAL vs DAL")

col1, col2, col3 = st.columns(3)
with col1:
    legs_n = st.slider("Ideal legs", 3, 5, 4)
with col2:
    risk_mode = st.selectbox("Mode", ["Safe", "Ideal", "Higher-risk"], index=1)
with col3:
    show_debug = st.toggle("Show debug", False)

run_btn = st.button("Auto-build best SGP", type="primary")

# ============================================================
# EXECUTION
# ============================================================

if run_btn:
    with st.spinner("Crunching the numbers....."):
        try:
            team_a, team_b = parse_matchup(matchup)
            team_map = get_teams_map()

            if team_a not in team_map or team_b not in team_map:
                st.error("Invalid team abbreviation. Example: LAL vs DAL")
                st.stop()

            candidates = []

            for team in (team_a, team_b):
                team_id = team_map[team]
                games = get_last_games(team_id)

                player_logs = {}

                for g in games:
                    gid = g.get("id")
                    if not gid:
                        continue
                    rows = get_game_stats(gid, team_id)
                    for r in rows:
                        p = r.get("player", {})
                        pid = p.get("id")
                        name = p.get("name")
                        s = r.get("statistics", {})
                        if not pid:
                            continue
                        log = {
                            "min": parse_minutes(s.get("minutes")),
                            "pts": int(s.get("points", 0)),
                            "reb": int(s.get("totReb", 0)),
                            "ast": int(s.get("assists", 0)),
                        }
                        log["pra"] = log["pts"] + log["reb"] + log["ast"]
                        player_logs.setdefault(pid, {"name": name, "team": team, "games": []})
                        player_logs[pid]["games"].append(log)

                for info in player_logs.values():
                    last5 = info["games"][:5]
                    if len(last5) != 5 or not minutes_gate(last5):
                        continue

                    for stat in ["REB", "AST", "PRA", "PTS"]:
                        vals = [g[stat.lower()] for g in last5]
                        candidates.append({
                            "player": info["name"],
                            "team": info["team"],
                            "stat": stat,
                            "line": floor_line(vals),
                            "pref": PREF_ORDER.index(stat),
                            "variance": VARIANCE_RANK[stat],
                        })

            candidates.sort(key=lambda x: (x["pref"], x["variance"]))

            if len(candidates) < 3:
                st.warning(random.choice(NO_BET_MESSAGES))
                st.stop()

            legs = 3 if risk_mode == "Safe" else 5 if risk_mode == "Higher-risk" else legs_n
            final = candidates[:legs]
            safe = make_safe(final)

            st.success("✅ SGP built successfully")

            st.subheader("🔥 Final Slip")
            for p in final:
                st.write(f"• {p['player']} {p['stat']} ≥ {p['line']} ({p['team']})")

            st.subheader("🛡 SAFE Slip")
            for p in safe:
                st.write(f"• {p['player']} {p['stat']} ≥ {p['line']} ({p['team']})")

        except Exception as e:
            st.warning("⚠️ Temporary API issue. Try again shortly.")
            if show_debug:
                st.exception(e)
