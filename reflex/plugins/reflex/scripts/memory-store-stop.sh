#!/bin/sh
# Stop hook — at session end, remind if web research is unstored.
# Honors REFLEX_MEMORY_ENFORCE=hard|soft|off (default soft).
#   hard → block stop until memory_store/memory_update/task_complete is called
#   soft → warn only, don't block
#   off  → exit immediately, no check

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

case "${REFLEX_MEMORY_ENFORCE:-soft}" in
    off) exit 0 ;;
esac

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

python3 "$SCRIPT_DIR/memory-store-stop.py"
