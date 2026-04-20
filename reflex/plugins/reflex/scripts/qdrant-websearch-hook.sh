#!/bin/sh
# Qdrant WebSearch auto-storage hook for Reflex
# Called by Claude Code PostToolUse hook
# Automatically stores WebSearch results in Qdrant when available
#
# Uses a queue-and-drain pattern to avoid blocking the hook timeout.
# On first run, fastembed downloads ~90MB model which would exceed the 5s timeout
# if run synchronously. Instead, we write to a queue and drain asynchronously.
#
# NOTE: Claude Code executes hooks via /bin/sh, ignoring the shebang.
# This script must be POSIX sh-compatible — no bash-specific features:
#   - No 'pipefail' (bash-only option)
#   - No BASH_SOURCE (use $0 instead)
#   - No here-strings <<< (use printf | instead)

set -eu

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
QUEUE_FILE="${CLAUDE_DIR}/reflex/.qdrant-websearch-queue.jsonl"
PID_FILE="${CLAUDE_DIR}/reflex/.qdrant-websearch-drainer.pid"

# Check toggle (default: enabled)
if [ "${REFLEX_QDRANT_AUTOSAVE:-true}" = "false" ]; then
    exit 0
fi

# Read tool data from stdin (JSON from Claude Code hook)
TOOL_DATA=$(cat)

# Extract tool name
TOOL_NAME=$(printf '%s' "$TOOL_DATA" | jq -r '.tool_name // empty' 2>/dev/null || echo "")

# Filter for WebSearch only
if [ "$TOOL_NAME" != "WebSearch" ]; then
    exit 0
fi

# Write to queue and return immediately (avoid blocking hook timeout)
mkdir -p "$(dirname "$QUEUE_FILE")"
printf '%s\n' "$TOOL_DATA" >> "$QUEUE_FILE"

# Start background drainer if not already running
if [ -f "$PID_FILE" ]; then
    DRAINER_PID=$(cat "$PID_FILE" 2>/dev/null)
    if kill -0 "$DRAINER_PID" 2>/dev/null; then
        exit 0
    fi
fi

# Determine uvx python flag (check once before background launch)
PYTHON_FLAG="--python 3.12"
if ! uvx --quiet --python 3.12 python -c "pass" 2>/dev/null; then
    PYTHON_FLAG=""
fi

export PYTHON_FLAG
export CLAUDE_CONFIG_DIR="$CLAUDE_DIR"

nohup sh -c '
    QUEUE_FILE="${CLAUDE_CONFIG_DIR}/reflex/.qdrant-websearch-queue.jsonl"
    PID_FILE="${CLAUDE_CONFIG_DIR}/reflex/.qdrant-websearch-drainer.pid"
    SCRIPT_DIR="'"$SCRIPT_DIR"'"
    trap "rm -f \"$PID_FILE\"" EXIT INT TERM
    while true; do
        sleep 2
        if [ -f "$QUEUE_FILE" ] && [ -s "$QUEUE_FILE" ]; then
            PROC_FILE="${QUEUE_FILE}.processing"
            mv "$QUEUE_FILE" "$PROC_FILE"
            # shellcheck disable=SC2086
            if uvx --quiet '"${PYTHON_FLAG}"' --with qdrant-client --with fastembed \
                python "$SCRIPT_DIR/qdrant-websearch-store.py" --batch "$PROC_FILE" 2>/dev/null; then
                rm -f "$PROC_FILE"
            else
                mv "$PROC_FILE" "${PROC_FILE}.failed" 2>/dev/null || true
            fi
        fi
    done
' > /dev/null 2>&1 &
printf '%d\n' "$!" > "$PID_FILE"

exit 0
