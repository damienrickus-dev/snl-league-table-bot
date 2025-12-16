import json
import os
import re
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ----------------------------
# Logging
# ----------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ----------------------------
# Env
# ----------------------------
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
if not WEBHOOK_URL:
    raise EnvironmentError("DISCORD_WEBHOOK_URL not set")

# ----------------------------
# Constants
# ----------------------------
UK_TZ = ZoneInfo("Europe/London")
STATE_FILE = "posted.json"

# SIHA official league table page (SNL 25-26)
LEAGUE_TABLE_URL = "https://siha-uk.co.uk/snl-league-table-25-26/"

# Post window (UK local time) – allows scheduler jitter
POST_DAY = 0  # Monday (Mon=0 ... Sun=6)
POST_HOUR = 18
POST_MINUTE_MAX = 10  # 18:00–18:10

# ----------------------------
# Helpers
# ----------------------------
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def post_to_discord(content: str) -> None:
    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=20)
    r.raise_for_status()


def load_state() -> Dict:
    if not os.path.exists(STATE_FILE):
        return {"last_posted_week": ""}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return {"last_posted_week": ""}

    state.setdefault("last_posted_week", "")
    return state


def save_state(state: Dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def current_iso_week_key(now: datetime) -> str:
    # Example: "2025-W51"
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


# ----------------------------
# Scrape + format league table
# ----------------------------
def scrape_snl_table() -> Tuple[List[Dict], Optional[Dict]]:
    """
    Returns:
      rows: list of dicts like {"pos":1,"team":"Warriors","gp":19,"pts":28,...}
      caps_row: the row for Capitals (team contains 'Caps' or 'Capitals'), if found
    """
    html = requests.get(LEAGUE_TABLE_URL, timeout=20).text
    soup = BeautifulSoup(html, "html.parser")

    # Find the first HTML table on the page (SIHA page contains a standings table)
    table = soup.find("table")
    if not table:
        return [], None

    # Extract header labels (best effort)
    headers = []
    thead = table.find("thead")
    if thead:
        headers = [norm(th.get_text(" ")) for th in thead.find_all(["th", "td"])]

    # Extract body rows
    rows = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = [norm(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue

        # If headers exist, map them; otherwise assume common order:
        # Pos, Team, GP, W, L, OTL, GF, GA, Diff, PTS, Strk (varies)
        row = {}

        def to_int(x: str) -> Optional[int]:
            x = x.replace("+", "").strip()
            if re.fullmatch(r"-?\d+", x):
                return int(x)
            return None

        # Best effort parsing by position
        pos = to_int(cells[0])
        team = cells[1] if len(cells) > 1 else ""
        gp = to_int(cells[2]) if len(cells) > 2 else None

        # Points often near the end; try to detect last int as PTS
        ints = [to_int(c) for c in cells]
        ints_clean = [i for i in ints if i is not None]
        pts = ints_clean[-2] if len(ints_clean) >= 2 else (ints_clean[-1] if ints_clean else None)

        row["pos"] = pos
        row["team"] = team
        row["gp"] = gp
        row["pts"] = pts
        row["raw"] = cells  # keep raw for resilience

        # keep only rows with a team name + pos
        if row["team"] and row["pos"] is not None:
            rows.append(row)

    # Sort by position if available
    rows.sort(key=lambda r: (r["pos"] if r["pos"] is not None else 999))

    caps_row = None
    for r in rows:
        t = (r.get("team") or "").lower()
        if "cap" in t:  # covers "Caps" / "Capitals"
            caps_row = r
            break

    return rows, caps_row


def format_weekly_post(rows: List[Dict], caps_row: Optional[Dict], week_key: str) -> str:
    now = datetime.now(UK_TZ)
    title = f"🏒 **SNL League Table — {week_key}**\n_As of {now:%a %d %b %Y, %H:%M} (UK time)_\n"

    if not rows:
        return title + "\nNo table data detected this week."

    # Highlight Capitals
    if caps_row:
        title += f"\n🟢 **Edinburgh Capitals:** Position **{caps_row.get('pos')}** — **{caps_row.get('pts')} pts** (GP {caps_row.get('gp')})\n"
    else:
        title += "\n🟡 **Edinburgh Capitals:** Not detected in the table this week.\n"

    # Print top 10 (or all if fewer)
    lines = []
    for r in rows[:10]:
        pos = r.get("pos")
        team = r.get("team")
        pts = r.get("pts")
        gp = r.get("gp")
        marker = "✅" if caps_row and r.get("team") == caps_row.get("team") else "•"
        lines.append(f"{marker} {pos}. {team} — {pts} pts (GP {gp})")

    return title + "\n" + "\n".join(lines)


# ----------------------------
# Posting control (weekly)
# ----------------------------
def should_post_now(now: datetime) -> bool:
    # Only during Monday 18:00–18:10 UK time
    if now.weekday() != POST_DAY:
        return False
    if now.hour != POST_HOUR:
        return False
    if not (0 <= now.minute <= POST_MINUTE_MAX):
        return False
    return True


def main() -> None:
    now = datetime.now(UK_TZ)
    if not should_post_now(now):
        logging.info("Not within weekly post window.")
        return

    week_key = current_iso_week_key(now)
    state = load_state()

    if state.get("last_posted_week") == week_key:
        logging.info("Already posted this week.")
        return

    try:
        rows, caps_row = scrape_snl_table()
        msg = format_weekly_post(rows, caps_row, week_key)
        post_to_discord(msg)
    except Exception as e:
        logging.error(f"Weekly table bot failed: {e}")
        raise

    state["last_posted_week"] = week_key
    save_state(state)
    logging.info("Posted weekly league table successfully.")


if __name__ == "__main__":
    main()
