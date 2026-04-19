
# Load env vars from profile files (bind-mounted from host)
[ -f /home/developer/.env ] && set -a && . /home/developer/.env && set +a
[ -e /home/developer/.env.secrets ] && set -a && . /home/developer/.env.secrets && set +a

# Claude Code aliases
alias c='claude'
alias cs='claude --dangerously-skip-permissions'

# Claude --fs shortcut
claude() {
  local args=()
  for arg in "$@"; do
    if [[ "$arg" == "--fs" ]]; then
      args+=("--fork-session")
    else
      args+=("$arg")
    fi
  done
  command claude "${args[@]}"
}

# Codex aliases (active when CODEX_MODEL is set — provider is "codex")
if [ -n "$CODEX_MODEL" ]; then
  alias c='codex'

  codex() {
    local args=()
    for arg in "$@"; do
      args+=("$arg")
    done
    command codex --model "${CODEX_MODEL}" "${args[@]}"
  }
fi
