#!/bin/sh
# LangFuse integration hook for Reflex
# Writes tool call data to a queue file and exits immediately.
# A background drainer (langfuse-drainer.sh) processes the queue asynchronously.

set -eu

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
QUEUE_FILE="${CLAUDE_DIR}/reflex/.langfuse-queue.jsonl"
PID_FILE="${CLAUDE_DIR}/reflex/.langfuse-drainer.pid"

# Source profile env if available (hooks don't run in login shells)
if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

# Map API-prefixed key names to SDK-expected names
LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY:-${LANGFUSE_API_PUBLIC_KEY:-}}"
LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY:-${LANGFUSE_API_SECRET_KEY:-}}"

# Skip if no credentials
if [ -z "$LANGFUSE_PUBLIC_KEY" ] || [ -z "$LANGFUSE_SECRET_KEY" ]; then
    exit 0
fi

# Read tool data, stamp with current time, and append to queue
TOOL_DATA=$(cat)
QUEUED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TOOL_DATA=$(printf '%s' "$TOOL_DATA" | jq --arg ts "$QUEUED_AT" '. + {_queued_at: $ts}')
mkdir -p "$(dirname "$QUEUE_FILE")"
printf '%s\n' "$TOOL_DATA" >> "$QUEUE_FILE"

# Start drainer if not already running
if [ -f "$PID_FILE" ]; then
    DRAINER_PID=$(cat "$PID_FILE" 2>/dev/null)
    if kill -0 "$DRAINER_PID" 2>/dev/null; then
        exit 0
    fi
fi

# Export credentials for the drainer process to inherit
export LANGFUSE_PUBLIC_KEY
export LANGFUSE_SECRET_KEY
export LANGFUSE_BASE_URL="${LANGFUSE_BASE_URL:-}"
export LANGFUSE_USER_ID="${WORKSPACE_PROFILE:-$HOME}"
export CLAUDE_CONFIG_DIR="$CLAUDE_DIR"

nohup "$SCRIPT_DIR/langfuse-drainer.sh" > /dev/null 2>&1 &
printf '%d\n' "$!" > "$PID_FILE"
exit 0
