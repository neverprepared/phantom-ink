# Environment variables for workspace profile: {{.ProfileName}}
# Template: {{.Template}}
#
# This file is loaded by direnv via dotenv_if_exists in .envrc
# Add tool-specific paths and non-secret config here (not in .envrc)
# Secrets belong in .env.secrets (1Password FIFO mount or plaintext)

# Workspace shortcut
H="$WORKSPACE_HOME"

# Obsidian vault
# Profile-scoped second-brain vault — used by reflex memory tooling and RAG
OBSIDIAN_VAULT_PATH="$WORKSPACE_HOME/obsidian/vaults/{{.ProfileName}}-memory"

# Git configuration
GIT_CONFIG_GLOBAL="$WORKSPACE_HOME/.gitconfig"

# SSH configuration
# Use workspace-specific SSH config instead of $HOME/.ssh/config
GIT_SSH_COMMAND="ssh -F $WORKSPACE_HOME/.ssh/config"

# XDG Base Directory specification
# Point all XDG-compliant tools to workspace-specific config
XDG_CONFIG_HOME="$WORKSPACE_HOME/.config"

# 1Password SSH Agent
# Point to 1Password SSH agent socket for SSH key management
SSH_AUTH_SOCK="$HOME/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"

# AWS configuration
# Point AWS CLI and SDKs to workspace-specific config and credentials
AWS_CONFIG_FILE="$WORKSPACE_HOME/.aws/config"
AWS_SHARED_CREDENTIALS_FILE="$WORKSPACE_HOME/.aws/credentials"

# Kubernetes configuration
# Point kubectl to workspace-specific kubeconfig
KUBECONFIG="$WORKSPACE_HOME/.kube/config"

# Terraform configuration
# Use workspace-specific Terraform CLI config
TF_CLI_CONFIG_FILE="$WORKSPACE_HOME/.terraformrc"
# Optionally set workspace-specific plugin cache
# TF_PLUGIN_CACHE_DIR="$WORKSPACE_HOME/.terraform.d/plugin-cache"

# Terragrunt configuration
TERRAGRUNT_CONFIG="$WORKSPACE_HOME/.terragrunt-config.hcl"
TERRAGRUNT_DOWNLOAD="$WORKSPACE_HOME/.terragrunt-cache"

# Azure CLI configuration
# Point Azure CLI to workspace-specific config directory
AZURE_CONFIG_DIR="$WORKSPACE_HOME/.azure"

# Google Cloud SDK configuration
# Point gcloud CLI to workspace-specific config directory
CLOUDSDK_CONFIG="$WORKSPACE_HOME/.gcloud"

# Claude Code configuration
# Point Claude Code to workspace-specific config directory
# Claude Code stores settings.json, plugins, projects, and history here
CLAUDE_CONFIG_DIR="$WORKSPACE_HOME/.claude"
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1

# Gemini CLI configuration
# Point Gemini CLI to workspace-specific config directory
GEMINI_CONFIG_DIR="$WORKSPACE_HOME/.config/gemini"

# Codex CLI configuration
# Point Codex to workspace-specific config directory
CODEX_HOME="$WORKSPACE_HOME/.codex"

# Codex plugin directory (reflex codex skills, hooks, config)
# Falls back to $HOME/.codex-plugins if not set
CODEX_PLUGIN_DIR="${WORKSPACE_HOME:-$HOME}/.codex-plugins/reflex"
