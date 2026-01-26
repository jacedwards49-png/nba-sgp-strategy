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

st.set_page_config(page_title="MMMBets NBA SGP Generator", layout="centered")
st.title("MMMBets NBA SGP Generator")
st.caption(
    "Team dropdown • API-Sports NBA v2 • Last 5 only • 5/5 gate • Minutes gate • "
    "Floor lines • Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# ============================================================
# API CONFIG
# ============================================================

API_KEY = st.secrets.get("API_SPORTS_KEY")
if not API_KEY:
    st.error("Missing API_SPORTS_KEY in Streamlit Secrets.")
    st.stop()

BASE_URL = "https://v2.nba.api-sports.io"
NBA_LEAGUE = "standard"

HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}

NO_BET_MESSAGES = [
    "❌ No bets here home boy, move to next matchup",
    "🧊 Cold game — zero edge",
    "🚫 Nothing clean here, pass it",
    "📉 Variance too high — bankroll protection engaged",
    "😴 This matchup ain't it",
    "🧯 Nothing but traps — skip it",
]

# ============================================================
# MODEL RULES (OPTION A)
# ============================================================

VARIANCE_RANK = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}
PREF_ORDER = ["REB", "AST", "PRA", "PTS"]

def minutes_gate(last5):
    mins = [g["min"] for g in last5]
    return len(mins) == 5 and (all(m >= 28 for m in mins) or sum(m > 30 for m in mins) >= 4)

def near_miss_score(last5, stat, floor):
    vals = [g[stat.lower()] for g in last5]
    mins = [g["min"] for g in last5]

    score = 0
    if sum(v >= floor for v in vals) == 4:
        score += 1
    if not minutes_gate(last5):
        score += 1
    if min(vals) == floor - 1:
        score += 1
    score += VARIANCE_RANK[stat] - 1
    return score

def make_safe(chosen):
    if len(chosen) <= 3:
        return chosen
    worst = max(chosen, key=lambda x: (x["variance"], x["stat"] == "PTS"))
    return [x for x in chosen if x is not worst]

def mode_to_legs(mode, ideal):
    if mode == "Safe":
        return 2
    if mode == "Higher-risk":
        return 5
    return int(ideal)

def choose_main_team(players, a, b):
    counts = {a: 0, b: 0}
    for p in players:
        if p["team"] in counts:
            counts[p["team"]] += 1
    return a if counts[a] >= counts[b] else b

def build_sgp_with_constraints(cands, a, b, main_team, n_legs):
    opp = b if main_team == a else a
    chosen = []
    used_opp = False

    for c in cands:
        if len(chosen) >= n_legs:
            break
        if c["team"] == opp:
            if used_opp:
                continue
            used_opp = True
        if any(x["player"] == c["player"] and x["stat"] == c["stat"] for x in chosen):
            continue
        chosen.append(c)

    return chosen

# ============================================================
# NBA SEASON
# ============================================================

def current_season():
    today = datetime.today()
    return today.year if today.month >= 10 else today.year - 1

SEASON = current_season()
st.caption(f"NBA season: **{SEASON}–{str(SEASON+1)[-2:]}**")

# ============================================================
# API HELPERS
# ============================================================

def api_get(path, params=None):
    r = requests.get(f"{BASE_URL}/{path}", headers=HEADERS, params=params or {}, timeout=25)
    r.raise_for_status()
    return r.json().get("response", [])

@st.cache_data(ttl=86400)
def get_teams():
    return [
        {
            "team_id": t["id"],
            "name": t["name"],
            "code": t["code"],
            "logo": t["logo"],
            "label": f'{t["name"]} ({t["code"]})'
        }
        for t in api_get("teams")
        if t.get("code") and len(t["code"]) == 3
    ]

@st.cache_data(ttl=1800)
def get_games(team_id):
    games = api_get("games", {"league": NBA_LEAGUE, "season": SEASON, "team": team_id})
    games = sorted(games, key=lambda g: g["date"]["start"], reverse=True)
    return games[:5]

@st.cache_data(ttl=1800)
def get_stats(game_id, team_id):
    return api_get("players/statistics", {"season": SEASON, "game": game_id, "team": team_id})

def parse_minutes(v):
    return int(str(v).split(":")[0]) if v else 0

# ============================================================
# UI — CONTROLS
# ============================================================

c1, c2, c3 = st.columns(3)
with c1:
    legs_n = st.slider("Ideal legs (2–5)", 2, 5, 3)
