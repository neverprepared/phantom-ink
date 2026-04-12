#!/bin/sh
# PreToolUse hook — blocks WebSearch/WebFetch until memory_search is called first.
# Paired with memory-first-set.sh (PostToolUse on memory_search) which sets the flag.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

TOOL_DATA=$(cat)
printf '%s' "$TOOL_DATA" | python3 "$SCRIPT_DIR/memory-first.py"
