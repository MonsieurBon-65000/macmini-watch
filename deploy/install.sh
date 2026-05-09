#!/bin/sh
# Install the macmini-watch LaunchAgent on this Mac.
#
# Usage:
#   cd ~/src/macmini-watch
#   ./deploy/install.sh
#
# Idempotent: re-running re-bootstraps the agent with whatever changed in
# deploy/net.trailhead.macmini-watch.plist. Run after a code-only `git pull`
# only if the plist itself changed; otherwise `launchctl kickstart` (see
# README) is enough to restart the next tick under new code.

set -eu

LABEL="net.trailhead.macmini-watch"
SRC_PLIST="$(cd "$(dirname "$0")" && pwd)/${LABEL}.plist"
DST_PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
ENV_DIR="$HOME/.config/macmini-watch"
ENV_FILE="$ENV_DIR/env"
LOG_DIR="$HOME/Library/Logs"
GUI_TARGET="gui/$(id -u)"

# 1. Sanity check: the repo's check.py must exist where the plist expects it.
if [ ! -f "$HOME/src/macmini-watch/check.py" ]; then
    echo "error: $HOME/src/macmini-watch/check.py not found." >&2
    echo "       Clone the fork to ~/src/macmini-watch first:" >&2
    echo "       mkdir -p ~/src && git clone <your fork URL> ~/src/macmini-watch" >&2
    exit 1
fi

# 2. Env file: create from template if missing, refuse to clobber if present.
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
    cp "$(dirname "$0")/env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Created $ENV_FILE from template."
    echo "Edit it now with your real SLACK_WEBHOOK_URL before continuing."
    echo "Then re-run this script."
    exit 0
fi
chmod 600 "$ENV_FILE"

# Refuse to install if the env file is still the unedited template.
if grep -q "REPLACE/ME/PLEASE" "$ENV_FILE"; then
    echo "error: $ENV_FILE still contains the placeholder webhook URL." >&2
    echo "       Edit it with your real SLACK_WEBHOOK_URL, then re-run." >&2
    exit 1
fi

# 3. Logs.
mkdir -p "$LOG_DIR"

# 4. Plist: render __USER_HOME__ -> $HOME and write into place.
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__USER_HOME__|${HOME}|g" "$SRC_PLIST" > "$DST_PLIST"
plutil -lint "$DST_PLIST" >/dev/null
echo "Installed plist to $DST_PLIST"

# 5. (Re-)bootstrap the agent. bootout first so we pick up plist edits cleanly.
if launchctl print "${GUI_TARGET}/${LABEL}" >/dev/null 2>&1; then
    launchctl bootout "${GUI_TARGET}/${LABEL}" 2>/dev/null || true
fi
launchctl bootstrap "$GUI_TARGET" "$DST_PLIST"
launchctl enable "${GUI_TARGET}/${LABEL}"

echo
echo "Installed. RunAtLoad=true means a first tick just fired."
echo "  tail -n 50 $LOG_DIR/macmini-watch.err.log   # see fetch logs"
echo "  launchctl print ${GUI_TARGET}/${LABEL} | grep -E 'state|last exit'"
