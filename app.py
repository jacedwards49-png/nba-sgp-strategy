import os
import math
import requests
import pandas as pd
import streamlit as st
from datetime import date, timedelta

API_KEY = os.getenv("BALLDONTLIE_API_KEY", "YOUR_API_KEY")
BASE = "https://api.balldontlie.io/v1"
HEADERS = {"Authorization": API_KEY}

st.set_page_config(page_title="Refined NBA SGP Builder (Option A)", layout="centered")


# ----------------------------
# Basic guards
# ----------------------------
def _require_key():
    if not API_KEY or API_KEY in ("YOUR_API_KEY", "YOUR_API_KEY_HERE"):
        st.error("Missing BALLDONTLIE_API_KEY. Set it as an environment variable and restart Streamlit.")
        st.stop()


# ----------------------------
# API helpers
# ----------------------------
@st.cache_data(ttl=300)
def search_players(search_name: str, per_page: int = 25):
    r = requests.get(
        f"{BASE}/players",
        headers=HEADERS,
        params={"search": search_name, "per_page": per_page},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["data"]


def get_player_best_match(search_name: str) -> dict:
    data = search_players(search_name)
    if not data:
        raise ValueError(f"No player found for: {search_name}")
    return data[0]  # best match (usually first)


@st.cache_data(ttl=86400)
def get_teams():
    r = requests.get(f"{BASE}/teams", headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()["data"]
    return {t["abbreviation"].upper(): t for t in data}


@st.cache_data(ttl=120)
def fetch_stats(player_id: int, start: str, end: str, per_page: int = 100):
    params = {
        "player_ids[]": player_id,
        "start_date": start,
        "end_date": end,
        "per_page": per_page,
        "postseason": "false",
        "period": 0,
    }
    r = requests.get(f"{BASE}/stats", headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()["data"]


def last5_game_logs(player_id: int, lookback_days: int = 45):
    end = date.today()
    start = end - timedelta(days=lookback_days)

    rows = fetch_stats(player_id, start.isoformat(), end.isoformat())
    rows.sort(key=lambda x: x["game"]["date"], reverse=True)  # newest -> oldest

    last5 = []
    for row in rows:
        if row.get("min") is None:
            continue

        raw_min = str(row.get("min", "")).strip()
        if not raw_min:
            continue

        # handle "30" or "30:12"
        try:
            mins = int(raw_min.split(":")[0])
        except Exception:
            continue

        pts = int(row.get("pts", 0) or 0)
        reb = int(row.get("reb", 0) or 0)
        ast = int(row.get("ast", 0) or 0)
        pra = pts + reb + ast

        last5.append(
            {
                "date": row["game"]["date"][:10],
                "min": mins,
                "pts": pts,
                "reb": reb,
                "ast": ast,
                "pra": pra,
            }
        )
        if len(last5) == 5:
            break

    return last5


# ----------------------------
# Option A parsing
# ----------------------------
def parse_matchup(matchup: str):
    m = matchup.upper().replace("@", "VS").replace("VS.", "VS").strip()
    parts = [p.strip() for p in m.split("VS") if p.strip()]
    if len(parts) != 2:
        raise ValueError("Matchup must look like: LAL vs DAL")
    return parts[0], parts[1]


def player_team_abbrev(player_obj: dict) -> str:
    t = player_obj.get("team") or {}
    return (t.get("abbreviation") or "").upper()


# ----------------------------
# Refined model rules
# ----------------------------
def minutes_gate(last5: list[dict]) -> dict:
    mins = [g["min"] for g in last5]
    all_28_plus = (len(mins) == 5) and all(m >= 28 for m in mins)
    over_30_count = sum(1 for m in mins if m > 30)
    pass_gate = all_28_plus or (over_30_count >= 4)  # exception: 4 games > 30
    return {
        "mins": mins,
        "all_28_plus": all_28_plus,
        "over_30_count": over_30_count,
        "pass": pass_gate,
    }


def stat_eligibility(last5: list[dict]) -> dict:
    if len(last5) != 5:
        return {"eligible": False, "reason": "Not enough games in lookback window (need 5)."}
    needed = ("pts", "reb", "ast", "pra", "min")
    for g in last5:
        if any(k not in g or g[k] is None for k in needed):
            return {"eligible": False, "reason": "Missing stat(s) in last 5."}
    return {"eligible": True, "reason": "5/5 games with stats."}


def floor_line(values: list[int]) -> int:
    # floor-based line: lowest minus 10%, rounded down
    return int(math.floor(min(values) * 0.90)) if values else 0


def build_floor_output(last5: list[dict]) -> pd.DataFrame:
    vals = {
        "REB": [g["reb"] for g in last5],
        "AST": [g["ast"] for g in last5],
        "PRA": [g["pra"] for g in last5],
        "PTS": [g["pts"] for g in last5],
    }

    # variance: PTS highest, PRA mid, REB/AST lower
    variance_rank = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}

    rows = []
    for stat, arr in vals.items():
        rows.append(
            {
                "stat": stat,
                "min_last5": min(arr) if arr else None,
                "floor_line": floor_line(arr),
                "eligible_5of5": (len(arr) == 5),
                "variance_rank": variance_rank[stat],
            }
        )

    df = pd.DataFrame(rows).sort_values(by=["variance_rank", "stat"])
    return df


def recommend_legs_for_player(floor_df: pd.DataFrame, per_player: int = 3) -> list[dict]:
    """
    Per player candidates.
    Prefer REB/AST/PRA; avoid PTS unless needed.
    """
    per_player = max(1, min(5, int(per_player)))

    pref_order = ["REB", "AST", "PRA", "PTS"]
    eligible = floor_df[floor_df["eligible_5of5"] == True].copy()
    eligible["pref"] = eligible["stat"].apply(lambda s: pref_order.index(s) if s in pref_order else 999)
    eligible = eligible.sort_values(by=["pref", "variance_rank"])

    picks = []
    # take non-PTS first
    for _, r in eligible.iterrows():
        if r["stat"] == "PTS":
            continue
        picks.append(r.to_dict())
        if len(picks) == per_player:
            return picks

    # if short, allow PTS as last resort
    for _, r in eligible.iterrows():
        if r["stat"] == "PTS":
            picks.append(r.to_dict())
            if len(picks) == per_player:
                break

    return picks


def choose_main_team(players_meta: list[dict], team_a: str, team_b: str) -> str:
    """
    Main side inferred: whichever team has more eligible players.
    Tie-breaker: team of first eligible player.
    """
    counts = {team_a: 0, team_b: 0}
    first = None
    for pm in players_meta:
        if not pm["eligible"]:
            continue
        t = pm["team_abbrev"]
        if t in counts:
            counts[t] += 1
            if first is None:
                first = t

    if counts[team_a] > counts[team_b]:
        return team_a
    if counts[team_b] > counts[team_a]:
        return team_b
    return first or team_a


def flatten_candidates(players_meta: list[dict]) -> list[dict]:
    """
    Combine all per-player picks into a single candidate pool.
    Prioritize preferred stats + low variance.
    """
    cands = []
    pref_rank = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}

    for pm in players_meta:
        if not pm["eligible"]:
            continue
        for p in pm["picks"]:
            cands.append(
                {
                    "player": pm["name"],
                    "team": pm["team_abbrev"],
                    "stat": p["stat"],
                    "line": int(p["floor_line"]),
                    "variance_rank": int(p["variance_rank"]),
                    "pref_rank": pref_rank.get(p["stat"], 99),
                }
            )

    cands.sort(key=lambda x: (x["pref_rank"], x["variance_rank"], x["player"]))
    return cands


def build_sgp(cands: list[dict], team_a: str, team_b: str, main_team: str, n_legs: int):
    """
    Enforce:
    - 3–5 legs
    - max 1 opposing player
    - no duplicate player+stat
    """
    n_legs = max(3, min(5, int(n_legs)))
    opp_team = team_b if main_team == team_a else team_a

    chosen = []
    used_opp_player = False

    for c in cands:
        if len(chosen) >= n_legs:
            break

        if c["team"] == opp_team:
            if used_opp_player:
                continue
            used_opp_player = True

        if any(x["player"] == c["player"] and x["stat"] == c["stat"] for x in chosen):
            continue

        chosen.append(c)

    return chosen


def make_safe(chosen: list[dict]) -> list[dict]:
    """
    SAFE: remove the highest variance leg.
    Tie-breaker: remove PTS first.
    """
    if len(chosen) <= 3:
        return chosen

    def key(x):
        pts_bonus = 1 if x["stat"] == "PTS" else 0
        return (x["variance_rank"], pts_bonus)

    worst = max(chosen, key=key)
    return [x for x in chosen if x is not worst]


# ----------------------------
# UI
# ----------------------------
st.title("Refined Floor-Based NBA SGP Builder (Option A)")
st.caption("Input: 'LAL vs DAL' + players • Output: 3–5 legs + SAFE • Last 5 only • Minutes gate • 5/5 • Floor lines • Max 1 opposing player")

_require_key()

matchup = st.text_input("Game matchup (Option A)", value="LAL vs DAL")

players_text = st.text_area(
    "Player names (one per line)",
    value="LeBron James\nAnthony Davis\nLuka Doncic\nKyrie Irving",
    height=140,
)

col1, col2, col3 = st.columns(3)
with col1:
    lookback = st.number_input("Lookback days", min_value=10, max_value=120, value=45, step=5)
with col2:
    legs_n = st.number_input("Final slip legs (3–5)", min_value=3, max_value=5, value=4, step=1)
with col3:
    per_player = st.number_input("Candidate legs per player", min_value=1, max_value=5, value=3, step=1)

run_btn = st.button("Build SGP slip", type="primary")

if run_btn:
    names = [n.strip() for n in players_text.splitlines() if n.strip()]
    if not names:
        st.warning("Add at least one player name.")
        st.stop()

    try:
        team_a, team_b = parse_matchup(matchup)
        teams = get_teams()
        if team_a not in teams or team_b not in teams:
            st.error(f"Unknown team abbreviation(s). Got '{team_a}' and '{team_b}'. Example: LAL vs DAL")
            st.stop()
    except Exception as e:
        st.error(str(e))
        st.stop()

    st.markdown(f"### Matchup: **{team_a} vs {team_b}**")

    players_meta = []
    for name in names:
        try:
            p0 = get_player_best_match(name)
            pid = p0["id"]
            t_abbrev = player_team_abbrev(p0) or "?"

            last5 = last5_game_logs(pid, lookback_days=int(lookback))
            logs_df = pd.DataFrame(last5) if last5 else pd.DataFrame()

            elig = stat_eligibility(last5)
            mins = minutes_gate(last5)
            in_game = (t_abbrev in (team_a, team_b))

            ok = (len(last5) == 5) and elig["eligible"] and mins["pass"] and in_game

            reason = []
            if not in_game:
                reason.append(f"Not on {team_a}/{team_b} (team={t_abbrev})")
            if not mins["pass"]:
                reason.append(f"Minutes gate fail mins={mins['mins']} (need ≥28 all 5 OR >30 in 4/5)")
            if not elig["eligible"]:
                reason.append(f"5/5 fail: {elig['reason']}")
            if len(last5) != 5:
                reason.append("Need 5 games (increase lookback days)")

            picks = []
            floor_df = None
            if ok:
                floor_df = build_floor_output(last5)
                picks = recommend_legs_for_player(floor_df, per_player=int(per_player))

            players_meta.append(
                {
                    "name": name,
                    "player_id": pid,
                    "team_abbrev": t_abbrev,
                    "eligible": ok,
                    "notes": "; ".join(reason) if reason else "Eligible",
                    "last5_df": logs_df,
                    "floor_df": floor_df,
                    "picks": picks,
                }
            )

        except Exception as e:
            players_meta.append(
                {
                    "name": name,
                    "player_id": None,
                    "team_abbrev": "?",
                    "eligible": False,
                    "notes": str(e),
                    "last5_df": pd.DataFrame(),
                    "floor_df": None,
                    "picks": [],
                }
            )

    # Eligibility summary
    summary_df = pd.DataFrame(
        [
            {
                "player": pm["name"],
                "team": pm["team_abbrev"],
                "eligible": pm["eligible"],
                "notes": pm["notes"],
            }
            for pm in players_meta
        ]
    )
    st.markdown("### Player Eligibility")
    st.dataframe(summary_df, use_container_width=True)

    eligible_players = [pm for pm in players_meta if pm["eligible"]]
    if not eligible_players:
        st.error("No eligible players after minutes gate / 5-of-5 / matchup filtering.")
        st.stop()

    # Show per-player logs + floors (optional but useful)
    with st.expander("Show per-player last 5 + floor lines", expanded=False):
        for pm in players_meta:
            st.subheader(f"{pm['name']} ({pm['team_abbrev']})")
            if not pm["last5_df"].empty:
                st.dataframe(pm["last5_df"], use_container_width=True)
            if pm["floor_df"] is not None:
                st.dataframe(pm["floor_df"][["stat", "min_last5", "floor_line"]], use_container_width=True)
            st.caption(pm["notes"])

    # Build final slip w/ max 1 opposing player
    main_team = choose_main_team(players_meta, team_a, team_b)
    opp_team = team_b if main_team == team_a else team_a

    st.markdown("### Team Constraint")
    st.write(f"Main side inferred: **{main_team}** (max **1** opposing player from **{opp_team}**)")

    cands = flatten_candidates(players_meta)
    chosen = build_sgp(cands, team_a, team_b, main_team, n_legs=int(legs_n))

    if len(chosen) < 3:
        st.error("Could not build at least 3 legs under constraints. Add more players or increase lookback days.")
        st.stop()

    safe = make_safe(chosen)

    def fmt_leg(x):
        return f"{x['player']} {x['stat']} ≥ {x['line']} ({x['team']})"

    st.markdown("## Final Slip")
    st.write(" • " + "\n • ".join(fmt_leg(x) for x in chosen))

    st.markdown("## SAFE Slip")
    st.write(" • " + "\n • ".join(fmt_leg(x) for x in safe))
