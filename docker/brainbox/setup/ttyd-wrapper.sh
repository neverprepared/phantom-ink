#!/bin/bash
# Start tmux session with claude

# Attach to existing session, or create new one with claude
if tmux has-session -t main 2>/dev/null; then
    exec tmux attach -t main
else
    # Create session
    tmux -f /dev/null new -d -s main
    tmux set -t main status off
    tmux set -t main mouse on

    # Start claude (env vars are loaded via BASH_ENV -> .bashrc -> .env)
    # --plugin-dir mirrors the host wrapper: claude --plugin-dir .../reflex
    CLAUDE_CMD="claude --plugin-dir /opt/reflex/share/reflex --dangerously-skip-permissions"
    if [ -n "$CLAUDE_MODEL" ]; then
        CLAUDE_CMD="$CLAUDE_CMD --model $CLAUDE_MODEL"
    fi

    # If a task file exists, pass its content as the initial prompt so Claude
    # starts working immediately without any manual Enter press.
    # Using shell command substitution inside the tmux send-keys string means
    # the task is evaluated by bash inside the tmux pane — no fragile polling
    # or character-by-character typing needed.
    if [ -f "/home/developer/.brainbox/task.txt" ]; then
        tmux send-keys -t main "$CLAUDE_CMD \"\$(cat /home/developer/.brainbox/task.txt)\"" Enter
    else
        tmux send-keys -t main "$CLAUDE_CMD" Enter
    fi

    exec tmux attach -t main
fi
