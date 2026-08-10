#!/bin/bash
# Start tmux session with the configured LLM agent

# Docker exec_run provides a minimal environment; ensure standard bin dirs are present.
# ~/.local/bin first: python3 is uv-managed there, and the credential
# extraction below pipes through it — a launcher that omits it from PATH
# must not leave sessions unauthenticated.
export PATH=${HOME}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}

# Ensure WORKSPACE_HOME has a sensible default so vars like CLAUDE_CONFIG_DIR
# that reference it expand correctly before the profile env is fully loaded.
: "${WORKSPACE_HOME:=$HOME}"

# Decrypt profile env if the image contains an encrypted bundle and the key was injected.
if [ -n "${PROFILE_ENV_KEY:-}" ] && [ -f "$HOME/.env.enc" ]; then
    openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d \
        -pass "pass:${PROFILE_ENV_KEY}" \
        -in "$HOME/.env.enc" \
        -out "$HOME/.env" 2>/dev/null && chmod 600 "$HOME/.env"
fi

# Decrypt Claude credentials if the image contains an encrypted bundle.
if [ -n "${PROFILE_ENV_KEY:-}" ] && [ -f "$HOME/.claude.enc" ]; then
    openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d \
        -pass "pass:${PROFILE_ENV_KEY}" \
        -in "$HOME/.claude.enc" \
        2>/dev/null | python3 -c "
import json, os, sys
d = json.load(sys.stdin)
home = os.environ.get('HOME', '/home/developer')
os.makedirs(home + '/.claude', mode=0o700, exist_ok=True)
def wf(path, content, mode):
    with open(path, 'w') as f:
        f.write(content)
    os.chmod(path, mode)
if 'credentials_json' in d:
    wf(home + '/.claude/.credentials.json', d['credentials_json'], 0o600)
if 'claude_json' in d:
    wf(home + '/.claude/.claude.json', d['claude_json'], 0o600)
if 'settings_json' in d:
    wf(home + '/.claude/settings.json', d['settings_json'], 0o644)
if 'claude_md' in d:
    wf(home + '/.claude/CLAUDE.md', d['claude_md'], 0o644)
" 2>/dev/null
fi

# Claude Code reads ~/.claude.json (CLAUDE_CONFIG_DIR is unset in the container),
# but the canonical profile config lives at ~/.claude/.claude.json — the host
# CLAUDE_CONFIG_DIR layout, written by the decrypt step above. Copy it into
# place, overwriting any stale file baked into the image, so Claude always
# loads the profile config. Must be a real file: Claude Code does not reliably
# honor a symlinked ~/.claude.json.
if [ -f "$HOME/.claude/.claude.json" ]; then
    cp -f "$HOME/.claude/.claude.json" "$HOME/.claude.json"
fi

# Re-assert onboarding + Bypass-Permissions acceptance AFTER the profile config
# is in place. The decrypted host config (claude_json from .claude.enc) does NOT
# carry these flags, so it overwrites the ones baked into the image — and a
# `claude --dangerously-skip-permissions` launch then stalls on the interactive
# "Bypass Permissions" warning, which is fatal for a headless session. Merge the
# flags back in (preserving oauthAccount and everything else) on both the file
# Claude reads (~/.claude.json) and the canonical copy.
for _cfg in "$HOME/.claude.json" "$HOME/.claude/.claude.json"; do
    if [ -f "$_cfg" ] && command -v jq >/dev/null 2>&1; then
        _tmp="$(mktemp)" || continue
        if jq '. + {hasCompletedOnboarding:true,bypassPermissionsModeAccepted:true}' \
               "$_cfg" > "$_tmp" 2>/dev/null; then
            mv "$_tmp" "$_cfg"
        else
            rm -f "$_tmp"
        fi
    fi
done

