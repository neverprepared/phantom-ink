#!/bin/bash
# Reflex SessionStart hook
# - Sets up git user from config
# - Checks for recommended plugins

set -euo pipefail

# Consume stdin to avoid blocking the hook runner
cat > /dev/null

# =============================================================================
# Git Configuration Setup
# =============================================================================
# Priority: GIT_CONFIG_GLOBAL > ~/.gitconfig > /etc/gitconfig

# Resolve git user with a single --list call per config source
GIT_USER_NAME=""
GIT_USER_EMAIL=""

if [[ -n "${GIT_CONFIG_GLOBAL:-}" ]] && [[ -f "${GIT_CONFIG_GLOBAL}" ]]; then
  _git_list=$(git config --file "${GIT_CONFIG_GLOBAL}" --list 2>/dev/null || true)
  GIT_USER_NAME=$(printf '%s\n' "$_git_list" | grep '^user\.name=' | head -1 | cut -d= -f2- || true)
  GIT_USER_EMAIL=$(printf '%s\n' "$_git_list" | grep '^user\.email=' | head -1 | cut -d= -f2- || true)
fi

if [[ -z "$GIT_USER_NAME" ]] || [[ -z "$GIT_USER_EMAIL" ]]; then
  _git_global=$(git config --global --list 2>/dev/null || true)
  [[ -z "$GIT_USER_NAME" ]] && GIT_USER_NAME=$(printf '%s\n' "$_git_global" | grep '^user\.name=' | head -1 | cut -d= -f2- || true)
  [[ -z "$GIT_USER_EMAIL" ]] && GIT_USER_EMAIL=$(printf '%s\n' "$_git_global" | grep '^user\.email=' | head -1 | cut -d= -f2- || true)
fi

