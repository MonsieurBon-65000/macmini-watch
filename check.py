#!/usr/bin/env python3
"""
Polls Apple's Certified Refurbished store for matching listings (M4 Mac
mini and, optionally, any Mac Studio) at-or-below configurable price caps.
On a new hit, fans out to any configured destination (Slack webhook,
Telegram bot, or both). Dedupes via state.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

PRICE_CAP = int(os.environ.get("PRICE_CAP", "600"))
# Optional minimum RAM (GB) for the Mac mini watch. Unset/empty disables the filter.
_mini_min_ram_raw = os.environ.get("MINI_MIN_RAM_GB", "").strip()
MINI_MIN_RAM_GB = int(_mini_min_ram_raw) if _mini_min_ram_raw else None
# Mac Studio watch is opt-in: leave STUDIO_PRICE_CAP unset/empty to disable.
_studio_cap_raw = os.environ.get("STUDIO_PRICE_CAP", "").strip()
STUDIO_PRICE_CAP = int(_studio_cap_raw) if _studio_cap_raw else None
STATE_PATH = Path("state.json")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
# Slack user ID(s) to @-mention on every alert (test pings included).
# Comma-separated for multiple. Leave empty to skip mentions.
SLACK_MENTION_USER_IDS = os.environ.get("SLACK_MENTION_USER_IDS", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Safari/605.1.15"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            print(f"[fetch] {url} -> {resp.status} ({len(body)} bytes)", file=sys.stderr)
            return body
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"[fetch] {url} -> {e}", file=sys.stderr)
        return ""


def check_apple_refurb(
    url: str,
    product: str,
    chip_pattern: str | None,
    price_cap: int,
    min_ram_gb: int | None = None,
) -> list[dict]:
    """Scrape one Apple refurb category page for at-or-below-cap listings.

    product is the literal page name ("Mac mini", "Mac Studio") used both
    as a match keyword and in alert text. chip_pattern is an optional
    regex segment (e.g. "M4") that must also appear in the title; pass
    None to match any chip. min_ram_gb optionally filters out listings
    whose title's "NNGB" memory spec falls below the threshold.
    """
    html = fetch(url)
    if not html:
        return []
    product_count = len(re.findall(re.escape(product), html, re.IGNORECASE))
    prices = sorted({p for p in re.findall(r"\$\s*([0-9][0-9,]{2,4}\.\d{2})", html)})
    if chip_pattern:
        chip_count = len(re.findall(rf"\b{chip_pattern}\b", html))
        chip_summary = f", '{chip_pattern}' x{chip_count}"
    else:
        chip_summary = ""
    print(
        f"[apple/{product}] '{product}' x{product_count}{chip_summary}, "
        f"distinct prices: {prices[:15]}{'...' if len(prices) > 15 else ''}",
        file=sys.stderr,
    )
    chip_segment = f"{chip_pattern}[^<]{{0,400}}?" if chip_pattern else ""
    pattern = re.compile(
        rf"(Refurbished[^<]{{0,200}}{re.escape(product)}[^<]{{0,200}}{chip_segment})"
        r"[\s\S]{0,3000}?\$\s*([0-9][0-9,]{2,4})\.\d{2}",
        re.IGNORECASE,
    )
    hits = []
    seen = set()
    for m in pattern.finditer(html):
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
        price = int(m.group(2).replace(",", ""))
        key = (title, price)
        if key in seen:
            continue
        seen.add(key)
        if min_ram_gb is not None:
            ram_matches = [int(g) for g in re.findall(r"(\d+)\s*GB", title, re.IGNORECASE)]
            if not ram_matches or max(ram_matches) < min_ram_gb:
                continue
        if price <= price_cap:
            hits.append(
                {
                    "retailer": "Apple Refurb",
                    "product": product,
                    "variant": title[:140],
                    "price": price,
                    "cap": price_cap,
                    "url": url,
                }
            )
    return hits


def signature(hit: dict) -> str:
    return f"{hit['retailer']}|{hit['variant']}|{hit['price']}"


def post_slack(hit: dict) -> None:
    if not SLACK_WEBHOOK_URL:
        return
    mentions = ""
    if SLACK_MENTION_USER_IDS:
        ids = [u.strip() for u in SLACK_MENTION_USER_IDS.split(",") if u.strip()]
        mentions = " ".join(f"<@{u}>" for u in ids)
        if mentions:
            mentions += " "
    text = (
        f"{mentions}:rotating_light: {hit['product']} ${hit['cap']} hit — {hit['retailer']}\n"
        f"{hit['variant']} at ${hit['price']}\n"
        f"{hit['url']}"
    )
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"[slack] post failed: {e}", file=sys.stderr)


def post_telegram(hit: dict) -> None:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    text = (
        f"\U0001f6a8 {hit['product']} ${hit['cap']} hit — {hit['retailer']}\n"
        f"{hit['variant']} at ${hit['price']}\n"
        f"{hit['url']}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps(
        {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"[telegram] post failed: {e}", file=sys.stderr)


def notify(hit: dict) -> None:
    if not (SLACK_WEBHOOK_URL or (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)):
        print(f"[notify] (dry-run, no destinations configured) would post: {hit}", file=sys.stderr)
        return
    post_slack(hit)
    post_telegram(hit)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def main() -> int:
    if os.environ.get("TEST_PING") == "1":
        print("[test] sending test ping to all configured destinations")
        notify(
            {
                "retailer": "TEST",
                "product": "Apple refurb",
                "variant": "end-to-end notification wiring test",
                "price": 0,
                "cap": 0,
                "url": "https://www.apple.com/shop/refurbished/mac/mac-mini",
            }
        )
        return 0

    watches = [
        ("https://www.apple.com/shop/refurbished/mac/mac-mini", "Mac mini", "M4", PRICE_CAP, MINI_MIN_RAM_GB),
    ]
    if STUDIO_PRICE_CAP is not None:
        watches.append(
            ("https://www.apple.com/shop/refurbished/mac/mac-studio", "Mac Studio", None, STUDIO_PRICE_CAP, None)
        )

    all_hits: list[dict] = []
    for url, product, chip, cap, min_ram in watches:
        try:
            all_hits.extend(check_apple_refurb(url, product, chip, cap, min_ram))
        except Exception as e:
            print(f"[check_apple_refurb {product}] error: {e}", file=sys.stderr)

    print(f"hits this run: {len(all_hits)}")
    for h in all_hits:
        print(f"  - {signature(h)} -> {h['url']}")

    previous = load_state()
    current = {signature(h): h for h in all_hits}
    new_keys = set(current) - set(previous)

    for key in new_keys:
        print(f"[alert] new hit: {key}")
        notify(current[key])

    save_state(current)
    return 0


if __name__ == "__main__":
    sys.exit(main())
