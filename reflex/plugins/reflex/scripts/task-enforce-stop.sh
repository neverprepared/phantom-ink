#!/bin/sh
# Stop hook — fires when Claude Code is about to end the session/turn.
# If an obsidian-second-brain task is active and dirty (has updates), surface
# the session's WebSearch/WebFetch activity from memory.db as task evidence
# and prompt Claude to call task_update + task_complete before stopping.
#
# Honors REFLEX_TASK_ENFORCE=hard|soft|off. In hard mode, denies the stop;
# in soft mode, only emits a systemMessage reminder.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

case "${REFLEX_TASK_ENFORCE:-hard}" in
    off) exit 0 ;;
esac

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

python3 "$SCRIPT_DIR/task-enforce-stop.py"
