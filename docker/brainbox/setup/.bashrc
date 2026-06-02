
# Load env vars from profile files (decrypted from .env.enc by ttyd-wrapper, or bind-mounted).
# set +H disables history expansion so values containing ! don't trigger "event not found".
if [ -f /home/developer/.env ]; then
    set +H; set -a; . /home/developer/.env; set +a; set -H
fi
if [ -e /home/developer/.env.secrets ]; then
    set +H; set -a; . /home/developer/.env.secrets; set +a; set -H
fi

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
