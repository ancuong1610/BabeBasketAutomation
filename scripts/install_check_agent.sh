#!/bin/zsh
# Install (or reinstall) the per-minute `check` LaunchAgent in the GUI session.
#   scripts/install_check_agent.sh          install / reload
#   scripts/install_check_agent.sh remove   uninstall
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.babebasket.check
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = remove ]; then rm -f "$DEST"; echo "removed $LABEL"; exit 0; fi
mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/data/logs"
sed "s|__ROOT__|$ROOT|g" "$ROOT/scripts/com.babebasket.check.plist" > "$DEST"
plutil -lint "$DEST"
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "installed $LABEL -> $DEST"
