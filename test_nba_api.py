import requests

API_KEY = "01df2e076b68364382fa3ce860cb76b7"

BASES = [
    "https://v2.nba.api-sports.io",
    "https://v1.basketball.api-sports.io",
]

HEADERS = {
    "x-apisports-key": API_KEY
}

def test(endpoint, params=None):
    for base in BASES:
        try:
            url = f"{base}/{endpoint}"
            r = requests.get(url, headers=HEADERS, params=params, timeout=20)
            print("\n==============================")
            print(f"BASE: {base}")
            print(f"URL: {r.url}")
            print(f"STATUS: {r.status_code}")
            print("RESPONSE:")
            print(r.json())
        except Exception as e:
            print(f"ERROR on {base}: {e}")

print("=== STATUS ===")
test("status")

print("\n=== SEASONS ===")
test("seasons")

print("\n=== TEAMS ===")
test("teams", {"league": "standard", "season": 2024})

print("\n=== GAMES ===")
test("games", {"league": "standard", "season": 2024})

