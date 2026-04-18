#!/bin/bash
# Start tmux session with the configured LLM agent

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

# Attach to existing session, or create new one
if tmux has-session -t main 2>/dev/null; then
    exec tmux attach -t main
else
    # Create session
    tmux -f /dev/null new -d -s main
    tmux set -t main status off
    tmux set -t main mouse on

    # Build agent command based on LLM_PROVIDER (defaults to claude)
    if [ "${LLM_PROVIDER:-claude}" = "codex" ]; then
        AGENT_CMD="codex --approval-mode full-auto"
        if [ -n "$CODEX_MODEL" ]; then
            AGENT_CMD="$AGENT_CMD --model \"$CODEX_MODEL\""
        fi
    else
        # Default: Claude Code
        # --plugin-dir mirrors the host wrapper: claude --plugin-dir .../reflex
        AGENT_CMD="claude --plugin-dir /opt/reflex/share/reflex --dangerously-skip-permissions"
        if [ -n "$CLAUDE_MODEL" ]; then
            AGENT_CMD="$AGENT_CMD --model \"$CLAUDE_MODEL\""
        fi
    fi

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
