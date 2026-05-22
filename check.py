#!/usr/bin/env python3
"""
Polls Apple's Certified Refurbished store for matching listings (M4 Mac
mini and, optionally, Mac Studio / MacBook Pro / iMac) at-or-below
configurable price caps. On a new hit, fans out to any configured
destination (Slack webhook, Telegram bot, Home Assistant). Dedupes via
state.json.

Apple now serves all Mac refurb categories from one page (the old
per-category URLs 302-redirect there) and filters client-side; the
listings live in an embedded `"tiles":[…]` JSON blob rather than the
HTML, so we parse that — see fetch_refurb_tiles().
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

PRICE_CAP = int(os.environ.get("PRICE_CAP", "600"))
# Optional minimum RAM (GB) for the Mac mini watch. Unset/empty disables the filter.
_mini_min_ram_raw = os.environ.get("MINI_MIN_RAM_GB", "").strip()
MINI_MIN_RAM_GB = int(_mini_min_ram_raw) if _mini_min_ram_raw else None
# Mac Studio watch is opt-in: leave STUDIO_PRICE_CAP unset/empty to disable.
_studio_cap_raw = os.environ.get("STUDIO_PRICE_CAP", "").strip()
STUDIO_PRICE_CAP = int(_studio_cap_raw) if _studio_cap_raw else None
# Optional minimum RAM (GB) for the Mac Studio watch. Unset/empty disables the filter.
_studio_min_ram_raw = os.environ.get("STUDIO_MIN_RAM_GB", "").strip()
STUDIO_MIN_RAM_GB = int(_studio_min_ram_raw) if _studio_min_ram_raw else None
# iMac watch is opt-in (currently used as a pipeline test since iMacs are reliably in stock).
_imac_cap_raw = os.environ.get("IMAC_PRICE_CAP", "").strip()
IMAC_PRICE_CAP = int(_imac_cap_raw) if _imac_cap_raw else None
# MacBook Pro watch is opt-in via MBP_PRICE_CAP. MBP_MIN_RAM_GB filters titles by
# advertised memory (defaults to 128 GB, which only M3/M4 Max configs reach).
_mbp_cap_raw = os.environ.get("MBP_PRICE_CAP", "").strip()
MBP_PRICE_CAP = int(_mbp_cap_raw) if _mbp_cap_raw else None
_mbp_min_ram_raw = os.environ.get("MBP_MIN_RAM_GB", "128").strip()
MBP_MIN_RAM_GB = int(_mbp_min_ram_raw) if _mbp_min_ram_raw else None
# Per-run alert cap for the iMac watch. Defaults to 2 since iMac is a pipeline-test
# channel that's always in stock — without a cap a fresh state.json fires a flood.
_imac_max_raw = os.environ.get("IMAC_MAX_ALERTS", "2").strip()
IMAC_MAX_ALERTS = int(_imac_max_raw) if _imac_max_raw else None
STATE_PATH = Path("state.json")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
# Slack user ID(s) to @-mention on every alert (test pings included).
# Comma-separated for multiple. Leave empty to skip mentions.
SLACK_MENTION_USER_IDS = os.environ.get("SLACK_MENTION_USER_IDS", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
HA_WEBHOOK_URL = os.environ.get("HA_WEBHOOK_URL", "").strip()

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


# Apple collapsed the per-category refurb URLs (mac-mini, mac-studio, …) into a
# single Mac page that 302-redirects everything here and filters client-side.
# The listings are no longer in the HTML — they live in a `"tiles":[…]` JSON
# blob with clean, stable fields (partNumber, title, price, category, RAM).
REFURB_URL = "https://www.apple.com/shop/refurbished/mac"

# refurbClearModel values Apple uses in each tile's filters.dimensions, mapped
# to the human label we put in alerts.
MODEL_LABELS = {
    "macmini": "Mac mini",
    "macstudio": "Mac Studio",
    "macbookpro": "MacBook Pro",
    "macbookair": "MacBook Air",
    "imac": "iMac",
    "macpro": "Mac Pro",
}


def parse_ram_gb(raw: str | None) -> int | None:
    """'64gb' -> 64. Returns None if unparseable."""
    if not raw:
        return None
    m = re.match(r"(\d+)", raw.strip())
    return int(m.group(1)) if m else None


def fetch_refurb_tiles() -> list[dict]:
    """Fetch the combined Mac refurb page and parse its `tiles` JSON array.

    Returns one normalized dict per listing with keys: partNumber, title,
    model (refurbClearModel), ram_gb, price (int USD), url. Returns [] on
    any fetch/parse failure (logged), so a markup change degrades to "no
    hits" rather than crashing.
    """
    html = fetch(REFURB_URL)
    if not html:
        return []
    i = html.find('"tiles":[')
    if i == -1:
        print("[parse] '\"tiles\":[' not found — Apple markup changed", file=sys.stderr)
        return []
    start = html.index("[", i)
    depth = 0
    end = None
    for j in range(start, len(html)):
        c = html[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if end is None:
        print("[parse] tiles array never closed — markup changed", file=sys.stderr)
        return []
    try:
        raw_tiles = json.loads(html[start:end])
    except json.JSONDecodeError as e:
        print(f"[parse] tiles JSON decode failed: {e}", file=sys.stderr)
        return []

    tiles = []
    for t in raw_tiles:
        dims = (t.get("filters") or {}).get("dimensions") or {}
        price_raw = ((t.get("price") or {}).get("currentPrice") or {}).get("raw_amount")
        if not price_raw:
            continue
        try:
            price = int(round(float(price_raw)))
        except (TypeError, ValueError):
            continue
        pdu = (t.get("productDetailsUrl") or "").split("?")[0]
        url = f"https://www.apple.com{pdu}" if pdu.startswith("/") else (pdu or REFURB_URL)
        tiles.append(
            {
                "partNumber": t.get("partNumber", ""),
                "title": (t.get("title") or "").strip(),
                "model": dims.get("refurbClearModel", ""),
                "ram_gb": parse_ram_gb(dims.get("tsMemorySize")),
                "price": price,
                "url": url,
            }
        )
    print(
        f"[apple] {len(tiles)} listings in stock by model: "
        f"{dict(Counter(t['model'] for t in tiles))}",
        file=sys.stderr,
    )
    return tiles


def select_hits(
    tiles: list[dict],
    model: str,
    product: str,
    price_cap: int,
    chip: str | None = None,
    min_ram_gb: int | None = None,
) -> list[dict]:
    """Filter parsed tiles for one watch: matching category, optional chip
    substring in the title, optional RAM floor, and at-or-below price cap.

    Dedupes identical configs by (title, price) so two in-stock units of the
    same spec yield a single alert.
    """
    matched = [t for t in tiles if t["model"] == model]
    print(f"[apple/{product}] {len(matched)} in stock; cap=${price_cap}", file=sys.stderr)
    hits = []
    seen: set[tuple[str, int]] = set()
    for t in matched:
        if chip and chip.lower() not in t["title"].lower():
            continue
        if min_ram_gb is not None and (t["ram_gb"] is None or t["ram_gb"] < min_ram_gb):
            continue
        if t["price"] > price_cap:
            continue
        key = (t["title"], t["price"])
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            {
                "retailer": "Apple Refurb",
                "product": product,
                "variant": (t["title"] or t["partNumber"])[:140],
                "price": t["price"],
                "cap": price_cap,
                "url": t["url"],
            }
        )
    return hits


def signature(hit: dict) -> str:
    return f"{hit['retailer']}|{hit['variant']}|{hit['price']}"


def _slack_send(text: str) -> None:
    if not SLACK_WEBHOOK_URL:
        return
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


def _telegram_send(text: str) -> None:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
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


def post_slack(hit: dict) -> None:
    mentions = ""
    if SLACK_MENTION_USER_IDS:
        ids = [u.strip() for u in SLACK_MENTION_USER_IDS.split(",") if u.strip()]
        mentions = " ".join(f"<@{u}>" for u in ids)
        if mentions:
            mentions += " "
    _slack_send(
        f"{mentions}:rotating_light: {hit['product']} ${hit['cap']} hit — {hit['retailer']}\n"
        f"{hit['variant']} at ${hit['price']}\n"
        f"{hit['url']}"
    )


def post_telegram(hit: dict) -> None:
    _telegram_send(
        f"\U0001f6a8 {hit['product']} ${hit['cap']} hit — {hit['retailer']}\n"
        f"{hit['variant']} at ${hit['price']}\n"
        f"{hit['url']}"
    )


def post_homeassistant(hit: dict) -> None:
    if not HA_WEBHOOK_URL:
        return
    payload = json.dumps(
        {
            "retailer": hit["retailer"],
            "product": hit["product"],
            "variant": hit["variant"],
            "price": hit["price"],
            "cap": hit["cap"],
            "url": hit["url"],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        HA_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"[homeassistant] post failed: {e}", file=sys.stderr)


def notify(hit: dict) -> None:
    if not (SLACK_WEBHOOK_URL or (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) or HA_WEBHOOK_URL):
        print(f"[notify] (dry-run, no destinations configured) would post: {hit}", file=sys.stderr)
        return
    post_slack(hit)
    post_telegram(hit)
    post_homeassistant(hit)


# Sentinel signature for the pipeline-health canary; persisted in state.json so a
# breakage alerts only once (on the transition into broken), not every 60s run.
CANARY_SIG = "CANARY|imac-retrieval|0"


def notify_canary_broken(message: str) -> None:
    """The iMac canary failed (0 iMacs parsed though they're always in stock),
    so refurb detection is silently dead. Posts to Slack + Telegram only — no
    critical iOS push (the 7am heartbeat's retrieval probe covers the phone)."""
    text = (
        f"⚠️ macmini-watch CANARY BROKEN — {message}\n"
        f"0 iMacs parsed, but iMacs are always in stock, so the fetch/parse "
        f"pipeline is likely dead and real Mac alerts won't fire. "
        f"Check the err log for [parse]/[fetch] lines.\n"
        f"https://www.apple.com/shop/refurbished/mac"
    )
    _slack_send(text)
    _telegram_send(text)


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

    # Each watch tuple: (model, product, chip, price_cap, min_ram_gb, max_alerts_per_run)
    # model is Apple's refurbClearModel key; chip is an optional title substring
    # ("M4"); max_alerts_per_run = None means "alert on every new hit".
    watches = [
        ("macmini", "Mac mini", "M4", PRICE_CAP, MINI_MIN_RAM_GB, None),
    ]
    if STUDIO_PRICE_CAP is not None:
        watches.append(("macstudio", "Mac Studio", None, STUDIO_PRICE_CAP, STUDIO_MIN_RAM_GB, None))
    # NOTE: iMac is intentionally NOT an alerting watch. It serves only as the
    # pipeline-health canary (see canary check below) — silent unless detection
    # breaks. IMAC_PRICE_CAP / IMAC_MAX_ALERTS are retained but unused.
    if MBP_PRICE_CAP is not None:
        watches.append(("macbookpro", "MacBook Pro", None, MBP_PRICE_CAP, MBP_MIN_RAM_GB, None))

    # All categories now live on one page, so fetch + parse once and filter per watch.
    tiles = fetch_refurb_tiles()

    per_watch_hits: list[tuple[str, int | None, list[dict]]] = []
    for model, product, chip, cap, min_ram, max_alerts in watches:
        try:
            hits = select_hits(tiles, model, product, cap, chip, min_ram)
        except Exception as e:
            print(f"[select_hits {product}] error: {e}", file=sys.stderr)
            hits = []
        per_watch_hits.append((product, max_alerts, hits))

    all_hits = [h for _, _, hits in per_watch_hits for h in hits]
    print(f"hits this run: {len(all_hits)}")
    for h in all_hits:
        print(f"  - {signature(h)} -> {h['url']}")

    # Canary: iMacs are reliably in stock, so 0 parsed means the fetch/parse
    # pipeline silently broke. Stays silent while healthy; alerts once per breakage.
    imac_count = sum(1 for t in tiles if t["model"] == "imac")
    canary_broken = imac_count == 0

    previous = load_state()
    current = {signature(h): h for h in all_hits}
    if canary_broken:
        current[CANARY_SIG] = {"broken": True, "total_tiles": len(tiles)}

    # Save state BEFORE notifying so a crash in notify doesn't cause re-alerts next run.
    save_state(current)

    fired = 0
    for product, max_alerts, hits in per_watch_hits:
        new_hits = [h for h in hits if signature(h) not in previous]
        if max_alerts is not None and len(new_hits) > max_alerts:
            print(
                f"[notify-cap] {product}: {len(new_hits)} new hits, capped to {max_alerts}",
                file=sys.stderr,
            )
            new_hits = new_hits[:max_alerts]
        for h in new_hits:
            print(f"[alert] new hit: {signature(h)}")
            notify(h)
            fired += 1

    # Fire the canary alert only on the transition into broken (sentinel absent
    # from previous state); recovery silently clears it for the next breakage.
    if canary_broken:
        msg = f"0 iMacs parsed ({len(tiles)} total tiles)"
        if CANARY_SIG not in previous:
            print(f"[canary] BROKEN — {msg}; alerting", file=sys.stderr)
            notify_canary_broken(msg)
            fired += 1
        else:
            print(f"[canary] still broken — {msg}; already alerted", file=sys.stderr)
    else:
        print(f"[canary] ok — {imac_count} iMacs in stock", file=sys.stderr)

    print(f"alerts fired this run: {fired}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
