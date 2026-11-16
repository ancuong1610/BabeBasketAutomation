#!/bin/zsh
# One-time setup check on the Mac mini. Safe to run again.
set -e
cd "$(dirname "$0")/.."

. scripts/find_python.sh
[ -n "$PY" ] || { echo "Python 3.11+ not found. Install: brew install python (or uv python install 3.11)"; exit 1; }
echo "Python: $PY ($($PY --version))"
$PY -c 'import sys; assert sys.version_info >= (3, 11), "need Python 3.11+ (brew install python)"'

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Fill in OPENROUTER_API_KEY and DISCORD_BOT_TOKEN, then run this again."
  open -e .env 2>/dev/null || true
  exit 0
fi
grep -q 'sk-or-\.\.\.' .env && { echo "Fill in OPENROUTER_API_KEY in .env first."; exit 1; }

chmod +x scripts/run.sh
echo "\n== Tests"; $PY -m unittest -q
echo "\n== dm + LLM (one product, nothing is sent)"; $PY -m babebasket try dm 3050111 | head -40
echo "\n== Discord"; $PY -m babebasket notify "✅ Mac mini setup check: the bot can post here."
echo "\nAll good. Now import n8n/daily.json and n8n/check.json into n8n."
