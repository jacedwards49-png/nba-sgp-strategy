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
    "Team dropdown • API-Sports NBA v2 • Last 5 only • 5/5 gate • Minutes gate • "
    "Floor lines • Prefer REB/AST/PRA • Max 1 opposing player • FINAL + SAFE"
)

# ============================================================
# API CONFIG (NBA v2 ONLY)
# ============================================================

API_KEY = st.secrets.get("API_SPORTS_KEY")
if not API_KEY:
    st.error("Missing API_SPORTS_KEY in Streamlit Secrets.")
    st.stop()

BASE_URL = "https://v2.nba.api-sports.io"   # ✅ NBA v2 only
NBA_LEAGUE = "standard"                    # ✅ per docs (leagues endpoint shows "standard")

HEADERS = {
    "x-apisports-key": API_KEY,            # ✅ per docs
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
# MODEL RULES (LOCKED OPTION A)
# ============================================================

VARIANCE_RANK = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}
PREF_ORDER = ["REB", "AST", "PRA", "PTS"]  # PTS last resort

def minutes_gate(last5):
    mins = [g["min"] for g in last5]
    return (
        len(mins) == 5
        and (all(m >= 28 for m in mins) or sum(m > 30 for m in mins) >= 4)
    )

def floor_line(values):
    return int(math.floor(min(values) * 0.90)) if values else 0

def near_miss_score(last5, stat, floor):
    vals = [g[stat.lower()] for g in last5]
    mins = [g["min"] for g in last5]

    score = 0

    # Stat consistency
    if sum(v >= floor for v in vals) == 4:
        score += 1

    # Minutes near miss
    if not minutes_gate(last5):
        if sum(m >= 28 for m in mins) == 4:
            score += 1
        else:
            score += 2

    # Floor closeness
    if min(vals) == floor - 1:
        score += 1

    # Variance penalty
    score += VARIANCE_RANK[stat] - 1

    return score


def make_safe(chosen):
    """SAFE slip removes the highest variance leg. Tie-breaker: remove PTS first."""
    if len(chosen) <= 3:
        return chosen
    worst = max(chosen, key=lambda x: (x["variance"], x["stat"] == "PTS"))
    return [x for x in chosen if x is not worst]

def mode_to_legs(mode, ideal_legs):
    if mode == "Safe":
        return 3
    if mode == "Higher-risk":
        return 5
    return int(ideal_legs)

def choose_main_team(eligible_players, team_a_code, team_b_code):
    counts = {team_a_code: 0, team_b_code: 0}
    for p in eligible_players:
        t = p["team"]
        if t in counts:
            counts[t] += 1
    return team_a_code if counts[team_a_code] >= counts[team_b_code] else team_b_code

def build_sgp_with_constraints(cands, team_a_code, team_b_code, main_team, n_legs):
    n_legs = max(3, min(5, int(n_legs)))
    opp_team = team_b_code if main_team == team_a_code else team_a_code

    chosen = []
    used_opp_player = False

    for c in cands:
        if len(chosen) >= n_legs:
            break

        # max 1 opposing player
        if c["team"] == opp_team:
            if used_opp_player:
                continue
            used_opp_player = True

        # no duplicate player+stat
        if any(x["player"] == c["player"] and x["stat"] == c["stat"] for x in chosen):
            continue

        chosen.append(c)

    return chosen

# ============================================================
# NBA SEASON (safe resolver)
# ============================================================

def current_nba_season_start_year() -> int:
    today = datetime.today()
    return today.year if today.month >= 10 else today.year - 1

CURRENT_START_YEAR = current_nba_season_start_year()
st.caption(f"NBA season: **{CURRENT_START_YEAR}–{str(CURRENT_START_YEAR+1)[-2:]}**")

# ============================================================
# API HELPERS (NBA v2)
# ============================================================

