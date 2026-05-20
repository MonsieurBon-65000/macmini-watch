#!/usr/bin/env python3
"""
Daily heartbeat for macmini-watch. Fires once a day at 7am via a
separate LaunchAgent (StartCalendarInterval). Confirms the full
notification chain is alive — Mac Mini -> HA webhook + Slack +
Telegram -> phone -- so we'd notice silently-broken plumbing
before missing a real refurb hit.

Reads the same env file as check.py. Two independent health signals:
  1. The watcher's err log mtime — proxy for "is the 60s watcher process
     still alive."
  2. A live retrieval probe — actually runs check.fetch_refurb_tiles() and
     confirms it parses listings. This is the signal the notification chain
     alone CANNOT give us: when Apple changes their markup the watcher keeps
     running and notifying fine, but parses 0 listings and silently stops
     detecting anything. The heartbeat now catches that.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Make `import check` work regardless of the heartbeat's cwd (launchd runs it
# from /). check.py lives next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
SLACK_MENTION_USER_IDS = os.environ.get("SLACK_MENTION_USER_IDS", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
HEARTBEAT_HA_WEBHOOK_URL = os.environ.get("HEARTBEAT_HA_WEBHOOK_URL", "").strip()

WATCHER_ERR_LOG = Path(os.path.expanduser("~/Library/Logs/macmini-watch.err.log"))


def humanize_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m ago"
    return f"{int(seconds // 86400)}d ago"


def last_watcher_run_age() -> str:
    if not WATCHER_ERR_LOG.exists():
        return "unknown (watcher err log missing)"
    age = time.time() - WATCHER_ERR_LOG.stat().st_mtime
    return humanize_age(age)


def watches_summary() -> str:
    parts = []
    price_cap = os.environ.get("PRICE_CAP", "").strip()
    min_ram = os.environ.get("MINI_MIN_RAM_GB", "").strip()
    if price_cap:
        mini = "Mac mini M4"
        if min_ram:
            mini += f" ≥{min_ram}GB"
        mini += f" ≤${price_cap}"
        parts.append(mini)
    studio_cap = os.environ.get("STUDIO_PRICE_CAP", "").strip()
    if studio_cap:
        parts.append(f"Mac Studio ≤${studio_cap}")
    imac_cap = os.environ.get("IMAC_PRICE_CAP", "").strip()
    if imac_cap:
        parts.append(f"iMac ≤${imac_cap}")
    mbp_cap = os.environ.get("MBP_PRICE_CAP", "").strip()
    if mbp_cap:
        mbp = "MacBook Pro"
        mbp_ram = os.environ.get("MBP_MIN_RAM_GB", "128").strip()
        if mbp_ram:
            mbp += f" ≥{mbp_ram}GB"
        mbp += f" ≤${mbp_cap}"
        parts.append(mbp)
    return " · ".join(parts) if parts else "(no watches configured)"


def retrieval_health() -> tuple[bool, str]:
    """Exercise the real scrape/parse path so the heartbeat detects a broken
    retrieval side (e.g. Apple markup change), not just a dead process.

    Returns (ok, summary). ok is False if the fetch/parse raises or yields
    zero listings — Apple's full Mac refurb catalog being genuinely empty is
    near-impossible, so 0 listings means the parser stopped finding the
    `tiles` JSON.
    """
    try:
        import check

        tiles = check.fetch_refurb_tiles()
    except Exception as e:
        return False, f"BROKEN — probe errored: {e}"
    if not tiles:
        return False, "BROKEN — 0 listings parsed (Apple markup changed?)"
    by_model = collections.Counter(t["model"] for t in tiles)
    breakdown = ", ".join(f"{k}:{v}" for k, v in sorted(by_model.items()))
    return True, f"OK — {len(tiles)} listings parsed ({breakdown})"


def post_json(url: str, payload: dict, label: str) -> bool:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"[{label}] post failed: {e}", file=sys.stderr)
        return False


def post_slack(title: str, body: str) -> bool:
    if not SLACK_WEBHOOK_URL:
        return True
    mentions = ""
    if SLACK_MENTION_USER_IDS:
        ids = [u.strip() for u in SLACK_MENTION_USER_IDS.split(",") if u.strip()]
        mentions = " ".join(f"<@{u}>" for u in ids)
        if mentions:
            mentions += " "
    text = f"{mentions}{title}\n{body}"
    return post_json(SLACK_WEBHOOK_URL, {"text": text}, "slack")


def post_telegram(title: str, body: str) -> bool:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return True
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    return post_json(
        url,
        {"chat_id": TELEGRAM_CHAT_ID, "text": f"{title}\n{body}", "disable_web_page_preview": True},
        "telegram",
    )


def post_homeassistant(
    title: str, body: str, last_run_age: str, watches: str, retrieval_ok: bool, retrieval: str
) -> bool:
    if not HEARTBEAT_HA_WEBHOOK_URL:
        return True
    return post_json(
        HEARTBEAT_HA_WEBHOOK_URL,
        {
            "title": title,
            "message": body,
            "last_watcher_run": last_run_age,
            "watches": watches,
            "retrieval_ok": retrieval_ok,
            "retrieval": retrieval,
        },
        "homeassistant",
    )


def main() -> int:
    now = dt.datetime.now().astimezone()
    timestamp = now.strftime("%Y-%m-%d %H:%M %Z")
    last_run = last_watcher_run_age()
    watches = watches_summary()
    retrieval_ok, retrieval = retrieval_health()

    title = "✅ macmini-watch heartbeat" if retrieval_ok else "⚠️ macmini-watch — RETRIEVAL BROKEN"
    body = (
        f"{timestamp}\n"
        f"Last watcher run: {last_run}\n"
        f"Retrieval: {retrieval}\n"
        f"Watching: {watches}"
    )
    print(f"[heartbeat] {title}\n{body}", file=sys.stderr)

    if not (SLACK_WEBHOOK_URL or (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) or HEARTBEAT_HA_WEBHOOK_URL):
        print("[heartbeat] no destinations configured; nothing to do.", file=sys.stderr)
        return 0

    results = [
        post_slack(title, body),
        post_telegram(title, body),
        post_homeassistant(title, body, last_run, watches, retrieval_ok, retrieval),
    ]
    failed = results.count(False)
    if failed:
        print(f"[heartbeat] {failed} destination(s) failed", file=sys.stderr)
        return 1
    # Non-zero exit also if retrieval is broken, so the failure is visible in
    # logs / launchd status even if every notification destination succeeded.
    return 0 if retrieval_ok else 2


if __name__ == "__main__":
    sys.exit(main())
