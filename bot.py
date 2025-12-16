import json
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

# -------------------------------------------------
# Logging
# -------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# -------------------------------------------------
# Environment
# -------------------------------------------------
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
if not WEBHOOK_URL:
    raise EnvironmentError("DISCORD_WEBHOOK_URL not set")

# -------------------------------------------------
# Constants
# -------------------------------------------------
LEAGUE_TABLE_URL = "https://siha-uk.co.uk/snl-league-table-25-26/"
STATE_FILE = "posted.json"
SCREENSHOT_PATH = "snl-league-table.png"

UK_TZ = ZoneInfo("Europe/London")

# Weekly post window (UK local time)
POST_DAY = 0          # Monday (Mon=0 … Sun=6)
POST_HOUR = 18        # 18:00 UK time
POST_MINUTE_MAX = 10  # Allow 18:00–18:10 for scheduler jitter

# CSS selector for the league table container
TABLE_SELECTOR = ".sp-template-league-table"

# -------------------------------------------------
# State helpers
# -------------------------------------------------
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"last_posted_week": ""}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return {"last_posted_week": ""}

    state.setdefault("last_posted_week", "")
    return state


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def iso_week_key(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def should_post_now(now: datetime) -> bool:
    return (
        now.weekday() == POST_DAY
        and now.hour == POST_HOUR
        and 0 <= now.minute <= POST_MINUTE_MAX
    )

# -------------------------------------------------
# Screenshot logic (ELEMENT ONLY)
# -------------------------------------------------
def take_table_screenshot(url: str, selector: str, path: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        page.goto(url, wait_until="networkidle", timeout=60000)

        # Wait for the league table container to exist
        table = page.wait_for_selector(selector, timeout=30000)

        # Ensure it’s fully in view
        table.scroll_into_view_if_needed()
        page.wait_for_timeout(500)

        # Screenshot ONLY the table element
        table.screenshot(path=path)

        browser.close()

# -------------------------------------------------
# Discord posting
# -------------------------------------------------
def post_image_to_discord(image_path: str, content: str) -> None:
    with open(image_path, "rb") as f:
        files = {
            "file": (os.path.basename(image_path), f, "image/png")
        }
        data = {
            "content": content
        }
        r = requests.post(WEBHOOK_URL, data=data, files=files, timeout=60)
        r.raise_for_status()

# -------------------------------------------------
# Main
# -------------------------------------------------
def main() -> None:
    now = datetime.now(UK_TZ)

    if not should_post_now(now):
        logging.info("Not in weekly posting window.")
        return

    state = load_state()
    week_key = iso_week_key(now)

    if state["last_posted_week"] == week_key:
        logging.info("League table already posted this week.")
        return

    logging.info("Taking screenshot of SNL league table element...")
    take_table_screenshot(
        LEAGUE_TABLE_URL,
        TABLE_SELECTOR,
        SCREENSHOT_PATH
    )

    caption = (
        f"🏒 **SNL League Table** — {week_key}\n"
        f"_Source: SIHA_"
    )

    logging.info("Posting league table image to Discord...")
    post_image_to_discord(SCREENSHOT_PATH, caption)

    state["last_posted_week"] = week_key
    save_state(state)

    logging.info("Weekly league table posted successfully.")

# -------------------------------------------------
# Entrypoint
# -------------------------------------------------
if __name__ == "__main__":
    main()