def api_get(path, params=None, retries=3, timeout=25, debug_log=None):
    params = params or {}
    url = f"{BASE_URL}/{path.lstrip('/')}"
    last_err = None

    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)

            info = {
                "url": url,
                "params": dict(params),
                "status": r.status_code,
            }

            # retryable
            if r.status_code in (429, 500, 502, 503, 504):
                info["note"] = "retryable_status"
                if debug_log is not None:
                    debug_log.append(info)
                time.sleep(1.0 + 0.5 * attempt)
                continue

            # non-200
            if r.status_code != 200:
                info["note"] = "non_200"
                info["text_preview"] = r.text[:250]
                if debug_log is not None:
                    debug_log.append(info)
                last_err = RuntimeError(f"{url} HTTP {r.status_code}: {r.text[:250]}")
                break

            j = r.json()

            # nba v2 uses errors: []
            if isinstance(j, dict) and j.get("errors"):
                info["note"] = "api_errors"
                info["errors"] = str(j.get("errors"))[:250]
                if debug_log is not None:
                    debug_log.append(info)
                last_err = RuntimeError(f"{url} API errors: {str(j.get('errors'))[:250]}")
                break

            info["note"] = "ok"
            if isinstance(j, dict) and "response" in j:
                info["resp_len"] = len(j.get("response") or [])
                info["results"] = j.get("results")
            if debug_log is not None:
                debug_log.append(info)

            return j

        except requests.exceptions.ReadTimeout as e:
            last_err = e
            if debug_log is not None:
                debug_log.append({"url": url, "params": dict(params), "status": "timeout"})
            time.sleep(1.0 + 0.5 * attempt)
        except Exception as e:
            last_err = e
            if debug_log is not None:
                debug_log.append({"url": url, "params": dict(params), "status": "exception", "err": str(e)[:250]})
            break

    raise RuntimeError("NBA v2 request failed") from last_err

@st.cache_data(ttl=86400)
def resolve_api_season_year():
    """NBA v2: GET /seasons (no params). Choose max <= current start year."""
    dbg = []
    j = api_get("seasons", {}, debug_log=dbg)
    resp = j.get("response", []) if isinstance(j, dict) else []
    years = []
    for s in resp:
        try:
            years.append(int(s))
        except Exception:
            pass
    valid = [y for y in years if y <= CURRENT_START_YEAR]
    season = max(valid) if valid else CURRENT_START_YEAR - 1
    return season

API_SEASON_YEAR = resolve_api_season_year()
st.caption(f"API data season year: **{API_SEASON_YEAR}**")

# ============================================================
# TEAM LIST (NBA v2: /teams — no league, no season required)
# ============================================================

@st.cache_data(ttl=86400)
def get_team_display_list():
    dbg = []
    j = api_get("teams", {}, debug_log=dbg)
    resp = j.get("response", []) if isinstance(j, dict) else []

    teams = []
    for t in resp:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        name = t.get("name")
        code = (t.get("code") or "").upper()
        logo = t.get("logo")
        # keep only real NBA teams (some APIs include misc entries; code is best filter)
        if tid and name and code and len(code) == 3:
            teams.append(
                {
                    "team_id": int(tid),
                    "name": str(name),
                    "code": code,
                    "logo": logo,
                    "label": f"{name} ({code})",
                }
            )

    # de-dupe by code
    dedup = {}
    for t in teams:
        dedup[t["code"]] = t

    final = sorted(dedup.values(), key=lambda x: x["name"])
    return final, dbg

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

teams, teams_debug = get_team_display_list()
if not teams:
    st.error("Teams unavailable from API-Sports (NBA v2).")
    if show_debug:
        st.subheader("Debug: /teams call")
        st.json(teams_debug)
    st.stop()

team_lookup = {t["label"]: t for t in teams}
labels = list(team_lookup.keys())

# ============================================================
# TEAM DROPDOWNS
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
    if team_a.get("logo"):
        st.image(team_a["logo"], width=80)
