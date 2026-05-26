#!/bin/sh
# PostToolUse (WebFetch) — queue the URL for deferred fetch + synthesis.
# brain_synthesize will fetch the page, write to Raw/gathered/, and run the gate.
# Scoped via hooks.json matcher to WebFetch only.

set -eu

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

if ! command -v jq > /dev/null 2>&1; then exit 0; fi

VAULT_PATH="${BRAIN_VAULT_PATH:-}"
if [ -z "$VAULT_PATH" ]; then exit 0; fi

TOOL_DATA=$(cat)
TOOL_NAME=$(printf '%s' "$TOOL_DATA" | jq -r '.tool_name // ""')
if [ "$TOOL_NAME" != "WebFetch" ]; then exit 0; fi

URL=$(printf '%s' "$TOOL_DATA" | jq -r '.tool_input.url // ""')
if [ -z "$URL" ] || [ "$URL" = "null" ]; then exit 0; fi

# Use hostname as the title (e.g. "karpathy.github.io")
TITLE=$(printf '%s' "$URL" | sed 's|https://||; s|http://||; s|/.*||')

QUEUE_DIR="$VAULT_PATH/_queue/pending"
mkdir -p "$QUEUE_DIR"

STAMP=$(date -u +"%Y%m%dT%H%M%SZ")
RAND=$(openssl rand -hex 6 2>/dev/null || dd if=/dev/urandom bs=6 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')

QUEUE_FILE="$QUEUE_DIR/${STAMP}-${RAND}.json"
TMP_QUEUE="${QUEUE_FILE}.tmp.$$"

jq -n \
    --arg source_url "$URL" \
    --arg title "$TITLE" \
    --arg captured_at "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    '{
        source: "gathered",
        source_url: $source_url,
        title: $title,
        format: "html",
        captured_at: $captured_at,
        deferred_fetch: true
    }' > "$TMP_QUEUE"
mv "$TMP_QUEUE" "$QUEUE_FILE"

exit 0
