import random
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
    "Last 5 completed games only • Box score stats • Minutes gate • "
    "Floor lines • Prefer REB/AST/PRA • Max 1 opposing player"
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
}

NO_BET_MESSAGES = [
    "❌ Nothing clean here, pass it",
    "🧊 Cold matchup — skip",
    "🚫 No safe edges",
    "📉 Variance too high",
    "😴 This matchup ain't it",
]

# ============================================================
# DEBUG HELPER
# ============================================================

def dbg(show_debug, *args):
    if show_debug:
        st.write(*args)

# ============================================================
# MODEL RULES (OPTION A — LOCKED)
# ============================================================

VARIANCE_RANK = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}
PREF_ORDER = ["REB", "AST", "PRA", "PTS"]

def minutes_gate(last5):
    mins = [g["min"] for g in last5]
    return (
        len(mins) == 5 and (
            all(m >= 27 for m in mins) or
            sum(m > 30 for m in mins) >= 4
        )
    )

def near_miss_score(last5, stat, floor):
    vals = [g[stat.lower()] for g in last5]
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

def choose_main_team(players, a, b):
    counts = {a: 0, b: 0}
    for p in players:
        counts[p["team"]] += 1
    return a if counts[a] >= counts[b] else b

def build_sgp_with_constraints(cands, a, b, main_team, n_legs):
    opp = b if main_team == a else a
    chosen, used_opp = [], False

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
# SEASON
# ============================================================

def current_season():
    today = datetime.today()
    return today.year if today.month >= 10 else today.year - 1

SEASON = current_season()

# ============================================================
# API HELPERS
# ============================================================

def api_get(path, params=None):
    r = requests.get(
        f"{BASE_URL}/{path}",
        headers=HEADERS,
        params=params or {},
        timeout=25
    )
    r.raise_for_status()
    return r.json().get("response", [])

@st.cache_data(ttl=86400)
def get_teams():
    teams = api_get("teams")
    return [
        {
            "team_id": t["id"],
            "name": t["name"],
            "code": t["code"],
            "label": f'{t["name"]} ({t["code"]})'
        }
        for t in teams if t.get("code")
    ]

# ============================================================
# STEP 1 — LAST 5 COMPLETED GAMES
# ============================================================

@st.cache_data(ttl=1800)
def get_last_5_completed_games(team_id):
    games = api_get(
        "games",
        {"league": NBA_LEAGUE, "season": SEASON, "team": team_id}
    )

    finished = [
        g for g in games
        if g.get("status", {}).get("long") == "Finished"
    ]

    finished = sorted(
        finished,
        key=lambda g: g["date"]["start"],
        reverse=True
    )

    return finished[:5]

# ============================================================
# STEP 2 — BOX SCORE STATS
# ============================================================

@st.cache_data(ttl=1800)
def get_boxscore_players(game_id):
    return api_get(
        "players",
        {"season": SEASON, "game": game_id}
    )

def parse_minutes(v):
    if not v:
        return 0
    try:
        return int(str(v).split(":")[0])
    except Exception:
        return 0

# ============================================================
# UI CONTROLS
# ============================================================

legs_n = st.slider("Ideal legs (2–5)", 2, 5, 3)
risk_mode = st.selectbox("Mode", ["Safe", "Ideal", "Higher-risk"])
allow_two_leg = st.checkbox("Allow 2-leg parlay (higher confidence)")
show_debug = st.toggle("Show debug", False)

teams = get_teams()
lookup = {t["label"]: t for t in teams}

c1, c2 = st.columns(2)
with c1:
    team_a = lookup[st.selectbox("Team A", list(lookup.keys()))]
with c2:
    team_b = lookup[st.selectbox("Team B", list(lookup.keys()))]

run_btn = st.button("Auto-build best SGP", type="primary")

# ============================================================
# EXECUTION
# ============================================================

if run_btn:
    with st.spinner("Crunching the numbers..."):
        candidates, near_miss, eligible = [], [], []
        min_legs = 2 if allow_two_leg else 3

        for team in (team_a, team_b):
            games = get_last_5_completed_games(team["team_id"])

            # DEBUG #1 — completed games
            dbg(show_debug, "DEBUG completed games", team["code"], len(games))

            if len(games) < 5:
                continue

            logs = {}

            for g in games:
                players = get_boxscore_players(g["id"])

                # DEBUG #2 — stats per game
                dbg(show_debug, "DEBUG stats len", team["code"], g["id"], len(players))

                for r in players:
                    if r.get("team", {}).get("id") != team["team_id"]:
                        continue

                    stats_arr = r.get("statistics", [])
                    if not stats_arr:
                        continue
                    s = stats_arr[0]

                    p = r.get("player", {})
                    pid = p.get("id")
                    if not pid:
                        continue

                    logs.setdefault(pid, {
                        "name": p.get("name"),
                        "team": team["code"],
                        "games": []
                    })

                    logs[pid]["games"].append({
                        "min": parse_minutes(s.get("minutes")),
                        "pts": s.get("points", 0),
                        "reb": s.get("totReb", 0),
                        "ast": s.get("assists", 0),
                        "pra": (
                            s.get("points", 0)
                            + s.get("totReb", 0)
                            + s.get("assists", 0)
                        )
                    })

            # DEBUG #3 — logs integrity
            sample = next(iter(logs.values()), None)
            if sample:
                dbg(show_debug, "DEBUG sample player", sample["name"])
                dbg(show_debug, "DEBUG sample games count", len(sample["games"]))
            else:
                dbg(show_debug, f"DEBUG logs EMPTY for team {team['code']}")

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

        if not candidates:
            st.warning(random.choice(NO_BET_MESSAGES))
            st.stop()

        main_team = choose_main_team(eligible, team_a["code"], team_b["code"])
        chosen = build_sgp_with_constraints(
            candidates,
            team_a["code"],
            team_b["code"],
            main_team,
            legs_n
        )

        if len(chosen) < min_legs:
            st.warning(random.choice(NO_BET_MESSAGES))
            st.stop()

        safe = make_safe(chosen)

    st.success("✅ SGP built")

    st.subheader("🔥 Final Slip")
    for p in chosen:
        st.write(f'• {p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]})')

    if len(safe) < len(chosen):
        st.subheader("🛡 SAFE Slip")
        for p in safe:
            st.write(f'• {p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]})')
