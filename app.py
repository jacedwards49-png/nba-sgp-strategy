import math
import random
import time
import requests
import pandas as pd
import streamlit as st
st.cache_data.clear()
from datetime import datetime

# ============================================================
# STREAMLIT SETUP
# ============================================================

st.set_page_config(page_title="NBA SGP Ultimate Builder (Option A)", layout="centered")
st.title("NBA SGP Ultimate Builder (Option A)")
st.caption(
    "Team dropdown • API-Sports NBA • Last 5 only • 5/5 gate • Minutes gate • "
    "Floor lines • Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# ============================================================
# API CONFIG
# ============================================================

API_KEY = st.secrets.get("API_SPORTS_KEY")
if not API_KEY:
    st.error("Missing API_SPORTS_KEY in Streamlit Secrets.")
    st.stop()

API_BASES = [
    "https://v1.basketball.api-sports.io",
    "https://v2.nba.api-sports.io",
]

HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}

LEAGUE_ID = 12  # NBA on API-Sports

# ============================================================
# SEASON RESOLUTION (AUTO + SAFE)
# ============================================================

def current_nba_season_start_year() -> int:
    today = datetime.today()
    return today.year if today.month >= 10 else today.year - 1

@st.cache_data(ttl=86400)
def resolve_api_sports_season() -> int:
    current = current_nba_season_start_year()
    try:
        for base in API_BASES:
            r = requests.get(
                f"{base}/seasons",
                headers=HEADERS,
                params={"league": LEAGUE_ID},
                timeout=20,
            )
            if r.status_code == 200:
                seasons = [int(s) for s in r.json().get("response", [])]
                valid = [s for s in seasons if s <= current]
                if valid:
                    return max(valid)
    except Exception:
        pass
    return current - 1  # safe fallback

API_SEASON_YEAR = resolve_api_sports_season()
st.caption(
    f"NBA season: **{current_nba_season_start_year()}–{str(current_nba_season_start_year()+1)[-2:]}** "
    f"(API data: **{API_SEASON_YEAR}**)"
)

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

def minutes_gate(last5):
    mins = [g["min"] for g in last5]
    return len(mins) == 5 and (all(m >= 28 for m in mins) or sum(m > 30 for m in mins) >= 4)

def floor_line(values):
    return int(math.floor(min(values) * 0.90)) if values else 0

def make_safe(chosen):
    if len(chosen) <= 3:
        return chosen
    worst = max(chosen, key=lambda x: (x["variance"], x["stat"] == "PTS"))
    return [x for x in chosen if x is not worst]

# ============================================================
# API HELPERS (RETRY-SAFE)
# ============================================================

def api_get(path, params, retries=3):
    last_error = None
    for base in API_BASES:
        url = f"{base}/{path.lstrip('/')}"
        for _ in range(retries):
            try:
                r = requests.get(url, headers=HEADERS, params=params, timeout=25)
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(1.5)
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_error = e
    raise RuntimeError("API-Sports request failed") from last_error

# ============================================================
# TEAM DROPDOWN DATA
# ============================================================

@st.cache_data(ttl=86400)
def get_team_display_list():
    j = api_get("teams", {"league": LEAGUE_ID})
    out = []
    for t in j.get("response", []):
        team = t.get("team", t)
        if team.get("code") and team.get("id"):
            out.append({
                "code": team["code"].upper(),
                "team_id": int(team["id"]),
                "name": team["name"],
                "logo": team.get("logo"),
                "label": f'{team["name"]} ({team["code"].upper()})',
            })
    return sorted(out, key=lambda x: x["name"])

teams = get_team_display_list()
if not teams:
    st.error("Teams unavailable from API-Sports.")
    st.stop()

team_lookup = {t["label"]: t for t in teams}
labels = list(team_lookup.keys())

# ============================================================
# UI — TEAM DROPDOWNS
# ============================================================

colA, colB, colC = st.columns([5, 1, 5])

with colA:
    team_a_label = st.selectbox("Team A", labels, index=0)
with colB:
    st.markdown("<br><h3 style='text-align:center'>vs</h3>", unsafe_allow_html=True)
with colC:
    team_b_label = st.selectbox("Team B", labels, index=1)

team_a = team_lookup[team_a_label]
team_b = team_lookup[team_b_label]

logo1, logo2 = st.columns(2)
with logo1:
    st.image(team_a["logo"], width=80)
with logo2:
    st.image(team_b["logo"], width=80)

# ============================================================
# CONTROLS
# ============================================================

col1, col2, col3 = st.columns(3)
with col1:
    legs_n = st.slider("Ideal legs (3–5)", 3, 5, 4)
with col2:
    risk_mode = st.selectbox("Mode", ["Safe", "Ideal", "Higher-risk"], index=1)
with col3:
    show_debug = st.toggle("Show debug", False)

run_btn = st.button("Auto-build best SGP", type="primary")

# ============================================================
# DATA PULL HELPERS
# ============================================================

def parse_minutes(val):
    if not val:
        return 0
    s = str(val)
    return int(float(s.split(":")[0])) if ":" in s else int(float(s))

@st.cache_data(ttl=1800)
def get_last_games(team_id):
    j = api_get("games", {"league": LEAGUE_ID, "season": API_SEASON_YEAR, "team": team_id})
    games = j.get("response", [])
    games = sorted(games, key=lambda g: g.get("date", ""), reverse=True)
    return games[:5]

@st.cache_data(ttl=1800)
def get_game_stats(game_id, team_id):
    j = api_get("players/statistics", {"game": game_id, "team": team_id})
    return j.get("response", [])

# ============================================================
# EXECUTION
# ============================================================

if run_btn:
    with st.spinner("Crunching the numbers....."):
        try:
            candidates = []

            for team in (team_a, team_b):
                games = get_last_games(team["team_id"])
                player_logs = {}

                for g in games:
                    gid = g.get("id")
                    if not gid:
                        continue
                    rows = get_game_stats(gid, team["team_id"])
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
                        player_logs.setdefault(pid, {"name": name, "team": team["code"], "games": []})
                        player_logs[pid]["games"].append(log)

                for info in player_logs.values():
                    last5 = info["games"][:5]
                    if len(last5) != 5 or not minutes_gate(last5):
                        continue

                    for stat in PREF_ORDER:
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
                st.write(f'• {p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]})')

            st.subheader("🛡 SAFE Slip")
            for p in safe:
                st.write(f'• {p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]})')

            if show_debug:
                st.dataframe(pd.DataFrame(candidates).head(50), use_container_width=True)

        except Exception as e:
            st.warning("⚠️ Temporary API issue. Try again shortly.")
            if show_debug:
                st.exception(e)
