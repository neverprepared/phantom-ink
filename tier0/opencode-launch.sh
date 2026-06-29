#!/usr/bin/env bash
# Tier-0 local ollama harness (ADR-002).
#
# Launches opencode wired to a local ollama model + the phantom-ink MCP
# gateway, scoped to a workspace profile. This is the private, local Tier-0
# brain in the platform vision: ollama for inference (never leaves the box),
# the gateway for credentialed tools (per-profile, scoped).
#
# It is a thin launcher — opencode owns the agent loop. We only:
#   1. mint a profile-scoped Tier-0 gateway token (in-memory, TTL-bounded),
#   2. render an opencode config (token stays an {env:} ref — never on disk),
#   3. exec opencode with that config + token in the environment.
#
# Usage:
#   tier0/opencode-launch.sh [-p PROFILE] [opencode args...]
#   tier0/opencode-launch.sh -p personal                 # interactive TUI
#   tier0/opencode-launch.sh -p personal run "summarize X"  # headless
#
# Env knobs (all optional):
#   CL_API_KEY        REQUIRED — brainbox operator key (to mint the token)
#   BRAINBOX_URL      base URL for minting + gateway (default: public prod URL)
#   OPENCODE_MODEL    opencode model id (default: ollama/qwen3:8b)
#   OLLAMA_OPENAI_URL ollama OpenAI-compatible endpoint (default: localhost)
#   PHANTOM_GW_TTL    token TTL seconds (default: 43200 = 12h)
#   PHANTOM_GW_SCOPE  comma-separated tool patterns (default: empty = all)
set -euo pipefail

PROFILE="${WORKSPACE_PROFILE:-personal}"
oc_args=()
while [ $# -gt 0 ]; do
  case "$1" in
    -p|--profile) PROFILE="${2:?--profile needs a value}"; shift 2 ;;
    --) shift; while [ $# -gt 0 ]; do oc_args+=("$1"); shift; done ;;
    *) oc_args+=("$1"); shift ;;
  esac
done

BASE="${BRAINBOX_URL:-https://brainbox-api.neverprepared.com}"
MODEL="${OPENCODE_MODEL:-ollama/qwen3:8b}"
OLLAMA_URL="${OLLAMA_OPENAI_URL:-http://localhost:11434/v1}"
TTL="${PHANTOM_GW_TTL:-43200}"
SCOPE="${PHANTOM_GW_SCOPE:-}"

: "${CL_API_KEY:?CL_API_KEY must be set (brainbox operator key) to mint a gateway token}"
command -v opencode >/dev/null 2>&1 || {
  echo "opencode not installed. Install one of:" >&2
  echo "  npm install -g opencode-ai" >&2
  echo "  brew install anomalyco/tap/opencode" >&2
  echo "  curl -fsSL https://opencode.ai/install | bash" >&2
  exit 1
}

# 1. mint a Tier-0 gateway token bound to the profile
scope_json="[]"
if [ -n "$SCOPE" ]; then
  scope_json=$(printf '%s' "$SCOPE" | python3 -c 'import sys,json; print(json.dumps([s.strip() for s in sys.stdin.read().split(",") if s.strip()]))')
fi
resp=$(curl -fsS -m 15 -X POST \
  -H "X-API-Key: $CL_API_KEY" -H "Content-Type: application/json" \
  -d "{\"profile\":\"$PROFILE\",\"scope\":$scope_json,\"ttl\":$TTL}" \
  "$BASE/api/gateway/tokens")
TOKEN=$(printf '%s' "$resp" | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
[ -n "$TOKEN" ] || { echo "failed to mint gateway token: $resp" >&2; exit 1; }

# 2. render the opencode config (the token stays an {env:} ref — not written here)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/phantom-ink/opencode"
mkdir -p "$CACHE"
CFG="$CACHE/opencode.$PROFILE.json"
sed -e "s#__GW_URL__#$BASE/gateway/mcp#g" \
    -e "s#__OLLAMA_URL__#$OLLAMA_URL#g" \
    -e "s#__MODEL__#$MODEL#g" \
    "$HERE/opencode.template.json" > "$CFG"

# 3. launch — opencode owns the loop from here
export PHANTOM_GW_TOKEN="$TOKEN"
export OPENCODE_CONFIG="$CFG"
echo "Tier-0 opencode → profile=$PROFILE model=$MODEL gateway=$BASE/gateway/mcp (token ttl ${TTL}s)" >&2
exec opencode ${oc_args[@]+"${oc_args[@]}"}
