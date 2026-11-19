#!/bin/zsh
# Entry point for n8n (and cron/launchd): runs one pipeline command, logs it,
# and posts a short alert to Discord if it fails.
#   scripts/run.sh daily | check | scrape | draft | post
set -u
cd "$(dirname "$0")/.." || exit 1

. scripts/find_python.sh
[ -n "$PY" ] || { echo "Python 3.11+ not found (set BABEBASKET_PYTHON)" >&2; exit 1; }

export PYTHONUNBUFFERED=1   # write progress lines to the log as they happen
mkdir -p data/logs data/locks

# One run per command at a time: check runs every minute, and a run that is still making an
# image must not be joined by a second one. A lock left by a crashed run is cleared.
LOCKDIR="data/locks/$1.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  OLD_PID=$(cat "$LOCKDIR/pid" 2>/dev/null)
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    exit 0   # the previous run of this command is still busy: skip quietly
  fi
  rm -rf "$LOCKDIR"
  mkdir "$LOCKDIR" 2>/dev/null || exit 0
fi
echo $$ > "$LOCKDIR/pid"
trap 'rm -rf "$LOCKDIR"' EXIT

LOG="data/logs/$(date +%Y-%m-%d)-$1.log"
{
  echo "=== $(date '+%F %T') $*"
  "$PY" -m babebasket "$@"
} >>"$LOG" 2>&1
STATUS=$?

tail -n 25 "$LOG"
if [ $STATUS -ne 0 ]; then
  "$PY" -m babebasket notify "⚠️ \`$*\` failed (exit $STATUS). Last lines:\n\`\`\`$(tail -n 8 "$LOG" | cut -c1-180)\`\`\`" >/dev/null 2>&1
fi
exit $STATUS
