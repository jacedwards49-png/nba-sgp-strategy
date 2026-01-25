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
    "Team-only input • API-NBA (API-Sports) • Last 5 only • 5/5 gate • Minutes gate • Floor lines • "
    "Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# ============================
# API-SPORTS (API-NBA) CONFIG
# ============================

API_BASE = "https://v1.nba.api-sports.io"
API_KEY = st.secrets.get("API_SPORTS_KEY", None)

if not API_KEY:
    st.error("Missing API_SPORTS_KEY. Add it in Streamlit Secrets and rerun.")
    st.stop()

API_HEADERS = {"x-apisports-key": API_KEY}

# ============================
# SEASON AUTO-DETECTION
# ============================

def current_season_year() -> int:
    today = datetime.today()
    return today.year if today.month >= 10 else today.year - 1

SEASON_YEAR = current_season_year()

# ============================
# LOCKED MODEL RULES (OPTION A)
# ============================

PREF_ORDER = ["REB", "AST", "PRA", "PTS"]          # PTS last resort
PREF_RANK = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}
VARIANCE_RANK = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}

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

def floor_line(values):
    return int(math.floor(min(values) * 0.90)) if values else 0

def minutes_gate(last5):
    mins = [g["min"] for g in last5]
    if len(mins) != 5:
        return False
    all_28_plus = all(m >= 28 for m in mins)
    over_30_count = sum(1 for m in mins if m > 30)
    return all_28_plus or (over_30_count >= 4)

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
            "min_last5": min(arr) if arr else None,
            "line": floor_line(arr),
            "pref": PREF_ORDER.index(stat),
            "variance": VARIANCE_RANK[stat],
        })
    return pd.DataFrame(rows).sort_values(by=["pref", "variance", "stat"])

def make_safe(chosen):
    if len(chosen) <= 3:
        return chosen

    def key(x):
        pts_bonus = 1 if x["stat"] == "PTS" else 0
        return (x["variance"], pts_bonus)

    worst = max(chosen, key=key)
    return [x for x in chosen if x is not worst]

# ============================
# API HELPERS (PRO-TUNED)
# ============================

def api_get(endpoint: str, params: dict, retries=5, timeout=45):
    last_err = None
    backoff = 1.0

    for _ in range(retries):
        try:
            r = requests.get(
                f"{API_BASE}/{endpoint}",
                headers=API_HEADERS,
                params=params,
                timeout=timeout
            )
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ReadTimeout as e:
            last_err = e
        except requests.exceptions.RequestException as e:
            last_err = e

        time.sleep(backoff)
        backoff = min(backoff * 1.7, 6.0)

    raise RuntimeError("API-NBA temporarily unavailable") from last_err

@st.cache_data(ttl=604800)  # 7 days
def get_teams_map() -> dict:
    j = api_get("teams", {})
    resp = j.get("response", [])
    team_map = {}
    for t in resp:
        code = (t.get("code") or "").upper()
        tid = t.get("id")
        name = t.get("name")
        if code and tid:
            team_map[code] = {"team_id": int(tid), "name": name}
    return team_map

@st.cache_data(ttl=86400)  # 24 hours
def get_team_players(team_id: int, season: int) -> pd.DataFrame:
    j = api_get("players", {"team": team_id, "season": season})
    resp = j.get("response", [])
    if not resp:
        return pd.DataFrame()

    df = pd.json_normalize(resp)

    # display name
    if "firstname" in df.columns and "lastname" in df.columns:
        df["player_name"] = (
            df["firstname"].fillna("").astype(str).str.strip() + " " +
            df["lastname"].fillna("").astype(str).str.strip()
        ).str.replace(r"\s+", " ", regex=True).str.strip()
    else:
        df["player_name"] = df.get("name", "")

    return df

@st.cache_data(ttl=600)  # 10 minutes
def get_player_last_games_stats(player_id: int, season: int, last_n: int = 5) -> list[dict]:
    j = api_get("players/statistics", {"id": player_id, "season": season})
    resp = j.get("response", [])
    if not resp:
        return []

    df = pd.json_normalize(resp)

    # pick date column
    date_col = None
    for c in ["game.date", "date", "gameDate"]:
        if c in df.columns:
            date_col = c
            break
    if date_col:
        df = df.sort_values(by=date_col, ascending=False)

    out = []
    for _, r in df.iterrows():
        mins = r.get("min")
        pts = r.get("points") if "points" in df.columns else r.get("pts")
        reb = r.get("totReb") if "totReb" in df.columns else r.get("rebounds") if "rebounds" in df.columns else r.get("reb")
        ast = r.get("assists") if "assists" in df.columns else r.get("ast")

        try:
            mins = int(float(mins)) if mins not in (None, "", "null") else None
            pts = int(float(pts)) if pts not in (None, "", "null") else None
            reb = int(float(reb)) if reb not in (None, "", "null") else None
            ast = int(float(ast)) if ast not in (None, "", "null") else None
        except Exception:
            continue

        if mins is None or pts is None or reb is None or ast is None:
            continue

        out.append({"min": mins, "pts": pts, "reb": reb, "ast": ast, "pra": pts + reb + ast})
        if len(out) == last_n:
            break

    return out

# ============================
# RISK PROFILES
# ============================

def risk_sort_key(profile: str):
    profile = profile.upper().strip()
    if profile == "HIGHER-RISK":
        # PRA slightly promoted; PTS still allowed but less punished
        adj_pref = {"REB": 2, "AST": 2, "PRA": 1, "PTS": 3}
        return lambda c: (adj_pref.get(c["stat"], 99), c["variance"], c["player"])
    # IDEAL / SAFE
    return lambda c: (c["pref"], c["variance"], c["player"])

