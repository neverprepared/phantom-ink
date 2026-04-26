# Brainbox Ansible — UTM VM Template Provisioning

Ansible playbooks for provisioning macOS and Windows UTM VM templates used by the brainbox platform. These automate Steps 3-8 of the [UTM setup guide](../docs/utm-setup.md).

## Prerequisites

**On the host (your Mac):**

```bash
brew install ansible
ansible-galaxy collection install community.general ansible.posix
# For Windows targets:
ansible-galaxy collection install ansible.windows chocolatey.chocolatey
```

**On the VM (manual — Steps 1-4 of utm-setup.md):**

1. Create VM in UTM (Virtualize > macOS/Windows)
2. Install the OS
3. Create `developer` user account
4. Enable SSH (Remote Login on macOS, OpenSSH Server on Windows)
5. Copy your SSH public key to the VM for initial access

## Usage

### macOS Template

1. Boot the template VM and note its IP (ARP discovery on `192.168.64.0/24`):
   ```bash
   arp -a | grep 192.168.64
   ```

2. Update the IP in `inventory/macos.ini`

3. Run the playbook (first run needs `--ask-become-pass` for sudo setup):
   ```bash
   ansible-playbook -i inventory/macos.ini macos-template.yml --ask-become-pass
   ```

4. Subsequent runs (passwordless sudo is now configured):
   ```bash
   ansible-playbook -i inventory/macos.ini macos-template.yml
   ```

### Windows Template

1. Boot the template VM
2. Update connection details in `inventory/windows.ini`
3. Run:
   ```bash
   ansible-playbook -i inventory/windows.ini windows-template.yml
   ```

### Re-provision a specific role

```bash
ansible-playbook -i inventory/macos.ini macos-template.yml --tags mcp-servers
ansible-playbook -i inventory/macos.ini macos-template.yml --tags languages
```

## What stays manual

- **VM creation** — UTM GUI (Step 1)
- **OS installation** — UTM installer flow (Step 2)
- **VirtioFS shares** — UTM GUI file picker required for macOS security-scoped bookmarks (Step 7 of utm-setup.md)
- **Template cleanup** — Clear history/caches before final shutdown (Step 8)

## What Ansible does NOT configure

API keys, OAuth tokens, secrets, and per-session config are injected at runtime by `brainbox/src/brainbox/backends/configure.py`. The template only needs the software stack and the SSH key injection daemon.

## Structure

```
roles/
  homebrew/       macOS only — Homebrew + all brew packages
  sudo-setup/     macOS only — passwordless sudo for developer
  languages/      nvm/Node, uv/Python, goenv/Go, rustup/Rust
  cli-tools/      fd, fzf, gh, ripgrep, tree, yq, terraform, kubectl
  cloud-clis/     AWS CLI, Azure CLI (post-install setup)
  ai-tools/       Claude Code, OpenAI Codex
  mcp-servers/    17 npm + 6 Python MCP packages, Playwright, fastembed
  ssh-setup/      LaunchDaemon (macOS) / ScheduledTask (Windows)
  shell-config/   .zprofile/.zshrc (macOS) / PS profile (Windows)
  claude-config/  Onboarding bypass, trusted paths
```

## Updating packages

Package versions and lists are centralized in `inventory/group_vars/all.yml`. Update versions there and re-run the playbook. Keep in sync with `docker/brainbox/Dockerfile`.
