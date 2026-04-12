#!/bin/sh
# Memory hook for Reflex
# Logs WebSearch and WebFetch events to SQLite at $REFLEX_HOME/memory.db
# Scoped to WebSearch|WebFetch via matcher in hooks.json

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Source profile env if available (hooks don't run in login shells)
if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

# Read tool data from stdin before anything else (stdin is consumed once)
TOOL_DATA=$(cat)

# Pass to memory.py for synchronous SQLite ingestion
printf '%s' "$TOOL_DATA" | python3 "$SCRIPT_DIR/memory.py" ingest || \
    echo "memory-hook: ingestion failed" >&2

exit 0
