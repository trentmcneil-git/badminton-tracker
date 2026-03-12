"""
Standalone helper script - called as a subprocess to avoid asyncio conflicts with Streamlit.
Usage: python _playwright_helper.py <tournament_id>
Outputs JSON array of player dicts to stdout.
"""
import sys
import re
import json
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def normalize_player_name(name: str) -> str:
    name = str(name).strip()
    name = re.sub(r'\s*\[.*?\]\s*$', '', name).strip()
    if ',' in name:
        parts = name.split(',', 1)
        name = parts[1].strip() + ' ' + parts[0].strip()
    return name.title().strip()

def main():
    tournament_id = sys.argv[1]
    url = f"https://badmintoncanada.tournamentsoftware.com/tournament/{tournament_id}/players"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        try:
            page.wait_for_selector(".list__item.js-alphabet-list-item", timeout=10000)
        except Exception:
            pass
        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for item in soup.select(".list__item.js-alphabet-list-item"):
        name_el = item.select_one("h5.media__title .nav-link__value, h5.media__title")
        club_el = item.select_one(".media__subheading .nav-link__value, .media__subheading")
        link_el = item.select_one("a.nav-link[href*='/player/']")

        name = normalize_player_name(name_el.get_text(strip=True)) if name_el else ""
        club = club_el.get_text(strip=True) if club_el else ""
        player_url = link_el["href"] if link_el else ""

        if name:
            rows.append({
                "player_name": name,
                "club": club,
                "player_url": player_url,
                "tournament_id": tournament_id,
            })

    print(json.dumps(rows))

if __name__ == "__main__":
    main()
