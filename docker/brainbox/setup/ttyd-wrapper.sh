#!/bin/bash
# Start tmux session with the configured LLM agent

# Docker exec_run provides a minimal environment; ensure standard bin dirs are present.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}

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
            # CLAUDE_MODEL is set to the ollama model name by lifecycle.py
            OLLAMA_MODEL="${CLAUDE_MODEL:-llama3}"
            AGENT_CMD="ollama run \"$OLLAMA_MODEL\""
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

    # If a task file exists, pass its content as the initial prompt so the
    # agent starts working immediately without any manual Enter press.
    # After the agent exits, run complete.sh and exit the container.
    if [ -f "${HOME}/.brainbox/task.txt" ]; then
        TASK_CMD="$AGENT_CMD \"\$(cat ${HOME}/.brainbox/task.txt)\""
        TASK_CMD="$TASK_CMD; ${HOME}/.brainbox/complete.sh \"\$(cat /tmp/.claude-task-result 2>/dev/null || echo done)\"; exit"
        tmux send-keys -t main "$TASK_CMD" Enter
    else
        tmux send-keys -t main "$AGENT_CMD" Enter
    fi

    exec tmux attach -t main
fi
