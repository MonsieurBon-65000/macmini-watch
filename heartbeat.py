#!/usr/bin/env python3
"""
Daily heartbeat for macmini-watch. Fires once a day at 7am via a
separate LaunchAgent (StartCalendarInterval). Confirms the full
notification chain is alive — Mac Mini -> HA webhook + Slack +
Telegram -> phone -- so we'd notice silently-broken plumbing
before missing a real refurb hit.

Reads the same env file as check.py. The watcher's err log mtime
is used as a proxy for "is the regular 60s watcher still alive."
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

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
    return " · ".join(parts) if parts else "(no watches configured)"


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


def post_homeassistant(title: str, body: str, last_run_age: str, watches: str) -> bool:
    if not HEARTBEAT_HA_WEBHOOK_URL:
        return True
    return post_json(
        HEARTBEAT_HA_WEBHOOK_URL,
        {
            "title": title,
            "message": body,
            "last_watcher_run": last_run_age,
            "watches": watches,
        },
        "homeassistant",
    )


def main() -> int:
    now = dt.datetime.now().astimezone()
    timestamp = now.strftime("%Y-%m-%d %H:%M %Z")
    last_run = last_watcher_run_age()
    watches = watches_summary()

    title = "✅ macmini-watch heartbeat"
    body = (
        f"{timestamp}\n"
        f"Last watcher run: {last_run}\n"
        f"Watching: {watches}"
    )
    print(f"[heartbeat] {title}\n{body}", file=sys.stderr)

    if not (SLACK_WEBHOOK_URL or (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) or HEARTBEAT_HA_WEBHOOK_URL):
        print("[heartbeat] no destinations configured; nothing to do.", file=sys.stderr)
        return 0

    results = [
        post_slack(title, body),
        post_telegram(title, body),
        post_homeassistant(title, body, last_run, watches),
    ]
    failed = results.count(False)
    if failed:
        print(f"[heartbeat] {failed} destination(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
