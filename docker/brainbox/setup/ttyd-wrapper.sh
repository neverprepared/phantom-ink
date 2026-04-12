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
    # After Claude exits, run complete.sh and exit the container.
    if [ -f "/home/developer/.brainbox/task.txt" ]; then
        TASK_CMD="$CLAUDE_CMD \"\$(cat /home/developer/.brainbox/task.txt)\""
        TASK_CMD="$TASK_CMD; ~/.brainbox/complete.sh \"\$(cat /tmp/.claude-task-result 2>/dev/null || echo done)\"; exit"
        tmux send-keys -t main "$TASK_CMD" Enter
    else
        tmux send-keys -t main "$CLAUDE_CMD" Enter
    fi

    exec tmux attach -t main
fi
