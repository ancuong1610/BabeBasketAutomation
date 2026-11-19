#!/bin/zsh
# Entry point for n8n (and cron/launchd): runs one pipeline command, logs it,
# and posts a short alert to Discord if it fails.
#   scripts/run.sh daily | check | scrape | draft | post
set -u
cd "$(dirname "$0")/.." || exit 1

. scripts/find_python.sh
[ -n "$PY" ] || { echo "Python 3.11+ not found (set BABEBASKET_PYTHON)" >&2; exit 1; }

export PYTHONUNBUFFERED=1   # write progress lines to the log as they happen
mkdir -p data/logs
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
