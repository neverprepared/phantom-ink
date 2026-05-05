#!/bin/sh
# PostToolUse hook — maintain the active-task session-state file.
# Scoped via hooks.json matcher to mcp__obsidian-second-brain__task_start
# / task_update / task_complete only.
#
# State file: ${TMPDIR:-/tmp}/reflex-task-state/{session_id}.active_task.json
# Pairs with task-enforce-pre and task-enforce-stop, which read the state.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

python3 "$SCRIPT_DIR/task-enforce-set.py"
