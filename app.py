import math
import requests
import pandas as pd
import streamlit as st
from datetime import datetime

# ============================
# STREAMLIT SETUP
# ============================

st.set_page_config(
    page_title="Refined NBA SGP Builder (Option A)",
    layout="centered",
)

st.title("Refined Floor-Based NBA SGP Builder (Option A)")
st.caption(
    "NBA Stats API • Last 5 games • Minutes gate • Floor lines • Option A"
)

# ============================
# NBA STATS API CONFIG
# ============================

NBA_HEADERS = {
    "Host": "stats.nba.com",
    "Connection": "keep-alive",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept-Language": "en-US,en;q=0.9",
}

NBA_BASE = "https://stats.nba.com/stats"

# ============================
# HELPERS
# ============================

@st.cache_data(ttl=86400)
def get_all_players():
    url = f"{NBA_BASE}/commonallplayers"
    params = {
        "LeagueID": "00",
        "Season": "2024-25",
        "IsOnlyCurrentSeason": "1",
    }
    r = requests.get(url, headers=NBA_HEADERS, params=params, timeout=20)
    r.raise_for_status()

    rs = r.json()["resultSets"][0]
    df = pd.DataFrame(rs["rowSet"], columns=rs["headers"])
    return df


def get_player_id(name: str):
    df = get_all_players()
    matches = df[df["DISPLAY_FIRST_LAST"].str.contains(name, case=False)]
    if matches.empty:
        raise ValueError(f"No player found for: {name}")
    return int(matches.iloc[0]["PERSON_ID"])


@st.cache_data(ttl=300)
def get_player_gamelog(player_id: int, season: str):
    url = f"{NBA_BASE}/playergamelog"
    params = {
        "PlayerID": player_id,
        "Season": season,
        "SeasonType": "Regular Season",
    }
    r = requests.get(url, headers=NBA_HEADERS, params=params, timeout=20)
    r.raise_for_status()

    rs = r.json()["resultSets"][0]
    df = pd.DataFrame(rs["rowSet"], columns=rs["headers"])
    return df


def last5_games(player_id: int, season: str):
    df = get_player_gamelog(player_id, season)
    if df.empty:
        return [], "?"

    df = df.sort_values("GAME_DATE", ascending=False)

    last5 = []
    for _, row in df.iterrows():
        mins = int(float(row["MIN"])) if row["MIN"] else 0
        last5.append(
            {
                "date": row["GAME_DATE"],
                "min": mins,
                "pts": int(row["PTS"]),
                "reb": int(row["REB"]),
                "ast": int(row["AST"]),
                "pra": int(row["PTS"] + row["REB"] + row["AST"]),
            }
        )
        if len(last5) == 5:
            break

    team = df.iloc[0]["TEAM_ABBREVIATION"]
    return last5, team


# ============================
# MODEL LOGIC (OPTION A)
# ============================

def minutes_gate(last5):
    mins = [g["min"] for g in last5]
    return len(mins) == 5 and (all(m >= 28 for m in mins) or sum(m > 30 for m in mins) >= 4)


def floor_line(values):
    return int(math.floor(min(values) * 0.9))


def build_floor_df(last5):
    stats = {
        "REB": [g["reb"] for g in last5],
        "AST": [g["ast"] for g in last5],
        "PRA": [g["pra"] for g in last5],
        "PTS": [g["pts"] for g in last5],
    }

    variance = {"REB": 1, "AST": 1, "PRA": 2, "PTS": 3}

    rows = []
    for stat, vals in stats.items():
        rows.append(
            {
                "stat": stat,
                "floor": floor_line(vals),
                "variance": variance[stat],
            }
        )

    return pd.DataFrame(rows).sort_values(["variance", "stat"])


def recommend_legs(df, n):
    pref = ["REB", "AST", "PRA", "PTS"]
    df["rank"] = df["stat"].apply(lambda s: pref.index(s))
    return df.sort_values(["rank", "variance"]).head(n)


# ============================
# UI INPUTS
# ============================

season = st.text_input("Season", "2024-25")

players_text = st.text_area(
    "Player names (one per line)",
    "LeBron James\nAnthony Davis\nLuka Doncic\nKyrie Irving",
    height=140,
)

legs_per_player = st.number_input(
    "Candidate legs per player", min_value=1, max_value=4, value=3
)

# ============================
# BUILD BUTTON
# ============================

if st.button("Build SGP Slip"):
    names = [n.strip() for n in players_text.splitlines() if n.strip()]
    all_legs = []

    for name in names:
        try:
            pid = get_player_id(name)
            last5, team = last5_games(pid, season)

            eligible = len(last5) == 5 and minutes_gate(last5)

            st.markdown(
                f"**{name} ({team})** — {'Eligible' if eligible else 'Filtered'}"
            )

            if not eligible:
                continue

            floors = build_floor_df(last5)
            picks = recommend_legs(floors, legs_per_player)

            for _, r in picks.iterrows():
                all_legs.append(
                    {
                        "player": name,
                        "team": team,
                        "stat": r["stat"],
                        "line": r["floor"],
                        "variance": r["variance"],
                    }
                )

        except Exception as e:
            st.warning(f"{name}: {e}")

    if not all_legs:
        st.error("No eligible players after filters.")
