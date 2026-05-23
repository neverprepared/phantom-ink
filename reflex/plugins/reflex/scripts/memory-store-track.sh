#!/bin/sh
# PostToolUse — track whether the session has unstored web research.
# Scoped via hooks.json matcher to:
#   WebSearch | WebFetch                         (sets pending=true)
#   mcp__phantom-brain__brain_commit             (clears pending)
#
# State file: ${TMPDIR:-/tmp}/reflex-memory-state/{session_id}.json
# Pairs with memory-store-stop, which reads this and gates session end.

set -eu

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

# Fail open if jq unavailable
if ! command -v jq > /dev/null 2>&1; then exit 0; fi

TOOL_DATA=$(cat)

TOOL_NAME=$(printf '%s' "$TOOL_DATA" | jq -r '.tool_name // ""')
SESSION_ID=$(printf '%s' "$TOOL_DATA" | jq -r '.session_id // "default"')

STATE_DIR="${TMPDIR:-/tmp}/reflex-memory-state"
mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/${SESSION_ID}.json"

# Atomic write: write to .tmp then mv
write_state() {
    local tmp="${STATE_FILE}.tmp"
    printf '%s\n' "$1" > "$tmp"
    mv "$tmp" "$STATE_FILE"
}

# Read existing state or start empty
read_state() {
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE" 2>/dev/null || echo '{}'
    else
        echo '{}'
    fi
}

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

case "$TOOL_NAME" in
    WebSearch)
        QUERY=$(printf '%s' "$TOOL_DATA" | jq -r '.tool_input.query // ""')
        NEW=$(read_state | jq --arg ts "$NOW" --arg q "$QUERY" \
            '. + {pending: true, last_web_at: $ts, last_query: $q}')
        write_state "$NEW"
        ;;
    WebFetch)
        URL=$(printf '%s' "$TOOL_DATA" | jq -r '.tool_input.url // ""')
        NEW=$(read_state | jq --arg ts "$NOW" --arg u "$URL" \
            '. + {pending: true, last_web_at: $ts, last_url: $u}')
        write_state "$NEW"
        ;;
    mcp__phantom-brain__brain_commit)
        EXISTING=$(read_state)
        # Only update if state file exists and has content
        if [ "$EXISTING" != '{}' ]; then
            NEW=$(printf '%s' "$EXISTING" | jq '. + {pending: false}')
            write_state "$NEW"
        fi
        ;;
esac

exit 0