with c2:
    risk_mode = st.selectbox("Mode", ["Safe", "Ideal", "Higher-risk"])
with c3:
    show_debug = st.toggle("Show debug", False)

allow_two_leg = st.checkbox(
    "Allow 2-leg parlay (higher confidence)",
    value=False,
    help="Allows 2-leg SGPs when edge is limited"
)

teams = get_teams()
lookup = {t["label"]: t for t in teams}

a, _, b = st.columns([5,1,5])
with a:
    team_a = lookup[st.selectbox("Team A", lookup.keys())]
with b:
    team_b = lookup[st.selectbox("Team B", lookup.keys())]

run_btn = st.button("Auto-build best SGP", type="primary")

# ============================================================
# EXECUTION
# ============================================================

if run_btn:
    with st.spinner("Crunching the numbers..."):

        candidates, near_miss, eligible = [], [], []
        min_legs = 2 if allow_two_leg else 3

        for team in (team_a, team_b):
            games = get_games(team["team_id"])
            logs = {}

            for g in games:
                for r in get_stats(g["id"], team["team_id"]):
                    p = r["player"]
                    s = r["statistics"]
                    pid = p["id"]

                    logs.setdefault(pid, {
                        "name": p["name"],
                        "team": team["code"],
                        "games": []
                    })

                    logs[pid]["games"].append({
                        "min": parse_minutes(s.get("minutes")),
                        "pts": s.get("points", 0),
                        "reb": s.get("totReb", 0),
                        "ast": s.get("assists", 0),
                        "pra": s.get("points", 0) + s.get("totReb", 0) + s.get("assists", 0)
                    })

            for info in logs.values():
                last5 = info["games"]
                if len(last5) != 5:
                    continue

                for stat in PREF_ORDER:
                    vals = [g[stat.lower()] for g in last5]
                    floor = int(min(vals) * 0.9)
                    if floor <= 0:
                        continue

                    if minutes_gate(last5):
                        eligible.append({"player": info["name"], "team": info["team"]})
                        candidates.append({
                            "player": info["name"],
                            "team": info["team"],
                            "stat": stat,
                            "line": floor,
                            "pref": PREF_ORDER.index(stat),
                            "variance": VARIANCE_RANK[stat]
                        })
                    else:
                        near_miss.append({
                            "player": info["name"],
                            "team": info["team"],
                            "stat": stat,
                            "line": floor,
                            "variance": VARIANCE_RANK[stat],
                            "score": near_miss_score(last5, stat, floor)
                        })

        candidates.sort(key=lambda x: (x["pref"], x["variance"]))

        chosen = build_sgp_with_constraints(
            candidates,
            team_a["code"],
            team_b["code"],
            choose_main_team(eligible, team_a["code"], team_b["code"]),
            mode_to_legs(risk_mode, legs_n)
        )

        if len(chosen) < min_legs:
            st.warning(random.choice(NO_BET_MESSAGES))
            if near_miss:
                st.subheader("🟡 Closest Possible Parlay")
                near_miss.sort(key=lambda x: (x["variance"], -x["score"]))
                for p in near_miss[:min_legs]:
                    st.write(f'• {p["player"]} {p["stat"]} ≥ {p["line"]}')
            st.stop()

        safe = make_safe(chosen)

    st.success("🔍 Search complete for this matchup")

        # ----------------------------
        # DISPLAY RESULTS (OPTION A)
        # ----------------------------
        
    st.success("✅ SGP built successfully")
        
        main_team = choose_main_team(
            eligible,
            team_a["code"],
            team_b["code"]
        )
        
        opp_team = team_b["code"] if main_team == team_a["code"] else team_a["code"]
        
        st.markdown("### Team constraint")
        st.write(
            f"Main side inferred: **{main_team}** "
            f"(max **1** opposing player from **{opp_team}**)"
        )
        
        st.subheader("🔥 Final Slip")
        for p in chosen:
            st.write(f'• {p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]})')
        
        if len(safe) < len(chosen):
            st.subheader("🛡 SAFE Slip")
            for p in safe:
                st.write(f'• {p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]})')
        
        # ----------------------------
        # DEBUG OUTPUT
        # ----------------------------
        if show_debug:
            st.subheader("Debug: Eligible players")
            st.dataframe(pd.DataFrame(eligible))
        
            st.subheader("Debug: Candidates (top 50)")
            st.dataframe(pd.DataFrame(candidates).head(50))
        
            st.subheader("Debug: Near-miss candidates")
            st.dataframe(pd.DataFrame(near_miss))
        
