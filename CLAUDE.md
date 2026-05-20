# macmini-watch — Project Notes

Scraper that polls Apple's Certified Refurbished store for matching Macs and fans out alerts. Aaron's fork of [`brosePR/macmini-watch`](https://github.com/brosePR/macmini-watch), now at [`MonsieurBon-65000/macmini-watch`](https://github.com/MonsieurBon-65000/macmini-watch).

## Deployment

Runs as a launchd LaunchAgent on Aaron's Mac Mini (hostname `mac-mini-m4`, LAN `mac-mini-m4.local`). SSH alias on Aaron's laptop: `ssh macmini` (resolves to user `utility1022`).

**Mac Mini account is `utility1022`, NOT `aarongood`.** Everything lives under `/Users/utility1022/...`:

- Repo clone: `/Users/utility1022/src/macmini-watch`
- LaunchAgent plist: `/Users/utility1022/Library/LaunchAgents/net.trailhead.macmini-watch.plist`
- Secrets env file: `/Users/utility1022/.config/macmini-watch/env` (chmod 600)
- Logs: `/Users/utility1022/Library/Logs/macmini-watch.{out,err}.log`
- Dedupe state: `/Users/utility1022/src/macmini-watch/state.json`

The plist's `StandardOutPath` / `StandardErrorPath` can't expand `$HOME` (launchd doesn't shell-expand them), so the repo stores them as `__USER_HOME__/...` and `deploy/install.sh` sed-substitutes the installing user's `$HOME` at install time.

**Poll interval: 60s** (was 10 min; reduced 2026-05-15). Apple has not (yet) rate-limited the IP at this cadence — keep an eye on `[fetch]` log lines for non-200 responses.

## Daily heartbeat (separate LaunchAgent)

A second LaunchAgent fires `heartbeat.py` once a day at **07:00 local time** to prove the whole notification chain (Mac Mini → HA webhook → Slack + Telegram + iPhone) is alive. If you ever stop getting a 7am heartbeat, something is broken — start debugging immediately rather than waiting weeks for a missing refurb alert to tip you off.

- LaunchAgent plist: `/Users/utility1022/Library/LaunchAgents/net.trailhead.macmini-watch.heartbeat.plist`
- Logs: `/Users/utility1022/Library/Logs/macmini-watch.heartbeat.{out,err}.log`
- Schedule: `StartCalendarInterval` Hour=7 Minute=0. **No `RunAtLoad`** — installing the agent does NOT fire a spurious heartbeat. To test on demand, run `/usr/bin/python3 ~/src/macmini-watch/heartbeat.py` directly on the Mac Mini.
- Reads the same `~/.config/macmini-watch/env` as the watcher; the only new env var it needs is `HEARTBEAT_HA_WEBHOOK_URL` (separate from the alert webhook so HA can route to a heartbeat-specific automation with `✅` instead of `🚨`).
- HA automation: alias `Mac refurb heartbeat`, separate webhook id from the alert path. Sends critical iOS push (user explicitly asked for critical, even though it weakens the "critical = real refurb" signal).
- "Last watcher run" age in the heartbeat body is computed from `~/Library/Logs/macmini-watch.err.log` mtime — the 60s watcher writes `[fetch]` lines on every run, so a stale mtime means the watcher LaunchAgent died.

## Reload sequence after a code change

```bash
ssh macmini 'cd ~/src/macmini-watch && git pull && \
  launchctl unload ~/Library/LaunchAgents/net.trailhead.macmini-watch.plist && \
  launchctl load ~/Library/LaunchAgents/net.trailhead.macmini-watch.plist'
```

If you change the LaunchAgent plist itself, the unload/load above is required to apply it. If you only change `check.py`, unload/load is still cleanest since launchd may have a long-lived child process.

## Env vars (`~/.config/macmini-watch/env` on Mac Mini)

| Var | Purpose |
| --- | --- |
| `PRICE_CAP` | Mac mini price ceiling in USD (required; default 600 in code) |
| `MINI_MIN_RAM_GB` | Optional — filter Mac mini hits to titles showing ≥ NN GB RAM. Unset = no filter |
| `STUDIO_PRICE_CAP` | Enables Mac Studio watch at this cap. Unset/empty = disabled |
| `IMAC_PRICE_CAP` | Enables iMac watch at this cap. Used as a pipeline-test channel (iMacs always in stock) |
| `IMAC_MAX_ALERTS` | Per-run alert cap for the iMac watch. Defaults to **2** — set higher only when intentionally testing burst behavior |
| `MBP_PRICE_CAP` | Enables MacBook Pro watch at this cap. Unset/empty = disabled |
| `MBP_MIN_RAM_GB` | Min RAM (GB) filter for the MacBook Pro watch. Defaults to **128** (only M3/M4 Max reach this). Set empty to disable the floor |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook |
| `SLACK_MENTION_USER_IDS` | Optional comma-separated Slack user IDs to `@`-mention |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram bot destination (both required to enable) |
| `HA_WEBHOOK_URL` | Home Assistant webhook URL for critical iOS push (see below) |
| `HEARTBEAT_HA_WEBHOOK_URL` | HA webhook for the daily heartbeat (different from `HA_WEBHOOK_URL` so HA can route to a separate automation with heartbeat-specific formatting) |

**Watch out:** the env file historically did NOT end with a trailing newline, so naive `echo >> env` will concatenate onto the last line. Always check the file after appending.

## Notification fan-out

`check.py` calls every configured destination on each new hit:
- Slack (via `SLACK_WEBHOOK_URL`)
- Telegram (requires both bot token AND chat id)
- Home Assistant webhook (via `HA_WEBHOOK_URL`)

Dedupe is signature-based (`retailer|variant|price`) stored in `state.json` next to `check.py`. New listings alert once; existing ones stay silent until they drop out and reappear.

## Home Assistant integration

The script POSTs JSON (`retailer`, `product`, `variant`, `price`, `cap`, `url`) to a local-only HA webhook. A HA automation receives it and sends an iOS-critical push to Aaron's iPhone 14.

- HA box: `homeassistant.local` (192.168.68.88)
- HA config share: `smb://homeassistant.local/config` (mount at `/Volumes/config`)
- HA automations file: `/Volumes/config/automations.yaml` (HA's `automation:` integration uses this)
- Aaron's iPhone notify service: **`notify.mobile_app_aaron_iphone`**
  - Device id (older config_entry): `9dbb329938e763895a25778dd0ebd95a` (entry `01J976DBN3BBJFG7F5XQ3TMDMF`)
  - Device id (newer config_entry): `a85100f6fd2a7fa6c9fd948c16e1b102` (entry `01JF802KWKWYGCA6NS9XM4N46R`)
  - Both devices share the **device-registry name "Aaron iPhone"** even though one config-entry is titled "Aaron iPhone 14". The notify slug is derived from `device_registry.name`, not the config-entry title — so it's `aaron_iphone`, NOT `aaron_iphone_14`. We learned this the hard way: the initial automation pointed at `_aaron_iphone_14` and HA silently logged `Action notify.mobile_app_aaron_iphone_14 not found` on every fire.
  - If both registrations are simultaneously active, HA would expose the second as `mobile_app_aaron_iphone_2`. Currently we just use `_aaron_iphone` — if alerts mysteriously stop reaching the right phone, check Settings → Devices & Services → Mobile App for stale registrations to delete.
- Automation alias: `Mac refurb critical alert`, id `macmini_watch_critical_alert`
- Webhook id: `macmini_watch_alert_N59Csqk_XimmbxSrRCBhypArI9Cy3vy2hC1GQ40WEzA` (local-only)
- Webhook URL the script posts to: `http://192.168.68.88:8123/api/webhook/macmini_watch_alert_N59Csqk_XimmbxSrRCBhypArI9Cy3vy2hC1GQ40WEzA`

**iOS critical alert payload** (in the automation's notify action):
```yaml
data:
  url: "{{ trigger.json.url }}"   # tapping the notification opens the Apple URL
  push:
    sound:
      name: default
      critical: 1
      volume: 1.0
    interruption-level: critical
```

This bypasses Focus modes and silent mode. The iPhone must have granted the HA Companion app the **Critical Alerts** entitlement (Settings → Notifications → Home Assistant → Critical Alerts).

After editing `automations.yaml`, **reload automations** from HA (Developer Tools → YAML → "Reload Automations", or full restart). Plain file edits are not picked up live.

Backups: HA writes timestamped `.bak.*` files alongside `automations.yaml` automatically. A manual pre-edit backup was saved as `automations.yaml.bak.macmini-watch-pre` before adding our automation.

## Scraper architecture (rewritten 2026-05-20)

Apple **collapsed the per-category refurb URLs** (`/mac/mac-mini`, `/mac/mac-studio`, `/mac/macbook-pro`, `/mac/imac`) — they all now **302-redirect to `/shop/refurbished/mac`**, a single SPA that filters client-side. The old HTML-regex scraper silently broke: every category URL returned the same combined page (iMac-heavy), so it matched nothing for mini/studio/MBP. The daily heartbeat kept the *notification chain* green while *detection* was dead — exactly the failure mode the heartbeat can't catch.

**Current approach (`fetch_refurb_tiles` + `select_hits` in `check.py`):**
- Fetch `https://www.apple.com/shop/refurbished/mac` **once per run**, then filter per watch (all categories live on that one page now).
- Listings are NOT in the HTML — they're in an embedded `"tiles":[…]` JSON array. We bracket-match that array and `json.loads` it. Each tile gives clean, stable fields:
  - `partNumber` (e.g. `FWUE3LL/A`) — stable unit ID
  - `title` — real product copy ("Refurbished 24-inch iMac Apple M4 Chip…")
  - `productDetailsUrl` — we prepend `https://www.apple.com` and strip the `?fnode=…` query
  - `filters.dimensions.refurbClearModel` — category key (`macmini`, `macstudio`, `macbookpro`, `imac`, `macpro`, `macbookair`, `display`)
  - `filters.dimensions.tsMemorySize` — RAM (`"64gb"` → parsed to int via `parse_ram_gb`)
  - `price.currentPrice.raw_amount` — current sale price
- If the `"tiles":[` marker is missing or JSON fails to parse, we log `[parse] …` and return `[]` (degrade to "no hits", don't crash). **If you ever see `[parse]` lines in the err log, Apple changed the markup again — that's your signal detection is down.**

**Watches** are now `(model, product, chip, price_cap, min_ram_gb, max_alerts)` tuples keyed on `refurbClearModel`. `chip` is an optional case-insensitive title substring (Mac mini uses `"M4"`).

## Known quirks / debugging notes

- **Signatures are now stable** — `(retailer, variant, price)` where `variant` is the clean JSON `title`. The old slug/JSON-leakage instability is gone. Dedup within a run is by `(title, price)`, so two in-stock units of the same config yield one alert.
- **State is saved before notifications fire.** Notify failures (e.g. HA down) won't cause a re-alert storm on the next run.
- **Belt-and-suspenders:** each watch tuple carries a `max_alerts_per_run` cap (6th element). iMac defaults to 2 via `IMAC_MAX_ALERTS`. Mac mini / Studio / MBP are uncapped — re-evaluate if any ever bulk-floods (e.g. enabling iMac uncapped on a fresh `state.json` would fire ~49 alerts).
- **Mac mini and Mac Studio are frequently out of stock** (both showed 0 listings on 2026-05-20 while iMac had 71). Zero hits is normal, not a bug — verify by checking the `[apple] N listings in stock by model: {…}` log line, which prints the live category breakdown every run.
- The `iMac` watch (opt-in via `IMAC_PRICE_CAP`) doubles as a pipeline-health canary since iMacs are reliably in stock.

## Reference

- Repo: https://github.com/MonsieurBon-65000/macmini-watch
- Upstream: https://github.com/brosePR/macmini-watch
- Apple refurb page (single combined page; per-category URLs 302-redirect here): https://www.apple.com/shop/refurbished/mac
  - Per-category links still work in a browser as client-side filters but are NOT separate documents for scraping.
