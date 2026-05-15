#!/bin/sh
# PreToolUse hook — blocks WebSearch/WebFetch until memory_search is called first.
# Paired with memory-first-set.sh (PostToolUse on memory_search) which sets the flag.
#
# Toggle: REFLEX_MEMORY_FIRST=on (default) | off
#   off  → exit immediately; no block. Recommended when the `research` subagent
#          is in use, since it self-enforces the memory-first pattern.

set -eu

case "${REFLEX_MEMORY_FIRST:-on}" in
    off|0|false) exit 0 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

TOOL_DATA=$(cat)
printf '%s' "$TOOL_DATA" | python3 "$SCRIPT_DIR/memory-first.py"
