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
| `SLACK_WEBHOOK_URL` | Slack incoming webhook |
| `SLACK_MENTION_USER_IDS` | Optional comma-separated Slack user IDs to `@`-mention |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram bot destination (both required to enable) |
| `HA_WEBHOOK_URL` | Home Assistant webhook URL for critical iOS push (see below) |

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
- Aaron's iPhone notify service: **`notify.mobile_app_aaron_iphone_14`**
  - Device id in HA: `9dbb329938e763895a25778dd0ebd95a`
  - mobile_app config_entry id: `01J976DBN3BBJFG7F5XQ3TMDMF`
  - Slug derived from device name "Aaron iPhone 14"
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

## Known quirks / debugging notes

- **`state.json` was empty for a long time despite Mac mini and Studio pages returning 200.** The "distinct prices" log line shows prices like $1,099–$1,899 — those don't appear to be true product-row prices but a sidebar/promo list. The regex in `check_apple_refurb` is fragile against Apple's markup; if alerts ever go quiet for weeks, suspect the regex first, not stock availability.
- The Mac Studio fetch returns the same byte count as Mac mini in some runs (e.g. both at 653,111 bytes). That suggests Apple may be A/B-serving identical content for both URLs under some conditions, or there's redirect/caching weirdness. Worth re-checking if Studio hits seem off.
- The `iMac` watch was added explicitly because iMacs are reliably in stock, so it doubles as a pipeline-health canary.

## Reference

- Repo: https://github.com/MonsieurBon-65000/macmini-watch
- Upstream: https://github.com/brosePR/macmini-watch
- Apple refurb pages:
  - Mac mini: https://www.apple.com/shop/refurbished/mac/mac-mini
  - Mac Studio: https://www.apple.com/shop/refurbished/mac/mac-studio
  - iMac: https://www.apple.com/shop/refurbished/mac/imac
