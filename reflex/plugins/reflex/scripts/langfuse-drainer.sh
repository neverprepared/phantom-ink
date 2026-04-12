#!/bin/sh
# LangFuse queue drainer
# Started as a background daemon by langfuse-hook.sh.
# Polls the queue file every 2 seconds and processes batches via langfuse-trace.py.

set -eu

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
QUEUE_FILE="${CLAUDE_DIR}/reflex/.langfuse-queue.jsonl"
PID_FILE="${CLAUDE_DIR}/reflex/.langfuse-drainer.pid"

# Clean up PID file on exit
trap 'rm -f "$PID_FILE"' EXIT INT TERM

# Determine uvx python flags (check once at startup)
PYTHON_FLAG="--python 3.12"
if ! uvx --quiet --python 3.12 python -c "pass" 2>/dev/null; then
    PYTHON_FLAG=""
fi

while true; do
    sleep 2
    if [ -f "$QUEUE_FILE" ] && [ -s "$QUEUE_FILE" ]; then
        PROC_FILE="${QUEUE_FILE}.processing"
        mv "$QUEUE_FILE" "$PROC_FILE"
        # shellcheck disable=SC2086
        uvx --quiet $PYTHON_FLAG --with "langfuse>=3,<4" python \
            "$SCRIPT_DIR/langfuse-trace.py" --batch "$PROC_FILE" || \
            echo "langfuse-drainer: batch processing failed for $PROC_FILE" >&2
        rm -f "$PROC_FILE"
    fi
done
