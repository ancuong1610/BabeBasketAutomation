#!/bin/zsh
# Entry point for n8n (and cron/launchd): runs one pipeline command, logs it,
# and posts a short alert to Discord if it fails.
#   scripts/run.sh daily | check | scrape | draft | post
set -u
cd "$(dirname "$0")/.." || exit 1

PY=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || { echo "python3 not found" >&2; exit 1; }

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