# Persist git user info to session environment if CLAUDE_ENV_FILE is available
if [[ -n "${CLAUDE_ENV_FILE:-}" ]] && [[ -n "$GIT_USER_NAME" ]]; then
  # Escape backslash, double-quote, dollar sign, and backtick so the env file
  # is safe to source without triggering variable expansion or command execution.
  escaped_name=$(printf '%s' "$GIT_USER_NAME" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\$/\\$/g; s/`/\\`/g')
  {
    echo "export GIT_AUTHOR_NAME=\"${escaped_name}\""
    echo "export GIT_COMMITTER_NAME=\"${escaped_name}\""
    [[ -n "$GIT_USER_EMAIL" ]] && echo "export GIT_AUTHOR_EMAIL=\"${GIT_USER_EMAIL}\""
    [[ -n "$GIT_USER_EMAIL" ]] && echo "export GIT_COMMITTER_EMAIL=\"${GIT_USER_EMAIL}\""
  } >> "$CLAUDE_ENV_FILE"
fi

# =============================================================================
# Plugin Dependency Check
# =============================================================================
# Check if official plugins directory exists
# Plugins are installed to $CLAUDE_CONFIG_DIR/plugins/ (default: ~/.claude/plugins/)
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
PLUGINS_DIR="${CLAUDE_DIR}/plugins"

check_plugin() {
  local plugin_name="$1"
  local plugin_package="$2"

  # Check multiple possible locations
  if [[ -d "${PLUGINS_DIR}/${plugin_name}" ]] || \
     [[ -d "${PLUGINS_DIR}/${plugin_package}" ]] || \
     [[ -d "${CLAUDE_DIR}/marketplace/${plugin_package}" ]]; then
    return 0
  fi
  return 1
}

MISSING_PLUGINS=()
RECOMMENDATIONS=()

# Check for claude-code-templates (provides testing-suite, security-pro, etc.)
if ! check_plugin "claude-code-templates" "anthropics/claude-code-templates"; then
  MISSING_PLUGINS+=("claude-code-templates")
  RECOMMENDATIONS+=("testing-suite, security-pro, documentation-generator")
fi

# Check for claude-code-workflows (provides developer-essentials, etc.)
if ! check_plugin "claude-code-workflows" "anthropics/claude-code-workflows"; then
  MISSING_PLUGINS+=("claude-code-workflows")
  RECOMMENDATIONS+=("developer-essentials, python-development, javascript-typescript")
fi

# Check for superpowers (provides TDD workflows, systematic debugging, etc.)
if ! check_plugin "superpowers" "obra/superpowers-marketplace"; then
  MISSING_PLUGINS+=("superpowers@superpowers-marketplace")
  RECOMMENDATIONS+=("test-driven-development, systematic-debugging, brainstorming, subagent-driven-development")
fi

# =============================================================================
# Plugin Version Check (marketplace users)
# =============================================================================
# Compare installed version against latest on GitHub to notify users of updates.
INSTALLED_VERSION=$(jq -r '.version // empty' "${CLAUDE_PLUGIN_ROOT:?Error: CLAUDE_PLUGIN_ROOT is not set}/.claude-plugin/plugin.json" 2>/dev/null || true)
VERSION_CACHE="${CLAUDE_DIR}/reflex/.version-checked"
LATEST_VERSION=""
# Only hit GitHub when the cache is missing or older than 24 hours (1440 minutes)
if [[ ! -f "$VERSION_CACHE" ]] || [[ -n "$(find "$VERSION_CACHE" -mmin +1440 2>/dev/null)" ]]; then
  LATEST_VERSION=$(curl -sf --max-time 3 \
    "https://raw.githubusercontent.com/mindmorass/reflex/main/plugins/reflex/.claude-plugin/plugin.json" \
    | jq -r '.version // empty' 2>/dev/null) || LATEST_VERSION=""
  if [[ -n "$LATEST_VERSION" ]]; then
    mkdir -p "${CLAUDE_DIR}/reflex"
    printf '%s\n' "$LATEST_VERSION" > "$VERSION_CACHE"
  fi
else
  LATEST_VERSION=$(cat "$VERSION_CACHE")
fi

UPDATE_AVAILABLE=""
if [[ -n "$LATEST_VERSION" && -n "$INSTALLED_VERSION" && "$INSTALLED_VERSION" != "$LATEST_VERSION" ]]; then
  UPDATE_AVAILABLE="yes"
fi

# =============================================================================
# Guardrail Default Setup (enabled by default for safety)
# =============================================================================
GUARDRAIL_STATE="${CLAUDE_DIR}/reflex/guardrail-enabled"
GUARDRAIL_FIRST_RUN="${CLAUDE_DIR}/reflex/.guardrail-initialized"

if [ ! -f "$GUARDRAIL_FIRST_RUN" ]; then
  mkdir -p "${CLAUDE_DIR}/reflex"
  touch "$GUARDRAIL_STATE"
  touch "$GUARDRAIL_FIRST_RUN"
  GUARDRAIL_ENABLED="new"
elif [ -f "$GUARDRAIL_STATE" ]; then
  GUARDRAIL_ENABLED="yes"
else
  GUARDRAIL_ENABLED="no"
fi

# =============================================================================
# MCP Server Status
# =============================================================================
MCP_CATALOG="${CLAUDE_PLUGIN_ROOT:?Error: CLAUDE_PLUGIN_ROOT is not set}/mcp-catalog.json"
MCP_JSON="${WORKSPACE_HOME:-$HOME}/.mcp.json"

MCP_STATUS=""
if [[ -f "$MCP_CATALOG" ]]; then
  TOTAL_SERVERS=$(jq '.servers | length' "$MCP_CATALOG" 2>/dev/null || echo "0")

  if [[ ! -f "$MCP_JSON" ]]; then
    MCP_STATUS="MCP servers: none configured. Select servers with /reflex:mcp select"
  else
    ENABLED=$(jq '.mcpServers | length' "$MCP_JSON" 2>/dev/null || echo "0")
    MCP_STATUS="MCP servers: ${ENABLED}/${TOTAL_SERVERS} enabled. Manage: /reflex:mcp"
  fi
fi

# =============================================================================
# Build Context Output
# =============================================================================
CONTEXT=""

# Add git user info to context
if [[ -n "$GIT_USER_NAME" ]]; then
  CONTEXT="Git user: ${GIT_USER_NAME}"
  [[ -n "$GIT_USER_EMAIL" ]] && CONTEXT="${CONTEXT} <${GIT_USER_EMAIL}>"
  CONTEXT="${CONTEXT}\n"
fi

# Add guardrail status to context (only on first run)
if [[ "$GUARDRAIL_ENABLED" == "new" ]]; then
  CONTEXT="${CONTEXT}\nGuardrails enabled: Destructive operations will be blocked or require confirmation."
  CONTEXT="${CONTEXT}\nManage with: /reflex:guardrail <on|off|status|patterns>\n"
fi

# Add update notification
if [[ "$UPDATE_AVAILABLE" == "yes" ]]; then
  CONTEXT="${CONTEXT}\nReflex update available: ${INSTALLED_VERSION} → ${LATEST_VERSION}"
  CONTEXT="${CONTEXT}\nRun: claude plugin update reflex@mindmorass-reflex\n"
fi

# Add MCP server status
if [[ -n "$MCP_STATUS" ]]; then
  CONTEXT="${CONTEXT}\n${MCP_STATUS}\n"
fi

# Add missing plugins warning
if [[ ${#MISSING_PLUGINS[@]} -gt 0 ]]; then
  CONTEXT="${CONTEXT}\nReflex recommends installing official Claude Code plugins:\n"

  for i in "${!MISSING_PLUGINS[@]}"; do
    CONTEXT="${CONTEXT}\n- ${MISSING_PLUGINS[$i]} (provides: ${RECOMMENDATIONS[$i]})"
  done

  CONTEXT="${CONTEXT}\n\nInstall official plugins: /install-plugin <plugin-name>"
  CONTEXT="${CONTEXT}\nInstall superpowers: /plugin marketplace add obra/superpowers-marketplace && /plugin install superpowers@superpowers-marketplace"
fi

# Output JSON for SessionStart hook (only if we have context)
if [[ -n "$CONTEXT" ]]; then
  # Use printf to expand \n sequences, then pipe through jq for safe JSON encoding
  printf '%b' "$CONTEXT" | jq -Rs '{
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: .
    }
  }'
fi

exit 0
