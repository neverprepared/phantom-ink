#!/bin/sh
# PostToolUse hook — sets the memory-checked flag after memory_search completes.
# This unlocks the next WebSearch/WebFetch for this session.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

TOOL_DATA=$(cat)

SESSION_ID=$(printf '%s' "$TOOL_DATA" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('session_id') or 'default')
except Exception:
    print('default')
")

FLAG_DIR="${TMPDIR:-/tmp}/reflex-memory-flags"
mkdir -p "$FLAG_DIR"
touch "$FLAG_DIR/${SESSION_ID}.memory_checked"
