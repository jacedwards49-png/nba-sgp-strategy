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
    "https://v1.basketball.api-sports.io",  # Basketball API
    "https://v2.nba.api-sports.io",         # NBA v2 API
]

HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}

# NBA identifiers used across API-Sports variants
LEAGUE_NUMERIC = 12          # common numeric NBA league id (basketball API)
LEAGUE_STRING = "standard"   # common league name for nba v2

NO_BET_MESSAGES = [
    "❌ No bets here home boy, move to next matchup",
    "🧊 Cold game — zero edge",
    "🚫 Nothing clean here, pass it",
    "📉 Variance too high — bankroll protection engaged",
    "😴 This matchup ain't it",
    "🧯 Nothing but traps — skip it",
]

# ============================================================
# LOCKED OPTION A MODEL RULES
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

def make_safe(chosen):
    """
    SAFE slip removes the highest variance leg.
    Tie-breaker: remove PTS first.
    """
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
    """
    Main side inferred: whichever team has more eligible players.
    Tie -> team_a.
    """
    counts = {team_a_code: 0, team_b_code: 0}
    for p in eligible_players:
        t = p["team"]
        if t in counts:
            counts[t] += 1
    return team_a_code if counts[team_a_code] >= counts[team_b_code] else team_b_code

def build_sgp_with_constraints(cands, team_a_code, team_b_code, main_team, n_legs):
    """
    Enforce Option A:
      - 3–5 legs
      - max 1 opposing player
      - no duplicate player+stat
    """
    n_legs = max(3, min(5, int(n_legs)))
    opp_team = team_b_code if main_team == team_a_code else team_a_code

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

# ============================================================
# SEASON RESOLUTION (AUTO + SAFE, never future)
# ============================================================

def current_nba_season_start_year() -> int:
    today = datetime.today()
    return today.year if today.month >= 10 else today.year - 1

CURRENT_START_YEAR = current_nba_season_start_year()
st.caption(
    f"NBA season: **{CURRENT_START_YEAR}–{str(CURRENT_START_YEAR+1)[-2:]}**"
)

# ============================================================
# API HELPERS (ROBUST + DEBUG)
# ============================================================

def _try_get(base, path, params, timeout=25):
    url = f"{base}/{path.lstrip('/')}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
    return r, url

def api_get_any_base(path, params, retries=3, timeout=25, bases=None, debug_log=None):
    """
    Tries bases in order. Retries on 429/5xx/timeouts.
    Returns JSON dict (or raises).
    If debug_log provided, appends attempt info.
    """
    bases = bases or API_BASES
    last_error = None

    for base in bases:
        for attempt in range(retries):
            try:
                r, url = _try_get(base, path, params, timeout=timeout)

                info = {
                    "base": base,
                    "path": path,
                    "params": dict(params),
                    "status": r.status_code,
                }

                # Retryable
                if r.status_code in (429, 500, 502, 503, 504):
                    info["note"] = "retryable_status"
                    if debug_log is not None:
                        debug_log.append(info)
                    time.sleep(1.0 + 0.5 * attempt)
                    continue

                # Non-200
                if r.status_code != 200:
                    info["note"] = "non_200"
                    info["text_preview"] = r.text[:250]
                    if debug_log is not None:
                        debug_log.append(info)
                    last_error = RuntimeError(f"{url} HTTP {r.status_code}: {r.text[:250]}")
                    break

                j = r.json()

                # API-Sports errors payload
                if isinstance(j, dict) and j.get("errors"):
                    info["note"] = "api_errors"
                    info["errors"] = str(j.get("errors"))[:250]
                    if debug_log is not None:
                        debug_log.append(info)
                    last_error = RuntimeError(f"{url} API errors: {str(j.get('errors'))[:250]}")
                    break

                # OK
                info["note"] = "ok"
                if isinstance(j, dict) and "response" in j:
                    info["resp_len"] = len(j.get("response") or [])
                if debug_log is not None:
                    debug_log.append(info)
                return j

            except requests.exceptions.ReadTimeout as e:
                last_error = e
                if debug_log is not None:
                    debug_log.append(
                        {"base": base, "path": path, "params": dict(params), "status": "timeout"}
                    )
                time.sleep(1.0 + 0.5 * attempt)
            except Exception as e:
                last_error = e
                if debug_log is not None:
                    debug_log.append(
                        {"base": base, "path": path, "params": dict(params), "status": "exception", "err": str(e)[:250]}
                    )
                break

    raise RuntimeError("API-Sports request failed") from last_error

