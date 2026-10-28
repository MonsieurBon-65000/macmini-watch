# macmini-watch

> **Retired (2026-10-28).** This watcher is no longer in use. It last ran as a
> launchd LaunchAgent on a Mac Mini (60s poll + a daily 7am heartbeat), not as
> the GitHub Actions cron described below. See "Start / stop (launchd)" for how
> to stop it or bring it back. The rest of this README is kept for historical
> reference.

A tiny GitHub Actions cron that watches Apple's Certified Refurbished store for an **M4 Mac mini** at-or-below a target price and pings a Slack channel when one shows up.

Runs every ~10 minutes, dedupes hits so you don't get spammed, and shuts up entirely when there's no inventory. Roughly 100 lines of Python with no dependencies.

## Start / stop (launchd)

The watcher is two launchd LaunchAgents on the Mac Mini (user `utility1022`):
`net.trailhead.macmini-watch` (60 s poll) and `net.trailhead.macmini-watch.heartbeat`
(daily 07:00). Both scripts below are idempotent and live in `deploy/`.

**Reaching the Mini.** `ssh macmini` uses mDNS (`mac-mini-m4.local`) and only works
on the home LAN; it has failed to resolve from the laptop at times. `ssh macmini-ts`
(Tailscale MagicDNS, `mac-mini-m4-pro.tail3283da.ts.net`) works from anywhere on the
tailnet and is the reliable choice. Substitute whichever resolves.

**Stop (retire):**

```bash
ssh macmini-ts 'cd ~/src/macmini-watch && git pull && sh deploy/uninstall.sh'
```

`uninstall.sh` boots out both agents, disables them so they do not return at login,
and deletes the two plists from `~/Library/LaunchAgents`. It leaves the repo,
`state.json`, the logs, and `~/.config/macmini-watch/env` in place.

**Start (reinstall):**

```bash
ssh macmini-ts 'cd ~/src/macmini-watch && git pull && sh deploy/install.sh'
```

`install.sh` renders the plists, bootstraps and re-enables both agents, and fires a
first watcher tick immediately (`RunAtLoad`). The heartbeat does not fire on install;
it next runs at 07:00. The env file must already exist with real secrets, or the
script stops and tells you to fill it in.

**Verify either way:**

```bash
ssh macmini-ts 'launchctl list | grep macmini-watch; tail -n 3 ~/Library/Logs/macmini-watch.err.log'
```

Running: two `net.trailhead.macmini-watch*` lines and a fresh `[canary] ok` log line.
Stopped: `launchctl list` prints nothing and the log mtime stops advancing.

**Pause without uninstalling** (keeps the plists, resumes at next login or on `load`):

```bash
ssh macmini-ts 'launchctl unload ~/Library/LaunchAgents/net.trailhead.macmini-watch.plist ~/Library/LaunchAgents/net.trailhead.macmini-watch.heartbeat.plist'
```

## How it works

1. `check.py` fetches `https://www.apple.com/shop/refurbished/mac/mac-mini`.
2. Parses the page for listings that mention "Mac mini" + "M4" with a price at-or-below `PRICE_CAP`.
3. Compares against `state.json` (committed back to the repo each run) so duplicate listings only alert once.
4. POSTs new hits to a Slack incoming webhook.

GitHub Actions cron is best-effort and typically lands every 10–20 minutes — fine for refurb watch, not fine if you need sub-minute polling.

## Setup

1. **Fork this repo** (or use it as a template).
2. **Create a Slack incoming webhook**:
   - https://api.slack.com/apps → Create New App → From scratch
   - Activate Incoming Webhooks → Add New Webhook to Workspace → pick the channel
   - Copy the URL (looks like `https://hooks.slack.com/services/...`)
3. **Add the webhook as a repo secret**:
   - Settings → Secrets and variables → Actions → New repository secret
   - Name: `SLACK_WEBHOOK_URL`
   - Value: the webhook URL
4. **(Optional) Tune the price cap**:
   - Edit the `PRICE_CAP` env var in `.github/workflows/macmini.yml`
   - Default is `600`
5. **(Optional) Tag a Slack user on real hits**:
   - Add a repo secret `SLACK_MENTION_USER_IDS` with one or more Slack user IDs (e.g. `U07PCFNRLH3` or `U07PCFNRLH3,U08ABCDE123`).
   - All Slack messages (including test pings) will be prefixed with `<@user>` mentions.
6. The cron starts on its own once pushed.

## Manual test ping

Confirms the webhook is wired up without waiting for inventory.

Actions tab → **Mac Mini stock watch** → **Run workflow** → check **Send a Slack test ping and exit** → Run.

You should see a `:rotating_light: Mac mini $X hit — TEST` message in your Slack channel within a few seconds.

## Local dry-run

```sh
SLACK_WEBHOOK_URL="" python3 check.py
```

Empty webhook = dry-run. The script logs what it would have posted to stderr instead of actually posting.

## Adapting it

This is purpose-built for Apple Refurb Mac mini M4. To watch a different product:

- Change the URL and the regex in `check_apple_refurb()` to match the new page.
- Apple's refurb site is server-rendered HTML, which makes parsing trivial. Other retailers (Amazon, Best Buy) heavily block GitHub Actions IPs with 503s and CAPTCHAs — a previous version of this repo tried and gave up. If you need those, expect to use a third-party scraping service.

## License

MIT — see [LICENSE](LICENSE).
