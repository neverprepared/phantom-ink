#!/bin/sh
# PreToolUse hook — blocks WebSearch/WebFetch until brain_recall is called first.
# Paired with memory-first-set.sh (PostToolUse on brain_recall) which sets the flag.
#
# Toggle: REFLEX_MEMORY_FIRST=on (default) | off
#   off  → exit immediately; no block. Recommended when the `research` subagent
#          is in use, since it self-enforces the memory-first pattern.
#
# Flag TTL: 86400s (24h / effectively per-session). One recall check unlocks the session.

set -eu

case "${REFLEX_MEMORY_FIRST:-on}" in
    off|0|false) exit 0 ;;
esac

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

TOOL_DATA=$(cat)

# Fail open if jq unavailable
if ! command -v jq > /dev/null 2>&1; then exit 0; fi

SESSION_ID=$(printf '%s' "$TOOL_DATA" | jq -r '.session_id // "default"')
QUERY=$(printf '%s' "$TOOL_DATA" | jq -r '(.tool_input.query // .tool_input.url // "this topic")')

FLAG_DIR="${TMPDIR:-/tmp}/reflex-memory-flags"
mkdir -p "$FLAG_DIR"
FLAG="$FLAG_DIR/${SESSION_ID}.memory_checked"
FLAG_TTL=86400

if [ -f "$FLAG" ]; then
    # mtime: macOS uses stat -f %m, Linux uses stat -c %Y
    MTIME=$(stat -f %m "$FLAG" 2>/dev/null || stat -c %Y "$FLAG" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    AGE=$((NOW - MTIME))
    if [ "$AGE" -lt "$FLAG_TTL" ]; then
        exit 0  # flag is fresh — allow
    fi
fi

# No valid flag — deny and direct Claude to call brain_recall
MSG="🧠 Memory-first guardrail: check memory before searching the web.

Call mcp__phantom-brain__brain_recall with query=\"${QUERY}\"
If no relevant memories exist, you may then proceed with the web search.
(The recall check unlocks web searches for the rest of this session.)"

jq -n --arg msg "$MSG" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny"}, systemMessage: $msg}'
exit 0