@st.cache_data(ttl=3600)
def pick_working_base() -> str:
    """
    Uses /status to pick the first base that responds cleanly.
    """
    dbg = []
    for base in API_BASES:
        try:
            j = api_get_any_base("status", {}, bases=[base], debug_log=dbg)
            # If we got here, base works
            return base
        except Exception:
            continue
    # If nothing works, default to first (we'll surface details in debug)
    return API_BASES[0]

WORKING_BASE = pick_working_base()

@st.cache_data(ttl=86400)
def resolve_api_season_year() -> int:
    """
    Try multiple /seasons styles; pick max <= CURRENT_START_YEAR.
    Never returns a future year.
    """
    debug = []
    candidates = []

    attempts = [
        # basketball style
        {"league": LEAGUE_NUMERIC},
        # nba-v2 style
        {"league": LEAGUE_STRING},
        # sometimes seasons works with no league
        {},
    ]

    for params in attempts:
        try:
            j = api_get_any_base("seasons", params, bases=[WORKING_BASE], debug_log=debug)
            resp = j.get("response", []) if isinstance(j, dict) else []
            years = []
            for s in resp:
                try:
                    years.append(int(s))
                except Exception:
                    pass
            candidates.extend(years)
        except Exception:
            continue

    candidates = sorted(set([y for y in candidates if y <= CURRENT_START_YEAR]))
    if candidates:
        return max(candidates)

    # safe fallback: current-1 (never future)
    return CURRENT_START_YEAR - 1

API_SEASON_YEAR = resolve_api_season_year()

st.caption(f"API data season year: **{API_SEASON_YEAR}** (never future)")

# ============================================================
# TEAM DROPDOWN DATA (ROBUST MULTI-SOURCE)
# ============================================================

@st.cache_data(ttl=86400)
def get_team_display_list():
    """
    Returns (teams_list, debug_attempts)
    teams_list items: {code, team_id, name, logo, label}
    """
    debug = []
    out = []

    # We try multiple "known-good" paths/params because API-Sports NBA endpoints vary by base/account.
    # Order: standings -> teams -> games (league+season only)
    tries = [
        ("standings", {"league": LEAGUE_NUMERIC, "season": API_SEASON_YEAR}),
        ("standings", {"league": LEAGUE_STRING, "season": API_SEASON_YEAR}),
        ("teams", {"league": LEAGUE_NUMERIC, "season": API_SEASON_YEAR}),
        ("teams", {"league": LEAGUE_STRING, "season": API_SEASON_YEAR}),
        ("games", {"league": LEAGUE_NUMERIC, "season": API_SEASON_YEAR}),
        ("games", {"league": LEAGUE_STRING, "season": API_SEASON_YEAR}),
    ]

    # helper to normalize team objects across endpoints
    def add_team(t):
        tid = t.get("id")
        name = t.get("name")
        code = (t.get("code") or t.get("abbreviation") or "").upper()
        logo = t.get("logo")

        if tid and name and code:
            out.append(
                {
                    "code": code,
                    "team_id": int(tid),
                    "name": str(name),
                    "logo": logo,
                    "label": f"{name} ({code})",
                }
            )

    for path, params in tries:
        try:
            j = api_get_any_base(path, params, bases=[WORKING_BASE], debug_log=debug)
            resp = j.get("response", []) if isinstance(j, dict) else []
            if not resp:
                continue

            # standings shape: each entry has entry["team"]
            if path == "standings":
                for entry in resp:
                    team = entry.get("team", {}) if isinstance(entry, dict) else {}
                    add_team(team)

            # teams shape: each entry might be {"team":{...}} or direct
            elif path == "teams":
                for entry in resp:
                    if isinstance(entry, dict) and "team" in entry:
                        add_team(entry.get("team") or {})
                    elif isinstance(entry, dict):
                        add_team(entry)

            # games shape: entry["teams"]["home"/"away"]
            elif path == "games":
                seen = {}
                for g in resp:
                    teams = g.get("teams", {}) if isinstance(g, dict) else {}
                    for side in ("home", "away"):
                        t = teams.get(side, {}) if isinstance(teams, dict) else {}
                        tid = t.get("id")
                        code = (t.get("code") or "").upper()
                        if tid and code:
                            seen[code] = t
                for t in seen.values():
                    add_team(t)

            if out:
                # de-dupe by code
                dedup = {}
                for t in out:
                    dedup[t["code"]] = t
                final = sorted(dedup.values(), key=lambda x: x["name"])
                return final, debug

        except Exception:
            continue

    return [], debug