with logo2:
    if team_b.get("logo"):
        st.image(team_b["logo"], width=80)

run_btn = st.button("Auto-build best SGP", type="primary")

# ============================================================
# DATA HELPERS (NBA v2)
# ============================================================

def parse_minutes(val):
    if val is None:
        return 0
    s = str(val).strip()
    if not s:
        return 0
    try:
        # "34:12" -> 34
        if ":" in s:
            return int(float(s.split(":")[0]))
        return int(float(s))
    except Exception:
        return 0

def game_date_key(g):
    # NBA v2 typically returns "date": {"start": "..."} OR "date": "YYYY-MM-DD..." depending on version.
    d = g.get("date")
    if isinstance(d, dict):
        return d.get("start") or ""
    return d or ""

def is_finished_game(g):
    # docs show status numbers; finished is typically 3 (Finished).
    stt = g.get("status")
    if isinstance(stt, dict):
        # sometimes { "short": 3, "long": "Finished" }
        short = stt.get("short")
        long = (stt.get("long") or "").lower()
        return short == 3 or "finished" in long
    if isinstance(stt, int):
        return stt == 3
    if isinstance(stt, str):
        return stt.strip() in ("3",) or "finished" in stt.lower()
    return False

@st.cache_data(ttl=1800)
def get_last_games(team_id: int):
    """
    NBA v2 /games requires at least one parameter.
    Use league=standard + season=YYYY + team=<id>.
    """
    dbg = []
    j = api_get(
        "games",
        {"league": NBA_LEAGUE, "season": API_SEASON_YEAR, "team": team_id},
        debug_log=dbg,
    )
    resp = j.get("response", []) if isinstance(j, dict) else []
    if not resp:
        return [], dbg

    # Sort newest first
    resp = sorted(resp, key=game_date_key, reverse=True)

    # Prefer finished games for logs
    finished = [g for g in resp if isinstance(g, dict) and is_finished_game(g)]
    use = finished if len(finished) >= 5 else resp
    return use[:5], dbg

@st.cache_data(ttl=1800)
def get_player_game_stats(game_id: int, team_id: int):
    """
    NBA v2 player stats endpoint from docs: /players/statistics
    Requires at least one parameter.
    Most reliable: season + game + team
    """
    dbg = []
    j = api_get(
        "players/statistics",
        {"season": API_SEASON_YEAR, "game": game_id, "team": team_id},
        debug_log=dbg,
    )
    resp = j.get("response", []) if isinstance(j, dict) else []
    return resp, dbg

# ============================================================
# EXECUTION
# ============================================================

if run_btn:
    with st.spinner("Crunching the numbers....."):
        try:
            candidates = []
            eligible_players = []
            near_miss_candidates = []


            all_debug = {"teams": teams_debug, "last_games": {}, "player_stats": {}}

            for team in (team_a, team_b):
                games, games_dbg = get_last_games(int(team["team_id"]))
                all_debug["last_games"][team["code"]] = games_dbg

                if len(games) < 5:
                    continue  # can't satisfy 5/5 gate

                player_logs = {}

                for g in games:
                    if not isinstance(g, dict):
                        continue
                    gid = g.get("id")
                    if not gid:
                        continue

                    rows, stats_dbg = get_player_game_stats(int(gid), int(team["team_id"]))
                    all_debug["player_stats"][f"{team['code']}_{gid}"] = stats_dbg

                    for r in rows:
                        if not isinstance(r, dict):
                            continue

                        p = r.get("player", {}) or {}
                        pid = p.get("id")
                        name = p.get("name") or "Unknown"
                        if not pid:
                            continue

                        stats = r.get("statistics", {}) or {}

                        log = {
                            "min": parse_minutes(stats.get("minutes") or stats.get("min")),
                            "pts": int(stats.get("points") or stats.get("pts") or 0),
                            "reb": int(stats.get("totReb") or stats.get("reb") or 0),
                            "ast": int(stats.get("assists") or stats.get("ast") or 0),
                        }
                        log["pra"] = log["pts"] + log["reb"] + log["ast"]

                        player_logs.setdefault(pid, {"name": name, "team": team["code"], "games": []})
                        player_logs[pid]["games"].append(log)

                # Evaluate players (must have 5 games + minutes gate)
