#!/bin/sh
# PreToolUse hook — enforce that an obsidian-second-brain task is active
# before any non-exempt tool runs. Honors REFLEX_TASK_ENFORCE=hard|soft|off.
#
# Matched broadly via hooks.json (.*); the python script does the
# fine-grained tool-name exclusion (lifecycle + memory recall are exempt).

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

python3 "$SCRIPT_DIR/task-enforce-pre.py"
