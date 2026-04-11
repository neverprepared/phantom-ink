#!/usr/bin/env bash
# Workspace profile: {{.ProfileName}}
# Template: {{.Template}}
# Created: {{.CreatedAt}}

# Workspace identification
export WORKSPACE_PROFILE="{{.ProfileName}}"
export WORKSPACE_HOME="$PWD"
export STARSHIP_CONFIG="$WORKSPACE_HOME/.config/starship.toml"

# Add custom bin directory to PATH (before system paths)
# The bin/ssh wrapper uses the profile-specific SSH config
# Git will automatically use bin/ssh since it's first in PATH
PATH_add bin

# Load global profile settings (exports only)
# Environment variables work with direnv, aliases and functions do not
GLOBAL_DIR="$(cd "$(dirname "$PWD")/.global" 2>/dev/null && pwd)"
if [[ -d "$GLOBAL_DIR" ]]; then
    # Source exports (environment variables work with direnv)
    if [[ -f "$GLOBAL_DIR/exports.sh" && -r "$GLOBAL_DIR/exports.sh" ]]; then
        source "$GLOBAL_DIR/exports.sh"
    fi
fi

# Load tool paths and non-secret config
dotenv_if_exists .env

# Load secrets (1Password FIFO mount or plaintext file)
# Note: dotenv_if_exists doesn't work with FIFOs (named pipes),
# so we read manually and export each KEY=VALUE pair.
if [ -e .env.secrets ]; then
    while IFS= read -r _line || [ -n "$_line" ]; do
        _line="${_line#"${_line%%[![:space:]]*}"}"
        case "$_line" in
            ""|\#*) continue ;;
            export\ *) _line="${_line#export }" ;;
        esac
        export "$_line"
    done < .env.secrets
fi

# Load local overrides
dotenv_if_exists .envrc.local

# ============================================================================
# WELCOME MESSAGE
# ============================================================================
log_status "Loaded workspace profile: $WORKSPACE_PROFILE"
echo "   CLAUDE_CONFIG_DIR: $CLAUDE_CONFIG_DIR"
echo "   Orchestration: Available"
echo "   AWS Config: $AWS_CONFIG_FILE"
echo "   Kubeconfig: $KUBECONFIG"

# Set iTerm2 tab color{{if eq .Template "personal"}} (blue #19baff){{else if eq .Template "work"}} (green #28c940){{else if eq .Template "client"}} (orange #ff9500){{else}} (gray #7e7f80){{end}}
if [[ "$TERM_PROGRAM" == "iTerm.app" ]]; then
{{if eq .Template "personal"}}  # Personal: Blue (#19baff)
  echo -ne "\033]6;1;bg;red;brightness;25\a"
  echo -ne "\033]6;1;bg;green;brightness;186\a"
  echo -ne "\033]6;1;bg;blue;brightness;255\a"
{{else if eq .Template "work"}}  # Work: Green (#28c940)
  echo -ne "\033]6;1;bg;red;brightness;40\a"
  echo -ne "\033]6;1;bg;green;brightness;201\a"
  echo -ne "\033]6;1;bg;blue;brightness;64\a"
{{else if eq .Template "client"}}  # Client: Orange (#ff9500)
  echo -ne "\033]6;1;bg;red;brightness;255\a"
  echo -ne "\033]6;1;bg;green;brightness;149\a"
  echo -ne "\033]6;1;bg;blue;brightness;0\a"
{{else}}  # Basic: Gray (#7e7f80)
  echo -ne "\033]6;1;bg;red;brightness;126\a"
  echo -ne "\033]6;1;bg;green;brightness;127\a"
  echo -ne "\033]6;1;bg;blue;brightness;128\a"
{{end}}  echo -ne "\033]1;[$WORKSPACE_PROFILE]\007"
fi
