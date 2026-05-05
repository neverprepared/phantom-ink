#!/bin/sh
# Stop hook — at session end, deny if web research is unstored.
# Honors REFLEX_MEMORY_ENFORCE=hard|soft|off (default hard).

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

case "${REFLEX_MEMORY_ENFORCE:-hard}" in
    off) exit 0 ;;
esac

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

python3 "$SCRIPT_DIR/memory-store-stop.py"
