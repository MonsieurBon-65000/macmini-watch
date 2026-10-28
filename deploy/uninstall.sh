#!/bin/sh
# Retire macmini-watch: stop and remove both LaunchAgents on this Mac.
#
# Usage (on the Mac Mini):
#   cd ~/src/macmini-watch
#   ./deploy/uninstall.sh
#
# Idempotent. Removes the watcher (60s loop) and the daily heartbeat so
# neither comes back at next login. Leaves the repo, state.json, logs, and
# ~/.config/macmini-watch/env in place — delete those by hand if wanted.
# To bring it back, run ./deploy/install.sh.

set -eu

LABELS="net.trailhead.macmini-watch net.trailhead.macmini-watch.heartbeat"
GUI_TARGET="gui/$(id -u)"

for LABEL in $LABELS; do
    DST_PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
    if launchctl print "${GUI_TARGET}/${LABEL}" >/dev/null 2>&1; then
        launchctl bootout "${GUI_TARGET}/${LABEL}" 2>/dev/null || true
        echo "Stopped ${LABEL}"
    fi
    launchctl disable "${GUI_TARGET}/${LABEL}" 2>/dev/null || true
    if [ -f "$DST_PLIST" ]; then
        rm -f "$DST_PLIST"
        echo "Removed $DST_PLIST"
    fi
done

echo
echo "Retired. Confirm nothing is left running:"
echo "  launchctl list | grep macmini-watch   # should print nothing"
