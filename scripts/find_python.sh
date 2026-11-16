# Sourced by run.sh / setup_mac.sh: sets PY to the first Python >= 3.11.
# Override with BABEBASKET_PYTHON=/path/to/python3.
PY=""
for candidate in ${BABEBASKET_PYTHON:-} /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    "$HOME/.local/bin/python3.13" "$HOME/.local/bin/python3.12" "$HOME/.local/bin/python3.11" \
    python3.13 python3.12 python3.11 python3; do
  [ -n "$candidate" ] || continue
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY=$(command -v "$candidate"); break
  fi
done
