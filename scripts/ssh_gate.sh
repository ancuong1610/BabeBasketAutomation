#!/bin/zsh
# Forced command for the n8n SSH key (see ~/.ssh/authorized_keys).
# The key can only start these pipeline commands, nothing else.
# n8n's SSH node sends "cd '/' 1> /dev/null 2> /dev/null; daily", so keep only the last part.
cmd="${SSH_ORIGINAL_COMMAND:-}"
cmd="${cmd##*; }"
case "$cmd" in
  daily|check) exec "$(dirname "$0")/run.sh" "$cmd" ;;
  *) echo "babebasket gate: only 'daily' or 'check' allowed" >&2; exit 2 ;;
esac
