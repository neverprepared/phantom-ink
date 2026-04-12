
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
