"""
Badminton Canada Tournament Scraper
Extracts players, clubs, and match results from tournamentsoftware.com
"""

import os
import re
import sys
import json
import subprocess
import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime


def normalize_player_name(name: str) -> str:
    """
    Standardize player names to 'First Last' title case.
    Handles formats: 'LAST, First', 'First LAST', 'First LAST [3/4]'
    """
    name = str(name).strip()
    # Strip seeding/placement brackets like [1], [3/4], [WC]
    name = re.sub(r'\s*\[.*?\]\s*$', '', name).strip()
    # Convert 'LAST, First' -> 'First Last'
    if ',' in name:
        parts = name.split(',', 1)
        name = parts[1].strip() + ' ' + parts[0].strip()
    # Title-case everything
    return name.title().strip()


def normalize_event(event: str) -> str:
    """Strip group suffixes like '- Group A' from event names."""
    return re.sub(r'\s*-\s*Group\s+[A-Za-z]+\s*$', '', event).strip()


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def extract_tournament_id(url: str) -> str:
    """Pull the UUID out of a tournament URL."""
    match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        url,
    )
    if not match:
        raise ValueError(f"Could not find a tournament ID in URL: {url}")
    return match.group(0)


def get_tournament_info(tournament_id: str) -> dict:
    """Scrape the tournament name and dates from the overview page."""
    url = f"https://badmintoncanada.tournamentsoftware.com/tournament/{tournament_id}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    name_el = soup.select_one(".media__title")
    if not name_el:
        name_el = soup.select_one("h2")
    name = name_el.get_text(strip=True) if name_el else "Unknown Tournament"

    date_el = soup.select_one("time, .tournament-date, [datetime]")
    date_str = date_el.get_text(strip=True) if date_el else ""

    return {"name": name, "date": date_str, "id": tournament_id}


def scrape_players(tournament_id: str) -> pd.DataFrame:
    """Return a DataFrame of all players and their clubs.
    Calls _playwright_helper.py as a subprocess to avoid asyncio conflicts with Streamlit on Windows.
    """
    helper = os.path.join(os.path.dirname(__file__), "_playwright_helper.py")
    result = subprocess.run(
        [sys.executable, helper, tournament_id],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f"Player scraper failed: {result.stderr[:500]}")

    rows = json.loads(result.stdout)

    return pd.DataFrame(rows)


def scrape_matches(tournament_id: str) -> pd.DataFrame:
    """Return a DataFrame of all matches with scores across all tournament days."""
    base = "https://badmintoncanada.tournamentsoftware.com"
    rows = []

    # Fetch the overview page to discover all date URLs
    overview_url = f"{base}/tournament/{tournament_id}/Matches"
    resp = requests.get(overview_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Date pages use pattern /matches/YYYYMMDD
    date_links = soup.select("a[href*='/matches/']")
    date_urls = list(dict.fromkeys(
        base + a["href"] for a in date_links
        if re.search(r"/matches/\d{8}", a["href"], re.IGNORECASE)
    ))
    if not date_urls:
        date_urls = [overview_url]

    for url in date_urls:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract date from URL (YYYYMMDD) and format it nicely
        date_match = re.search(r"/matches/(\d{8})", url, re.IGNORECASE)
        if date_match:
            d = date_match.group(1)
            match_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        else:
            heading = soup.select_one("h2")
            match_date = heading.get_text(strip=True) if heading else ""

        for match_div in soup.select(".match"):
            # Event name and round
            title_items = match_div.select(".match__header-title-item .nav-link__value")
            event = normalize_event(title_items[0].get_text(strip=True)) if len(title_items) > 0 else ""
            round_name = title_items[1].get_text(strip=True) if len(title_items) > 1 else ""

            # Players (one .match__row per player/side)
            player_rows = match_div.select(".match__row")
            players = []
            winners = []
            for pr in player_rows:
                name_el = pr.select_one(".match__row-title-value-content .nav-link__value")
                player_name = normalize_player_name(name_el.get_text(strip=True)) if name_el else ""
                is_winner = bool(pr.select_one(".tag--round"))
                players.append(player_name)
                winners.append(is_winner)

            player1 = players[0] if len(players) > 0 else ""
            player2 = players[1] if len(players) > 1 else ""
            winner = player1 if (len(winners) > 0 and winners[0]) else (
                player2 if (len(winners) > 1 and winners[1]) else ""
            )

            # Scores — each .points ul is one set; two .points__cell per set
            sets = match_div.select(".points")
            scores = []
            for s in sets:
                cells = s.select(".points__cell")
                if len(cells) == 2:
                    scores.append(f"{cells[0].get_text(strip=True)}-{cells[1].get_text(strip=True)}")

            score_str = ", ".join(scores)

            if player1 and player2:
                rows.append({
                    "tournament_id": tournament_id,
                    "date": match_date,
                    "event": event,
                    "round": round_name,
                    "player1": player1,
                    "player2": player2,
                    "winner": winner,
                    "score": score_str,
                })

    return pd.DataFrame(rows)


def scrape_player_registry() -> pd.DataFrame:
    """
    Scrape the Alberta Junior ranking page to get player names, member IDs,
    and birth years. Returns a deduplicated DataFrame keyed on member_id.
    """
    url = "https://badmintoncanada.tournamentsoftware.com/ranking/ranking.aspx?id=50504"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    seen = {}  # member_id -> dict, to deduplicate
    for row in soup.select("table tr"):
        cells = [td.get_text(strip=True) for td in row.select("td")]
        # Data rows: ['rank', '', '', 'Name', '', 'AB#####', 'YYYY', ...]
        if len(cells) >= 7 and cells[5].startswith("AB") and cells[6].isdigit():
            member_id = cells[5]
            if member_id not in seen:
                seen[member_id] = {
                    "player_name": cells[3],
                    "member_id": member_id,
                    "birth_year": int(cells[6]),
                }

    return pd.DataFrame(seen.values())


def scrape_tournament(url: str) -> dict:
    """
    Main entry point. Pass any tournament URL and get back:
      - info:    dict with name/date/id
      - players: DataFrame
      - matches: DataFrame
    """
    tid = extract_tournament_id(url)
    print(f"Fetching tournament {tid}...")

    info = get_tournament_info(tid)
    print(f"  Tournament: {info['name']}")

    print("  Scraping players...")
    players = scrape_players(tid)
    print(f"    Found {len(players)} players")

    print("  Scraping matches...")
    matches = scrape_matches(tid)
    print(f"    Found {len(matches)} matches")

    return {"info": info, "players": players, "matches": matches}


if __name__ == "__main__":
    # Quick test
    TEST_URL = "https://badmintoncanada.tournamentsoftware.com/tournament/F00EA843-6536-473D-8B84-3F1B74B88152"
    result = scrape_tournament(TEST_URL)
    print("\nPlayers sample:")
    print(result["players"].head())
    print("\nMatches sample:")
    print(result["matches"].head())