# ============================================================
# UI — CONTROLS (KEEP SAME STRUCTURE)
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
    st.error("Teams unavailable from API-Sports.")
    if show_debug:
        st.subheader("Debug: team fetch attempts")
        st.json(teams_debug)
        st.write(f"WORKING_BASE selected: {WORKING_BASE}")
        st.write(f"API_SEASON_YEAR resolved: {API_SEASON_YEAR}")
    st.stop()

team_lookup = {t["label"]: t for t in teams}
labels = list(team_lookup.keys())

# ============================================================
# UI — TEAM DROPDOWNS (SAME LAYOUT)
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
# DATA PULL HELPERS
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

@st.cache_data(ttl=1800)
def get_last_games(team_id: int):
    debug = []
    # Try both league styles for games; some accounts need numeric, some need "standard"
    tries = [
        {"league": LEAGUE_NUMERIC, "season": API_SEASON_YEAR, "team": team_id},
        {"league": LEAGUE_STRING, "season": API_SEASON_YEAR, "team": team_id},
    ]
    for params in tries:
        try:
            j = api_get_any_base("games", params, bases=[WORKING_BASE], debug_log=debug)
            games = j.get("response", []) if isinstance(j, dict) else []
            if games:
                games = sorted(games, key=lambda g: (g.get("date") or ""), reverse=True)
                return games[:5]
        except Exception:
            continue
    return []

@st.cache_data(ttl=1800)
def get_game_stats(game_id: int, team_id: int):
    j = api_get_any_base("players/statistics", {"game": game_id, "team": team_id}, bases=[WORKING_BASE])
    return j.get("response", []) if isinstance(j, dict) else []

# ============================================================
# EXECUTION
# ============================================================

if run_btn:
    with st.spinner("Crunching the numbers....."):
        try:
            candidates = []
            eligible_players = []

            for team in (team_a, team_b):
                games = get_last_games(int(team["team_id"]))
                if len(games) < 5:
                    # Can't satisfy 5/5 model on this team
                    continue

                player_logs = {}

                for g in games:
                    gid = g.get("id")
                    if not gid:
                        continue

                    rows = get_game_stats(int(gid), int(team["team_id"]))
                    for r in rows:
                        if not isinstance(r, dict):
                            continue

                        p = r.get("player", {}) or {}
                        pid = p.get("id")
                        name = p.get("name") or "Unknown"

                        stats = r.get("statistics", {}) or {}
                        if not pid:
                            continue

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
                    last5 = info["games"][:5]
                    if len(last5) != 5 or not minutes_gate(last5):
                        continue

                    eligible_players.append({"player": info["name"], "team": info["team"]})

                    # Candidate legs for this player (REB/AST/PRA/PTS)
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
                st.stop()

            # Sort by preference + variance
            candidates.sort(key=lambda x: (x["pref"], x["variance"], x["player"]))

            # Enforce max 1 opposing + main-team inference
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

        except Exception as e:
            st.warning("⚠️ Temporary API issue. Try again shortly.")
            if show_debug:
                st.exception(e)
