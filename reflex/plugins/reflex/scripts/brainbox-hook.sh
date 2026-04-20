#!/bin/bash
# SessionStart hook: discover brainbox API.
#
# Runs the connect script and outputs status to the session context.
# Silent when unavailable — don't nag users who don't have it installed.

set -euo pipefail

# Consume stdin to avoid blocking the hook runner
cat > /dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONNECT_SCRIPT="${SCRIPT_DIR}/brainbox-connect.sh"

if [[ ! -x "$CONNECT_SCRIPT" ]]; then
  exit 0
fi

RESULT=$("$CONNECT_SCRIPT" 2>/dev/null) || {
  # Connect script failed — exit silently
  exit 0
}

STATUS=$(echo "$RESULT" | jq -r '.status // empty' 2>/dev/null || true)
CONTEXT=""

case "$STATUS" in
  connected)
    URL=$(echo "$RESULT" | jq -r '.url // empty' 2>/dev/null)
    [[ -n "$URL" ]] && CONTEXT="Brainbox: connected at ${URL}"
    ;;
  started)
    URL=$(echo "$RESULT" | jq -r '.url // empty' 2>/dev/null)
    [[ -n "$URL" ]] && CONTEXT="Brainbox: auto-started at ${URL}"
    ;;
  *)
    # Unavailable — stay silent
    exit 0
    ;;
esac

# Output hook context (only if we have something to report)
[[ -z "$CONTEXT" ]] && exit 0
printf '%s' "$CONTEXT" | jq -Rs '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: .
  }
}'

exit 0