# Decrypt Codex credentials (ChatGPT OAuth) if the image contains an encrypted
# bundle. Codex reads ~/.codex/auth.json; without it a codex session aborts at
# startup because ~/.codex does not exist. Create the dir and write the auth
# file so codex is authenticated on first launch (tokens refresh in place).
if [ -n "${PROFILE_ENV_KEY:-}" ] && [ -f "$HOME/.codex.enc" ]; then
    mkdir -p "$HOME/.codex" && chmod 700 "$HOME/.codex"
    openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -d \
        -pass "pass:${PROFILE_ENV_KEY}" \
        -in "$HOME/.codex.enc" \
        -out "$HOME/.codex/auth.json" 2>/dev/null && chmod 600 "$HOME/.codex/auth.json"
fi

# Source the session env (non-hardened sessions deliver secrets + the
# LLM_PROVIDER / CLAUDE_MODEL / gateway-contract vars via ~/.env). This MUST
# run before the provider case below — otherwise LLM_PROVIDER is empty at
# decision time and every ollama/codex session falls through to claude.
# Sourcing again later is harmless (idempotent).
set -a
[ -f "$HOME/.env" ] && . "$HOME/.env" 2>/dev/null
set +a

# In hardened mode secrets land in /run/secrets/ rather than ~/.env.
# Read the vars we need from there if not already in the environment.
_secret() {
    local key="$1"
    if [ -z "${!key}" ] && [ -f "/run/secrets/$key" ]; then
        export "$key"="$(cat "/run/secrets/$key")"
    fi
}
_secret LLM_PROVIDER
_secret CODEX_MODEL
_secret CLAUDE_MODEL
_secret CLAUDE_EFFORT
_secret ANTHROPIC_BASE_URL

# --- ADR-004: live Claude credential pull (preferred over baked .claude.enc) ---
# Pull a FRESH OAuth credential from the router at startup so authentication
# decouples from the image's bake time. The router is the sole broker reader; it
# applies the TTL floor and fails closed. On any non-200 (live creds off, none
# stored, below TTL floor, broker down, no token) we KEEP the baked credential —
# so this is strictly additive and safe to ship flag-gated. Uses the same
# BRAINBOX_TOKEN / BRAINBOX_HUB_URL the task fetch already relies on.
# We content-validate the body (top-level "claudeAiOauth" object, the real
# .credentials.json shape) before overwriting, so a well-formed-but-wrong 200
# (e.g. {} or an error envelope) can never clobber a working baked credential.
_bb_token="$(cat "${HOME}/.agent-token" 2>/dev/null || echo "${BRAINBOX_TOKEN:-}")"
_bb_hub="${BRAINBOX_HUB_URL_PUBLIC:-${BRAINBOX_HUB_URL:-}}"
if [ -n "$_bb_token" ] && [ -n "$_bb_hub" ]; then
    _cred_tmp="$(mktemp)"
    _code="$(curl -s -o "$_cred_tmp" -w '%{http_code}' --max-time 10 \
        -H "Authorization: Bearer $_bb_token" \
        "$_bb_hub/api/session-store/claude-credentials" 2>/dev/null || echo 000)"
    if [ "$_code" = "200" ] && [ -s "$_cred_tmp" ] \
       && python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if isinstance(d.get('claudeAiOauth'), dict) else 1)" "$_cred_tmp" 2>/dev/null; then
        mkdir -p "$HOME/.claude"
        cp -f "$_cred_tmp" "$HOME/.claude/.credentials.json"
        chmod 600 "$HOME/.claude/.credentials.json"
        echo "ttyd-wrapper: using LIVE Claude credential from broker (ADR-004)" >&2
    else
        echo "ttyd-wrapper: live credential unavailable (HTTP $_code) — using baked credential" >&2
    fi
    rm -f "$_cred_tmp"
fi

# Attach to existing session, or create new one
if tmux has-session -t main 2>/dev/null; then
    exec tmux attach -t main