for info in player_logs.values():
    try:
        last5 = info["last5"]

        # Gate: must have exactly 5 games + minutes check
        if len(last5) != 5 or not minutes_gate(last5):
            continue

        for stat in PREF_ORDER:
            values = [g.get(stat, 0) for g in last5]

            # Require stat present in all 5 games
            if any(v is None for v in values):
                continue

            floor = int(min(values) * 0.90)

            if floor <= 0:
                continue

            leg = {
                "player": info["player_name"],
                "team": info["team"],
                "stat": stat,
                "line": floor,
                "values": values,
            }

            candidate_legs.append(leg)

    except Exception as e:
        # Fail silently on bad player data
        continue



                    eligible_players.append({"player": info["name"], "team": info["team"]})

                    for stat in PREF_ORDER:
                        key = stat.lower()
                        vals = [g[key] for g in last5]
                        candidates.append(
                            {
                                "player": info["name"],
                                "team": info["team"],
                                "stat": stat,
                                "line": floor_line(vals),
                                "pref": PREF_ORDER.index(stat),
                                "variance": VARIANCE_RANK[stat],
                            }
                        )

            if len(eligible_players) == 0 or len(candidates) < 3:
    st.warning(random.choice(NO_BET_MESSAGES))

    if near_miss_candidates:
        st.subheader("🟡 Closest Possible Parlay (did not fully qualify)")

        # Rank by lowest variance, then closest to hitting
        near_miss_candidates.sort(
            key=lambda x: (x["variance"], -x["score"])
        )

        fallback_legs = near_miss_candidates[:mode_to_legs(risk_mode, legs_n)]

        for p in fallback_legs:
            st.write(
                f'• {p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]}) '
                f'(miss score: {round(p["score"], 2)})'
            )

    st.stop()


            candidates.sort(key=lambda x: (x["pref"], x["variance"], x["player"]))

            main_team = choose_main_team(eligible_players, team_a["code"], team_b["code"])
            opp_team = team_b["code"] if main_team == team_a["code"] else team_a["code"]

            legs = mode_to_legs(risk_mode, legs_n)
            chosen = build_sgp_with_constraints(
                candidates,
                team_a["code"],
                team_b["code"],
                main_team=main_team,
                n_legs=legs,
            )

            if len(chosen) < 3:
                st.warning(random.choice(NO_BET_MESSAGES))
                if show_debug:
                    st.subheader("Debug: API calls")
                    st.json(all_debug)
                st.stop()

            safe = make_safe(chosen)

            st.success("✅ SGP built successfully")
            st.markdown("### Team constraint")
            st.write(f"Main side inferred: **{main_team}** (max **1** opposing player from **{opp_team}**)")

            st.subheader("🔥 Final Slip")
            for p in chosen:
                st.write(f'• {p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]})')

            st.subheader("🛡 SAFE Slip")
            for p in safe:
                st.write(f'• {p["player"]} {p["stat"]} ≥ {p["line"]} ({p["team"]})')

            if show_debug:
                st.subheader("Debug: eligible players")
                st.dataframe(pd.DataFrame(eligible_players), use_container_width=True)
                st.subheader("Debug: candidates (top 50)")
                st.dataframe(pd.DataFrame(candidates).head(50), use_container_width=True)
                st.subheader("Debug: API calls (raw)")
                st.json(all_debug)

        except Exception as e:
            st.warning("⚠️ Temporary API issue. Try again shortly.")
            if show_debug:
                st.exception(e)
