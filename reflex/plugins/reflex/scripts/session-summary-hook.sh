#!/bin/sh
# SessionEnd hook — emit a session.summary event onto the phantom event bus
# describing what the agent did this session (narrative + machine-extracted
# facts + artifact handles). Fires ONCE at session end (not per-turn like Stop).
#
# Toggle: REFLEX_SESSION_SUMMARY = on | off (default on). No-ops silently when
# no endpoint/key is configured, so it's safe everywhere the plugin loads.
# Evidence: REFLEX_SESSION_EVIDENCE = on | off (default OFF). When on, the full
# transcript + git diff are uploaded as artifacts — off by default because those
# can contain secrets. The summary event (narrative + facts) posts either way.
#
# Config (env; the wrapper sources /run/profile/.env first, as other hooks do):
#   PHANTOM_API_URL   router base (default http://127.0.0.1:9910); falls back to CL_PUBLIC_URL
#   PHANTOM_API_KEY   X-API-Key / profile token with agent_events:write; falls back to CL_API_KEY
#   WORKSPACE_PROFILE the profile the summary is scoped to; falls back to CL_WORKSPACE_PROFILE

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

case "${REFLEX_SESSION_SUMMARY:-on}" in
    off) exit 0 ;;
esac

if [ -f /run/profile/.env ]; then
    set -a
    . /run/profile/.env
    set +a
fi

# Never let summary emission break session teardown.
python3 "$SCRIPT_DIR/session-summary.py" || true
exit 0