else
    # Create session
    tmux -f /dev/null new -d -s main
    tmux set -t main status off
    tmux set -t main mouse on

    # Build agent command based on LLM_PROVIDER (defaults to claude)
    case "${LLM_PROVIDER:-claude}" in
        codex)
            # --model is injected by the codex() shell wrapper in .bashrc
            # when CODEX_MODEL is set, so we don't pass it here.
            # --sandbox off: Docker already provides container isolation;
            # workspace-write sandbox requires kernel namespaces unavailable in containers.
            AGENT_CMD="codex --sandbox off --ask-for-approval never"
            ;;
        ollama)
            # CLAUDE_MODEL is set to the ollama model name by lifecycle.py.
            OLLAMA_MODEL="${CLAUDE_MODEL:-qwen3:8b}"
            # When the MCP gateway is wired for this session, launch the
            # ollama-mcp bridge so the model can call gateway tools (ollama
            # is not an MCP client). Otherwise fall back to the plain REPL.
            if [ -n "${PHANTOM_GATEWAY_URL:-}" ] && [ -n "${PHANTOM_GATEWAY_TOKEN:-}" ] \
               && command -v ollama-mcp >/dev/null 2>&1; then
                AGENT_CMD="MODEL=\"$OLLAMA_MODEL\" ollama-mcp"
            else
                AGENT_CMD="ollama run \"$OLLAMA_MODEL\""
            fi
            ;;
        *)
            # Default: Claude Code
            # --plugin-dir mirrors the host wrapper: claude --plugin-dir .../reflex
            AGENT_CMD="claude --plugin-dir /opt/reflex/share/reflex --dangerously-skip-permissions"
            if [ -n "$CLAUDE_MODEL" ]; then
                AGENT_CMD="$AGENT_CMD --model \"$CLAUDE_MODEL\""
            fi
            if [ -n "$CLAUDE_EFFORT" ]; then
                AGENT_CMD="$AGENT_CMD --effort \"$CLAUDE_EFFORT\""
            fi
            ;;
    esac

    # --- Session-store task fetch (pull model) ---------------------------
    # The task is stored durably on the hub BEFORE this container existed;
    # fetch it with the session's bearer token. Works identically for local
    # and runner-dispatched sessions — no exec-injection race. On local
    # sessions the env contract arrives via ~/.env; on runner sessions it
    # is already in the container env.
    [ -f "${HOME}/.env" ] && . "${HOME}/.env" 2>/dev/null
    TOKEN=$(cat "${HOME}/.agent-token" 2>/dev/null || echo "${BRAINBOX_TOKEN:-}")
    HUB="${BRAINBOX_HUB_URL_PUBLIC:-${BRAINBOX_HUB_URL:-}}"
    TASK_FILE=""
    if [ -n "$TOKEN" ] && [ -n "$HUB" ]; then
        for _try in 1 2 3; do   # runner networks can be slow right after create
            if curl -sf --max-time 10 -H "Authorization: Bearer $TOKEN" \
                 "$HUB/api/session-store/task" -o /tmp/brainbox-task.txt 2>/dev/null \
               && [ -s /tmp/brainbox-task.txt ]; then
                TASK_FILE=/tmp/brainbox-task.txt
                break
            fi
            sleep 2
        done
    fi
    # Legacy fallback: exec-injected file (old daemon, or hub unreachable).
    [ -z "$TASK_FILE" ] && [ -f "${HOME}/.brainbox/task.txt" ] && TASK_FILE="${HOME}/.brainbox/task.txt"

    # If a task was found, pass it as the initial prompt so the agent starts
    # working immediately without any manual Enter press. After the agent
    # exits, record the result and exit the container.
    if [ -n "$TASK_FILE" ]; then
        COMPLETE=brainbox-complete
        command -v brainbox-complete >/dev/null 2>&1 || COMPLETE="${HOME}/.brainbox/complete.sh"
        TASK_CMD="$AGENT_CMD \"\$(cat $TASK_FILE)\""
        TASK_CMD="$TASK_CMD; $COMPLETE \"\$(cat /tmp/.claude-task-result 2>/dev/null || echo done)\"; exit"
        tmux send-keys -t main "$TASK_CMD" Enter
    else
        tmux send-keys -t main "$AGENT_CMD" Enter
    fi

    exec tmux attach -t main
fi