def build_sgp_from_candidates(candidates: list[dict], team_a: str, team_b: str, legs_n: int):
    legs_n = max(3, min(5, int(legs_n)))

    uniq_players = {team_a: set(), team_b: set()}
    for c in candidates:
        uniq_players[c["team"]].add(c["player"])

    main_team = team_a if len(uniq_players[team_a]) >= len(uniq_players[team_b]) else team_b
    opp_team = team_b if main_team == team_a else team_a

    chosen = []
    used_opp = False

    for c in candidates:
        if len(chosen) >= legs_n:
            break

        if c["team"] == opp_team:
            if used_opp:
                continue
            used_opp = True

        if any(x["player"] == c["player"] and x["stat"] == c["stat"] for x in chosen):
            continue

        chosen.append(c)

    return chosen, main_team, opp_team

# ============================
# UI
# ============================

matchup = st.text_input("Matchup (team acronyms)", value="LAL vs DAL")

col1, col2, col3 = st.columns(3)
with col1:
    legs_n = st.slider("Final slip legs", 3, 5, 4)
with col2:
    max_players_per_team = st.slider(
        "Players checked per team (Pro speed)",
        8, 30, 18,
        help="Pro can handle higher values. Higher = more thorough but slower."
    )
with col3:
    bet_profile = st.selectbox(
        "Bet profile",
        ["SAFE", "IDEAL", "HIGHER-RISK"],
        index=0
    )

st.caption(f"Auto season detected: **{SEASON_YEAR}**")

run_btn = st.button("Auto-build best SGP", type="primary")

# ============================
# EXECUTION
# ============================

if run_btn:
    status = st.empty()
    prog = st.progress(0)

    with st.spinner("Crunching the numbers....."):
        try:
            status.info("Loading teams…")
            team_map = get_teams_map()
            prog.progress(10)

            team_a, team_b = parse_matchup(matchup)

            if team_a not in team_map or team_b not in team_map:
                st.error(f"Unknown team abbreviation(s): '{team_a}' / '{team_b}'. Example: LAL vs DAL")
                st.stop()

            team_a_id = team_map[team_a]["team_id"]
            team_b_id = team_map[team_b]["team_id"]

            candidates = []
            teams = [(team_a, team_a_id), (team_b, team_b_id)]
            total_steps = len(teams) * int(max_players_per_team)
            done = 0

            for team_abbr, team_id in teams:
                status.info(f"Loading roster: {team_abbr}…")
                players_df = get_team_players(team_id, SEASON_YEAR)
                prog.progress(min(25, 10 + (done / max(1, total_steps)) * 15))

                if players_df.empty:
                    continue

                # Consistent selection (no randomness) for stability
                players_df = players_df.head(int(max_players_per_team)).copy()

                for _, prow in players_df.iterrows():
                    done += 1
                    pid = prow.get("id")
                    pname = prow.get("player_name") or "Unknown"

                    # progress UI
                    prog.progress(min(85, 25 + int((done / max(1, total_steps)) * 60)))
                    status.info(f"Crunching: {team_abbr} • {pname}…")

                    if pd.isna(pid):
                        continue

                    last5 = get_player_last_games_stats(int(pid), SEASON_YEAR, last_n=5)

                    if len(last5) != 5:
                        continue
                    if not minutes_gate(last5):
                        continue

                    floors = build_floor_output(last5)

                    # take top 3 per player with PTS last resort
                    per_player_picks = []
                    for _, f in floors.iterrows():
                        if f["stat"] != "PTS":
                            per_player_picks.append(f.to_dict())
                        if len(per_player_picks) == 3:
                            break
                    if len(per_player_picks) < 3:
                        for _, f in floors.iterrows():
                            if f["stat"] == "PTS":
                                per_player_picks.append(f.to_dict())
                                if len(per_player_picks) == 3:
                                    break

                    for pick in per_player_picks:
                        candidates.append({
                            "player": str(pname),
                            "team": team_abbr,
                            "stat": pick["stat"],
                            "line": int(pick["line"]),
                            "pref": int(pick["pref"]),
                            "variance": int(pick["variance"]),
                        })

            # Sort for profile
            candidates.sort(key=risk_sort_key(bet_profile))

            prog.progress(92)
            status.info("Building final slip under constraints…")

            if len(candidates) < 3:
                prog.progress(100)
                status.empty()
                st.warning(random.choice(NO_BET_MESSAGES))
                st.stop()

            chosen, main_team, opp_team = build_sgp_from_candidates(candidates, team_a, team_b, legs_n)

            if len(chosen) < 3:
                prog.progress(100)
                status.empty()
                st.warning(random.choice(NO_BET_MESSAGES))
                st.stop()

            prog.progress(100)
            status.empty()

            st.success(f"✅ Built {len(chosen)} legs | Main side: {main_team} | Max 1 opp from {opp_team}")

            st.subheader("🔥 Final Slip")
            for p in chosen:
                st.write(f"{p['player']} {p['stat']} ≥ {p['line']} ({p['team']})")

            if bet_profile in ("SAFE", "IDEAL"):
                safe = make_safe(chosen)
                st.subheader("🛡 SAFE Slip")
                for p in safe:
                    st.write(f"{p['player']} {p['stat']} ≥ {p['line']} ({p['team']})")

            if bet_profile == "HIGHER-RISK":
                st.caption("Higher-risk profile: candidate ordering is more aggressive (still within model constraints).")

        except Exception as e:
            status.empty()
            st.error("⚠️ Temporary API issue. Try again shortly.")
            st.exception(e)
