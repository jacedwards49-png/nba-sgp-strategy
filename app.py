import math
import requests
import pandas as pd
import streamlit as st
from datetime import date, timedelta

BASE = "https://api.balldontlie.io/v1"

st.set_page_config(
    page_title="Refined NBA SGP Builder (Option A)",
    layout="centered",
)

st.write("✅ App script loaded")


# ============================
# API HELPERS (NO AUTH)
# ============================

@st.cache_data(ttl=300)
def search_players(search_name: str, per_page: int = 25):
    r = requests.get(
        f"{BASE}/players",
        params={"search": search_name, "per_page": per_page},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["data"]


def get_player_best_match(search_name: str) -> dict:
    data = search_players(search_name)
    if not data:
        raise ValueError(f"No player found for: {search_name}")
    return data[0]


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
    r = requests.get(f"{BASE}/stats", params=params, timeout=20)
    r.raise_for_status()
    return r.json()["data"]


def last5_game_logs(player_id: int, lookback_days: int = 45):
    end = date.today()
    start = end - timedelta(days=lookback_days)

    rows = fetch_stats(player_id, start.isoformat(), end.isoformat())
    rows.sort(key=lambda x: x["game"]["date"], reverse=True)

    last5 = []
    for row in rows:
        if row.get("min") is None:
            continue

        raw_min = str(row.get("min", "")).strip()
        if not raw_min:
            continue

        try:
            mins = int(raw_min.split(":")[0])
        except Exception:
            continue

        pts = int(row.get("pts", 0) or 0)
        reb = int(row.get("reb", 0) or 0)
        ast = int(row.get("ast", 0) or 0)

        last5.append(
            {
                "date": row["game"]["date"][:10],
                "min": mins,
                "pts": pts,
                "reb": reb,
                "ast": ast,
                "pra": pts + reb + ast,
            }
        )

        if len(last5) == 5:
            break

    team_abbrev = rows[0]["team"]["abbreviation"] if rows else "?"
    return last5, team_abbrev


# ============================
# MODEL RULES
# ============================

def parse_matchup(matchup: str):
    m = matchup.upper().replace("@", "VS").replace("VS.", "VS").strip()
    parts = [p.strip() for p in m.split("VS") if p.strip()]
    if len(parts) != 2:
        raise ValueError("Matchup must look like: LAL vs DAL")
    return parts[0], parts[1]


def minutes_gate(last5):
    mins = [g["min"] for g in last5]
    all_28_plus = len(mins) == 5 and all(m >= 28 for m in mins)
    over_30 = sum(1 for m in mins if m > 30)
    return all_28_plus or over_30 >= 4


def stat_eligibility(last5):
    return len(last5) == 5


def floor_line(values):
    return int(math.floor(min(values) * 0.90)) if values else 0


def build_floor_output(last5):
    stats = {
        "REB": [g["reb"] for g in last5],
        "AST": [g["ast"] for g in last5],
        "PRA": [g["pra"] for g in last5],
        "PTS": [g["pts"] for g in last5],
    }
    variance = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}

    rows = []
    for stat, arr in stats.items():
        rows.append(
            {
                "stat": stat,
                "floor": floor_line(arr),
                "variance": variance[stat],
            }
        )

    return pd.DataFrame(rows).sort_values(by=["variance", "stat"])


def recommend_legs(df, per_player):
    order = ["REB", "AST", "PRA", "PTS"]
    df["rank"] = df["stat"].apply(lambda x: order.index(x))
    df = df.sort_values(by=["rank", "variance"])
    return df.head(per_player).to_dict("records")


def choose_main_team(players, team_a, team_b):
    counts = {team_a: 0, team_b: 0}

    for p in players:
        if p["eligible"] and p["team"] in counts:
            counts[p["team"]] += 1

    if counts[team_a] >= counts[team_b]:
        return team_a
    else:
        return team_b

