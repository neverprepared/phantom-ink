#!/bin/sh
# PostToolUse — track whether the session has unstored web research.
# Scoped via hooks.json matcher to:
#   WebSearch | WebFetch                         (sets pending=true)
#   mcp__obsidian-second-brain__memory_store     (clears pending)
#   mcp__obsidian-second-brain__memory_update    (clears pending)
#   mcp__obsidian-second-brain__task_complete    (clears pending — auto-promotes findings)
#
# State file: ${TMPDIR:-/tmp}/reflex-memory-state/{session_id}.json
# Pairs with memory-store-stop, which reads this and gates session end.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

python3 "$SCRIPT_DIR/memory-store-track.py"
