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
    "NBA-only • API-Sports NBA Pro • Last 5 only • 5/5 gate • Minutes gate • "
    "Floor lines • Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# ============================================================
# API CONFIG (NBA ONLY)
# ============================================================

API_KEY = st.secrets.get("API_SPORTS_KEY")
if not API_KEY:
    st.error("Missing API_SPORTS_KEY in Streamlit Secrets.")
    st.stop()

NBA_BASE = "https://v2.nba.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json",
}

# ============================================================
# SEASON (AUTO, NEVER FUTURE)
# ============================================================

def current_season_start_year():
    today = datetime.today()
    return today.year if today.month >= 10 else today.year - 1

SEASON_YEAR = current_season_start_year()

st.caption(f"NBA season: **{SEASON_YEAR}–{str(SEASON_YEAR+1)[-2:]}**")

# ============================================================
# OPTION A MODEL RULES (LOCKED)
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

def mode_to_legs(mode, ideal):
    return 3 if mode == "Safe" else 5 if mode == "Higher-risk" else ideal

def choose_main_team(players, a, b):
    counts = {a: 0, b: 0}
    for p in players:
        counts[p["team"]] += 1
    return a if counts[a] >= counts[b] else b

# ============================================================
# API HELPERS (NBA ONLY)
# ============================================================

def nba_get(path, params, timeout=25):
    r = requests.get(
        f"{NBA_BASE}/{path}",
        headers=HEADERS,
        params=params,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json().get("response", [])

# ============================================================
# TEAM DROPDOWN (NBA-PRO SAFE)
# ============================================================

@st.cache_data(ttl=86400)
def get_teams():
    teams = nba_get("teams", {"season": SEASON_YEAR})
    return sorted(
        [{
            "code": t["code"],
            "team_id": t["id"],
            "name": t["name"],
            "logo": t["logo"],
            "label": f'{t["name"]} ({t["code"]})',
        } for t in teams],
        key=lambda x: x["name"]
    )

teams = get_teams()
if not teams:
    st.error("NBA teams unavailable. Check API key or subscription.")
    st.stop()

team_lookup = {t["label"]: t for t in teams}
labels = list(team_lookup.keys())

# ============================================================
# UI — CONTROLS
# ============================================================

col1, col2, col3 = st.columns(3)
with col1:
    legs_n = st.slider("Ideal legs (3–5)", 3, 5, 4)
with col2:
    risk_mode = st.selectbox("Mode", ["Safe", "Ideal", "Higher-risk"], index=1)
with col3:
    show_debug = st.toggle("Show debug", False)

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

run_btn = st.button("Auto-build best SGP", type="primary")

# ============================================================
# DATA HELPERS
# ============================================================

def parse_minutes(v):
    if not v:
        return 0
    return int(v.split(":")[0]) if ":" in str(v) else int(float(v))

@st.cache_data(ttl=1800)
def last_games(team_id):
    games = nba_get("games", {"team": team_id, "season": SEASON_YEAR})
    games = sorted(games, key=lambda g: g["date"], reverse=True)
    return games[:5]

@st.cache_data(ttl=1800)
def player_stats(game_id, team_id):
    return nba_get("players/statistics", {"game": game_id, "team": team_id})

# ============================================================
# EXECUTION
# ============================================================

if run_btn:
    with st.spinner("Crunching the numbers....."):
        try:
            candidates, eligible_players = [], []

            for team in (team_a, team_b):
                games = last_games(team["team_id"])
                if len(games) < 5:
                    continue

                logs = {}
                for g in games:
                    rows = player_stats(g["id"], team["team_id"])
                    for r in rows:
                        pid = r["player"]["id"]
                        stats = r["statistics"]
                        log = {
                            "min": parse_minutes(stats["minutes"]),
                            "pts": stats["points"],
                            "reb": stats["totReb"],
                            "ast": stats["assists"],
                        }
                        log["pra"] = log["pts"] + log["reb"] + log["ast"]
                        logs.setdefault(pid, {"name": r["player"]["name"], "team": team["code"], "games": []})
                        logs[pid]["games"].append(log)

                for info in logs.values():
                    last5 = info["games"][:5]
                    if len(last5) != 5 or not minutes_gate(last5):
                        continue

                    eligible_players.append({"player": info["name"], "team": info["team"]})

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

            if len(candidates) < 3:
                st.warning(random.choice(NO_BET_MESSAGES))
                st.stop()

            candidates.sort(key=lambda x: (x["pref"], x["variance"], x["player"]))

            main_team = choose_main_team(eligible_players, team_a["code"], team_b["code"])
            opp_team = team_b["code"] if main_team == team_a["code"] else team_a["code"]

            legs = mode_to_legs(risk_mode, legs_n)
            final = []
            used_opp = False

            for c in candidates:
                if len(final) >= legs:
                    break
                if c["team"] == opp_team:
                    if used_opp:
                        continue
                    used_opp = True
                if any(x["player"] == c["player"] and x["stat"] == c["stat"] for x in final):
                    continue
                final.append(c)

            safe = make_safe(final)

            st.success("✅ SGP built successfully")
            st.write(f"Main side: **{main_team}** (max 1 from {opp_team})")

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
