# UTM Template VM Setup for Brainbox

This guide explains how to create a "golden image" macOS VM template in UTM that brainbox can clone for isolated iOS/macOS development environments.

## Overview

Brainbox's UTM backend clones a template VM for each session, providing:
- **Full macOS environment** for Xcode, iOS Simulator, Swift development
- **Isolated sessions** via VM cloning
- **SSH-only access** (no web terminal)
- **VirtioFS volume mounting** for sharing host directories with the VM

## Prerequisites

- **macOS host** (Intel or Apple Silicon)
- **UTM** 4.0+ ([download](https://mac.getutm.app/) or `brew install --cask utm`)
- **UTM command-line tools**: Install via `brew install utmctl` or from UTM preferences
- **macOS installer** (macOS 13 Ventura or later recommended for VirtioFS support)
- **SSH key pair**: Generate with `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519` if you don't have one

## Step 1: Create the Base VM

1. **Open UTM** and click "Create a New Virtual Machine"
2. **Select "Virtualize"** (not Emulate)
3. **Choose macOS** as the operating system
4. **Configure resources:**
   - **Memory**: 8 GB minimum, 16 GB recommended
   - **CPU cores**: 4 minimum, 8 recommended
   - **Disk size**: 100 GB minimum (VMs will be cloned, so this is per-session)
5. **Set VM name**: `brainbox-macos-template`
6. **Install macOS**: Follow the installer prompts to install macOS 13+ (Ventura or Sonoma recommended)

## Step 2: Initial macOS Setup

After macOS installation completes:

1. **Create user account**:
   - Username: `developer` (brainbox expects this username)
   - Password: Set a password (you'll use this for SSH and sudo)
2. **Complete macOS setup wizard**:
   - Skip iCloud sign-in (optional)
   - Disable analytics (recommended)
   - Skip Touch ID setup
3. **Update macOS**: Run Software Update to get the latest patches

## Step 3: Install Development Tools

Open Terminal in the VM and run:

```bash
# Install Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Add Homebrew to PATH
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"

# Install Xcode Command Line Tools
xcode-select --install

# Install Claude Code CLI
brew install claudeai/claude-code/claude-code

# Optional: Install full Xcode from App Store if needed
```

## Step 4: Configure SSH Access

### Enable Remote Login

- Open System Settings → General → Sharing
- Enable **"Remote Login"**
- Set "Allow full disk access for remote users"

### Authorized Keys via VirtioFS

Brainbox uses VirtioFS to inject the host's public SSH key into each VM on boot. The share is configured **once in the template** and inherited by every clone automatically.

**How it works:**
- The template has `$WORKSPACE_HOME/.ssh` (or `~/.ssh`) added as a VirtioFS share via UTM's GUI
- Brainbox clones the template with `utmctl clone`, which copies the registry entry (including security-scoped bookmarks) to the new VM
- A LaunchDaemon in the template copies `id_ed25519.pub` from `/Volumes/My Shared Files/.ssh` into `~/.ssh/authorized_keys` on every boot

**Install the startup script and LaunchDaemon** (run inside the macOS VM as the `developer` user):

```bash
sudo tee /usr/local/bin/brainbox-ssh-setup.sh > /dev/null << 'EOF'
#!/bin/bash
set -euo pipefail
SHARED_BASE="/Volumes/My Shared Files"
USER="developer"
AUTH_KEYS="/Users/${USER}/.ssh/authorized_keys"

SSH_SHARE="${SHARED_BASE}/.ssh"
if [ -d "$SSH_SHARE" ]; then
    PUB_KEY="${SSH_SHARE}/id_ed25519.pub"
    if [ -f "$PUB_KEY" ]; then
        mkdir -p "/Users/${USER}/.ssh"
        chmod 700 "/Users/${USER}/.ssh"
        cp "$PUB_KEY" "$AUTH_KEYS"
        chown "${USER}:staff" "$AUTH_KEYS"
        chmod 600 "$AUTH_KEYS"
        echo "brainbox-ssh: authorized_keys updated"
    else
        echo "brainbox-ssh: id_ed25519.pub not found in share"
    fi
else
    echo "brainbox-ssh: .ssh share not present, skipping"
fi
EOF
sudo chmod +x /usr/local/bin/brainbox-ssh-setup.sh

sudo tee /Library/LaunchDaemons/com.brainbox.ssh-setup.plist > /dev/null << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.brainbox.ssh-setup</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/brainbox-ssh-setup.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/var/log/brainbox-ssh-setup.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/brainbox-ssh-setup.log</string>
</dict>
</plist>
EOF
sudo launchctl load /Library/LaunchDaemons/com.brainbox.ssh-setup.plist
```

After the next reboot, check `/var/log/brainbox-ssh-setup.log` — it should say `authorized_keys updated`.

**Then add the `.ssh` share to the template via UTM GUI** (see Step 7 below). The script is a no-op when the share is absent, so the template can still be booted manually.

### Configure passwordless sudo

```bash
echo 'developer ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/developer
sudo chmod 440 /etc/sudoers.d/developer
sudo -n true   # verify: should return immediately with no password prompt
```

---

## Windows Template (`brainbox-windows-template`)

The Windows template uses the QEMU backend with Bridged networking. VirtioFS is used for directory sharing; the `brainbox-ssh` share works the same way as macOS.

### Prerequisites (one-time, inside the Windows VM)

1. **OpenSSH Server** — Settings → Apps → Optional Features → Add "OpenSSH Server"
   ```powershell
   Set-Service sshd -StartupType Automatic
   Start-Service sshd
   ```
2. **WinFsp** — [download](https://github.com/winfsp/winfsp/releases/latest) — install with default options
3. **VirtioFS for Windows** — from [virtio-win](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/) (`virtio-win-guest-tools.exe`) — install and reboot

### Authorized Keys via VirtioFS

Run in PowerShell as Administrator inside the Windows VM:

```powershell
New-Item -ItemType Directory -Force -Path "C:\brainbox"

$script = @'
# VirtioFS shares are mounted under drive letters by WinFsp/VirtioFSSvc.
# Find the drive letter that contains id_ed25519.pub.
$driveLetter = $null
foreach ($letter in @("Z","Y","X","W","V")) {
    if (Test-Path "${letter}:\id_ed25519.pub") { $driveLetter = $letter; break }
}

if ($null -eq $driveLetter) {
    Write-Output "brainbox-ssh: share not present (non-brainbox boot, skipping)"
    exit 0
}

$user = (Get-WmiObject Win32_ComputerSystem).UserName.Split("\")[-1]
if (-not $user) { $user = $env:USERNAME }
$sshDir  = "C:\Users\$user\.ssh"
$authKey = "$sshDir\authorized_keys"

New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
Copy-Item -Path "${driveLetter}:\id_ed25519.pub" -Destination $authKey -Force
icacls $authKey /inheritance:r /grant "${user}:(F)" | Out-Null
Write-Output "brainbox-ssh: authorized_keys updated from ${driveLetter}:"
'@
Set-Content -Path "C:\brainbox\ssh-setup.ps1" -Value $script

$action   = New-ScheduledTaskAction -Execute "powershell.exe" `
              -Argument "-NonInteractive -WindowStyle Hidden -File C:\brainbox\ssh-setup.ps1"
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName "BrainboxSSHSetup" -TaskPath "\Brainbox\" `
    -Action $action -Trigger $trigger -RunLevel Highest -User "SYSTEM" `
    -Settings $settings -Force
```

Reboot the VM. Check that `C:\Users\<user>\.ssh\authorized_keys` contains the public key contents.

---

## Step 5: Configure Network

For **macOS (Apple VF)** templates, leave the network mode as **"Shared"** (the default). Brainbox discovers the VM's IP via ARP on the `192.168.64.0/24` subnet — port forwarding is silently ignored by the Apple hypervisor.

For **Linux/Windows (QEMU)** templates using Shared/NAT mode, brainbox automatically adds a port forward (guest 22 → host 2200+) to `config.plist` at clone time. No manual network changes needed.

## Step 6: Configure Claude Code (Optional)

Pre-configure Claude Code to skip onboarding:

```bash
# Create config directory
mkdir -p ~/.claude

# Optional: Set up API key if not using 1Password
echo 'export ANTHROPIC_API_KEY=your_key_here' >> ~/.zprofile
```

Brainbox will automatically:
- Inject API keys via SSH
- Set `hasCompletedOnboarding: true`
- Configure bypass permissions mode

## Step 7: Configure Shared Directories

VirtioFS shares are configured **once in the template** and automatically inherited by every clone. Brainbox uses `utmctl clone` which copies the UTM registry entry (including macOS security-scoped bookmarks) to the new VM.

**Required: add the `.ssh` share** (for SSH key injection):

1. **Stop the template VM**
2. In UTM, right-click the VM → **"Edit"**
3. Go to the **Sharing** tab
4. Click **"+"** and select `$WORKSPACE_HOME/.ssh` (or `~/.ssh`)
5. Leave read-only **unchecked** (the LaunchDaemon needs to read files from it)
6. Click **Save**

**Optional: add project directories** you always want available:

Repeat the same steps for any directory you want mounted in every session (e.g., a shared workspace or credentials directory). Each share appears inside the VM at `/Volumes/My Shared Files/<folder-name>`.

```bash
# Inside any provisioned VM:
ls "/Volumes/My Shared Files/.ssh"        # → id_ed25519.pub, known_hosts, ...
ls "/Volumes/My Shared Files/workspace"   # → your project files
```

> **Why template-based?** Apple VF (Virtualization.framework) requires macOS security-scoped bookmarks stored in UTM's preferences registry. These can only be created with user-granted access via UTM's file picker. `utmctl clone` preserves the registry entry, making clones work without any additional setup.

## Step 8: Optimize VM for Cloning

Before shutting down the template:

```bash
# Clear shell history
history -c
rm ~/.zsh_history

# Clear temporary files
sudo rm -rf /tmp/*
sudo rm -rf ~/Library/Caches/*

# Clear logs
sudo rm -rf /var/log/*
```

## Step 9: Final Shutdown

1. **Shut down the VM cleanly**: Apple menu → Shut Down
2. **Verify VM is stopped** in UTM
3. **Do not start the template again** — brainbox will clone it

The template is now ready!

## Step 9: Configure Brainbox

Set the template name in your environment (if not using default):

```bash
export CL_UTM__DEFAULT_TEMPLATE=brainbox-macos-template
```

Other optional settings:

```bash
# SSH port range (default: 2200+)
export CL_UTM__SSH_BASE_PORT=2200

# Custom SSH key (default: ~/.ssh/id_ed25519)
export CL_UTM__SSH_KEY_PATH=~/.ssh/id_rsa

# Custom UTM documents directory (default: auto-detected)
export CL_UTM__DOCS_DIR=~/Library/Containers/com.utmapp.UTM/Data/Documents

# Custom utmctl path (default: auto-detected via PATH; Homebrew at /opt/homebrew/bin/utmctl)
export CL_UTM__UTMCTL_PATH=/opt/homebrew/bin/utmctl
```

## Usage

Create a UTM session via API:

```bash
curl -X POST http://localhost:9999/api/create \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ios-project",
    "backend": "utm",
    "vm_template": "brainbox-macos-template",
    "volumes": ["/Users/you/ios-project:/home/developer/workspace:rw"]
  }'
```

Or via the dashboard:
1. Click "**+ new session**"
2. Select backend: **"UTM macOS VM"**
3. Enter session name and volume mounts
4. Click **Create**

Connect via SSH:

```bash
# SSH port is returned in the API response or shown in dashboard
ssh -p 2200 developer@localhost
```

## Troubleshooting

### SSH Connection Refused

**Symptom**: `Connection refused` when trying to SSH

**Solutions**:
1. Check VM is running: `utmctl status brainbox-<session-name>`
2. Verify port forwarding: Check `config.plist` in the cloned VM's `.utm` package
3. Test SSH inside VM first: Boot the template VM and verify `System Settings → Sharing → Remote Login` is enabled

### VirtioFS Mount Fails

**Symptom**: Shared directories not visible in VM

**Solutions**:
1. Requires **macOS 13+** guest (Ventura or later)
2. Check mount command: `sudo mount_virtiofs <share_tag> /Volumes/<mount_point>`
3. Verify share tag in `config.plist` under `SharedDirectories`

### VM Clone Slow

**Symptom**: Provisioning takes 60+ seconds

**Expected**: VM cloning is slower than Docker container creation. UTM must copy the entire `.utm` package (50-100 GB).

**Optimization**:
- Store VMs on fast SSD
- Use sparse disk images (UTM default)
- Reduce VM disk size if possible

### utmctl Not Found

**Symptom**: `utmctl: command not found`

**Solution**: Install via Homebrew: `brew install utmctl` or set `CL_UTM__UTMCTL_PATH`

### "Template not found" Error

**Symptom**: Brainbox can't find template VM

**Solution**: Verify template name matches exactly (case-sensitive):
```bash
ls ~/Library/Containers/com.utmapp.UTM/Data/Documents/
# Should show: brainbox-macos-template.utm
```

## Maintenance

### Updating the Template

To update Xcode, Homebrew, or other tools:

1. **Clone the template manually** in UTM (right-click → Clone)
2. **Boot the clone** (not the original template)
3. **Update software**:
   ```bash
   brew update && brew upgrade
   softwareupdate --install --recommended
   ```
4. **Clean up** (see Step 7 above)
5. **Shut down** the clone
6. **Rename clone** to `brainbox-macos-template` (delete old template first)

### Disk Space Management

Each cloned VM is 50-100 GB. Monitor usage:

```bash
# List all brainbox VMs
ls ~/Library/Containers/com.utmapp.UTM/Data/Documents/brainbox-*.utm

# Calculate total disk usage
du -sh ~/Library/Containers/com.utmapp.UTM/Data/Documents/brainbox-*.utm
```

Brainbox automatically deletes VMs on recycle, but orphaned VMs may accumulate if crashes occur. Manually clean up:

```bash
# List stopped VMs
utmctl list

# Delete specific VM
rm -rf ~/Library/Containers/com.utmapp.UTM/Data/Documents/brainbox-<name>.utm
```

## Security Considerations

### VM Isolation

- **VMs are NOT sandboxed** like Docker containers
- Each VM has full macOS capabilities (network, filesystem, etc.)
- Use separate user accounts or FileVault encryption if handling sensitive data

### SSH Keys

- Brainbox uses **passwordless SSH** with key-based authentication
- Protect your private key: `chmod 600 ~/.ssh/id_ed25519`
- Consider using separate keys for brainbox VMs vs. production systems

### Network Access

- VMs use NAT networking (isolated from host network by default)
- Port forwarding exposes SSH on localhost only
- No direct incoming connections from external networks

## Limitations

See CLAUDE.md "Known Limitations" section for full details:

- **Slow provisioning**: 30-60s vs 1-2s for Docker
- **No labels/metadata**: Cannot filter VMs like Docker containers
- **SSH-only access**: No web terminal integration
- **Large disk footprint**: 50-100 GB per VM
- **macOS 13+ for VirtioFS**: Older guests cannot mount shared folders

## References

- [UTM Documentation](https://docs.getutm.app/)
- [UTM macOS Guest Setup](https://docs.getutm.app/guest-support/macos/)
- [VirtioFS Mounting](https://docs.getutm.app/guest-support/macos/#shared-directories)
- [utmctl CLI Reference](https://docs.getutm.app/advanced/scripting/)
