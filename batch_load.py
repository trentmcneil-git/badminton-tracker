"""
Batch load all completed 2025-26 AJC tournaments.
Run from the badminton-tracker directory:
    python batch_load.py
"""

import os
import json
import pandas as pd
from scraper import scrape_tournament, extract_tournament_id

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

TOURNAMENTS_FILE = os.path.join(DATA_DIR, "tournaments.json")
PLAYERS_FILE = os.path.join(DATA_DIR, "players.csv")
MATCHES_FILE = os.path.join(DATA_DIR, "matches.csv")

# All completed tournaments with URLs (as of 2026-03-11)
TOURNAMENTS = [
    {"name": "Blackfalds Junior",              "tier": "SILVER",   "dates": "Sep 12–14",    "url": "https://badmintoncanada.tournamentsoftware.com/tournament/807ce64d-893e-4740-9994-53cb06a2eefe"},
    {"name": "Clearone Calgary Junior",        "tier": "GOLD",     "dates": "Sep 19–21",    "url": "https://badmintoncanada.tournamentsoftware.com/tournament/3786adf2-d77b-44b6-b6a4-578ec1c60a35"},
    {"name": "Modu Badminton Club Junior",     "tier": "SILVER",   "dates": "Oct 3–5",      "url": "https://badmintoncanada.tournamentsoftware.com/tournament/2802ec6b-831f-4ca7-85a9-bf34b2ced959"},
    {"name": "Sunridge Junior",                "tier": "GOLD",     "dates": "Oct 24–26",    "url": "https://badmintoncanada.tournamentsoftware.com/tournament/216dd75d-d5d5-481e-8668-540e3d1209c9"},
    {"name": "Synergy Junior",                 "tier": "GOLD",     "dates": "Nov 7–9",      "url": "https://badmintoncanada.tournamentsoftware.com/tournament/20f4dc50-a87e-4e2b-80af-e9311f5dadc0"},
    {"name": "Edison Junior (u11/13/15)",      "tier": "SILVER",   "dates": "Nov 14–16",    "url": "https://badmintoncanada.tournamentsoftware.com/tournament/338d3e12-26ae-4de5-afaa-fb429c3018d9"},
    {"name": "Alberta Junior Elite",           "tier": "NATIONAL", "dates": "Nov 28–30",    "url": "https://badmintoncanada.tournamentsoftware.com/tournament/a3160b3c-7ee3-44af-a955-4ada88bb35fb"},
    {"name": "B-Active Junior",                "tier": "SILVER",   "dates": "Dec 5–7",      "url": "https://badmintoncanada.tournamentsoftware.com/tournament/a742ac96-de8a-450a-896b-2a13a599dc25"},
    {"name": "Glencoe Club Junior (Pilot)",    "tier": "BRONZE",   "dates": "Dec 6–7",      "url": "https://badmintoncanada.tournamentsoftware.com/tournament/fa8ae4dd-e675-411e-99e2-c999aad4bd2b"},
    {"name": "Olds College Junior",            "tier": "GOLD",     "dates": "Dec 19–21",    "url": "https://badmintoncanada.tournamentsoftware.com/tournament/b9c2c368-f7fd-4e06-b2f9-b5b324c3f95b"},
    {"name": "Modu Junior",                    "tier": "GOLD",     "dates": "Jan 2–4",      "url": "https://badmintoncanada.tournamentsoftware.com/tournament/F00EA843-6536-473D-8B84-3F1B74B88152"},
    {"name": "Badminton Academy Junior",       "tier": "SILVER",   "dates": "Jan 9–11",     "url": "https://badmintoncanada.tournamentsoftware.com/tournament/eee7aa95-282f-40fa-87aa-ca5756db3e86"},
    {"name": "Derrick Club Junior",            "tier": "GOLD",     "dates": "Jan 23–25",    "url": "https://badmintoncanada.tournamentsoftware.com/tournament/E417A2DF-ED7C-4F30-B0FB-3B22A7CC836E"},
    {"name": "ClearOne Calgary Junior",        "tier": "SILVER",   "dates": "Jan 30–Feb 1", "url": "https://badmintoncanada.tournamentsoftware.com/tournament/a7e7242e-7048-4a4f-8cca-56f64f433a83"},
    {"name": "Blackfalds Junior",              "tier": "GOLD",     "dates": "Feb 6–8",      "url": "https://badmintoncanada.tournamentsoftware.com/tournament/2E257919-BB98-4C28-B498-FA740D612360"},
    {"name": "B-Active (Shirley Mah Memorial)","tier": "GOLD",     "dates": "Feb 27–Mar 1", "url": "https://badmintoncanada.tournamentsoftware.com/tournament/0C767982-6953-44DD-819A-AA969512533E"},
]


def load_tournaments() -> dict:
    if os.path.exists(TOURNAMENTS_FILE):
        with open(TOURNAMENTS_FILE) as f:
            return json.load(f)
    return {}


def save_tournament_info(info: dict):
    tournaments = load_tournaments()
    tournaments[info["id"]] = info
    with open(TOURNAMENTS_FILE, "w") as f:
        json.dump(tournaments, f, indent=2)


def save_data(players: pd.DataFrame, matches: pd.DataFrame, tournament_id: str):
    # Players
    if os.path.exists(PLAYERS_FILE):
        existing = pd.read_csv(PLAYERS_FILE)
        existing = existing[existing["tournament_id"] != tournament_id]
    else:
        existing = pd.DataFrame(columns=["player_name", "club", "player_url", "tournament_id"])
    pd.concat([existing, players], ignore_index=True).to_csv(PLAYERS_FILE, index=False)

    # Matches
    if os.path.exists(MATCHES_FILE):
        existing_m = pd.read_csv(MATCHES_FILE)
        existing_m = existing_m[existing_m["tournament_id"] != tournament_id]
    else:
        existing_m = pd.DataFrame(columns=["tournament_id", "date", "event", "round", "player1", "player2", "winner", "score"])
    pd.concat([existing_m, matches], ignore_index=True).to_csv(MATCHES_FILE, index=False)


def main():
    loaded = load_tournaments()
    loaded_ids_upper = {k.upper() for k in loaded.keys()}

    total = len(TOURNAMENTS)
    skipped = 0
    success = 0
    failed = 0

    for i, t in enumerate(TOURNAMENTS, 1):
        name = t["name"]
        tier = t["tier"]
        dates = t["dates"]
        url = t["url"]

        tid = extract_tournament_id(url)
        prefix = f"[{i}/{total}] {dates} — {name} ({tier})"

        if tid.upper() in loaded_ids_upper:
            print(f"  SKIP  {prefix}  (already loaded)")
            skipped += 1
            continue

        print(f"  LOAD  {prefix} ...")
        try:
            result = scrape_tournament(url)
            # Preserve tier in the info dict
            result["info"]["tier"] = tier
            save_tournament_info(result["info"])
            save_data(result["players"], result["matches"], result["info"]["id"])
            n_players = len(result["players"])
            n_matches = len(result["matches"])
            print(f"        OK  {n_players} players, {n_matches} matches")
            loaded_ids_upper.add(tid.upper())
            success += 1
        except Exception as e:
            print(f"        FAILED: {e}")
            failed += 1

    print(f"\nDone. Loaded: {success}  Skipped: {skipped}  Failed: {failed}")


if __name__ == "__main__":
    main()
