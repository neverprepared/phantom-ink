#!/bin/sh
# brainbox-complete — record the session's result and mark its hub task done.
#
# Installed as /usr/local/bin/brainbox-complete (static, baked into the
# image). Primary path: PUT /api/session-store/result with the session's
# bearer token — the daemon stores result.json (Postgres + MinIO mirror)
# and completes the hub task in one call. The endpoint accepts this
# session's token even after TTL expiry (matched against the token minted
# at create), so long sessions complete cleanly.
#
# Legacy fallback (old daemon without /api/session-store): POST
# /api/hub/messages with API-key auth, exactly like the historically
# exec-generated ~/.brainbox/complete.sh.

RESULT="${1:-done}"

# The session-store env contract arrives via ~/.env on local sessions and
# via container env on runner sessions — source both ways.
[ -f "$HOME/.env" ] && . "$HOME/.env" 2>/dev/null

TOKEN=$(cat "$HOME/.agent-token" 2>/dev/null || echo "${BRAINBOX_TOKEN:-}")
HUB="${BRAINBOX_HUB_URL_PUBLIC:-${BRAINBOX_HUB_URL:-$(cat "$HOME/.brainbox/hub-url.txt" 2>/dev/null)}}"

if [ -z "$HUB" ]; then
    echo "brainbox-complete: no hub URL available" >&2
    exit 1
fi

BODY=$(python3 -c 'import json,sys; print(json.dumps({"result": sys.argv[1]}))' "$RESULT")

if [ -n "$TOKEN" ] && curl -sf --max-time 15 -X PUT "$HUB/api/session-store/result" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$BODY" >/dev/null 2>&1; then
    echo "Result recorded."
    exit 0
fi

# Legacy fallback: /api/hub/messages with API key + task id.
APIKEY=$(cat "$HOME/.brainbox-api-key" 2>/dev/null || echo '')
TASK_ID="${BRAINBOX_TASK_ID:-$(cat "$HOME/.brainbox/task-id.txt" 2>/dev/null)}"
if [ -n "$APIKEY" ] && [ -n "$TASK_ID" ]; then
    BODY2=$(python3 -c 'import json,sys; r,tid=sys.argv[1],sys.argv[2]; print(json.dumps({"payload":{"event":"task.completed","result":r,"task_id":tid}}))' "$RESULT" "$TASK_ID")
    if curl -sf --max-time 15 -X POST "$HUB/api/hub/messages" \
        -H "X-API-Key: $APIKEY" \
        -H "Content-Type: application/json" \
        -d "$BODY2" >/dev/null 2>&1; then
        echo "Task marked complete (legacy path)."
        exit 0
    fi
fi

echo "brainbox-complete: could not reach the hub — result not recorded" >&2
exit 1
