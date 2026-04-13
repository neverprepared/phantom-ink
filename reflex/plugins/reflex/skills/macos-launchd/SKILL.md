---
name: macos-launchd
description: launchd plist patterns, agent vs daemon, scheduling (interval and calendar), log routing, environment variables, and debugging with launchctl. Use when creating background services, scheduling recurring tasks, managing startup items, or troubleshooting launchd jobs on macOS.
---

# macOS launchd Skill

> The authoritative macOS process supervisor — schedule tasks, run background services, and manage startup jobs.

## Overview

`launchd` is macOS's PID 1 and the replacement for cron, init, and rc scripts. It manages daemons (system-wide) and agents (per-user). Jobs are defined in XML property list (`.plist`) files. `launchctl` is the control CLI. launchd is more reliable than cron for macOS because it handles boot ordering, log management, and restart policies.

## When to Use

- Running scripts or programs at startup, on a schedule, or on-demand
- Keeping a service running (restart on failure)
- Replacing cron jobs with more robust scheduling
- Running daemons that need to start before login (system daemons)
- Watching for file system changes (not covered here — use FSEvents for that)
- Any "run this in the background on this Mac" requirement

---

## Agent vs Daemon

| | LaunchAgent | LaunchDaemon |
|---|---|---|
| **Runs as** | Logged-in user | root (or specified user) |
| **When** | After user logs in | At boot, before login |
| **Location (user)** | `~/Library/LaunchAgents/` | — |
| **Location (admin)** | `/Library/LaunchAgents/` | `/Library/LaunchDaemons/` |
| **Location (Apple)** | `/System/Library/LaunchAgents/` | `/System/Library/LaunchDaemons/` |
| **Use case** | User-facing tools, cron replacements | System services, privileged daemons |
| **GUI access** | Yes (has UI session) | No |

**Rule of thumb**: If it runs as your user and doesn't need to start before login → `~/Library/LaunchAgents/`. If it needs root or must run at boot → `/Library/LaunchDaemons/`.

---

## Minimal Plist Template

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.example.myjob</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/me/scripts/myjob.py</string>
  </array>

  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
```

**Naming convention**: Reverse-DNS label, matching the filename.
`com.example.myjob` → `~/Library/LaunchAgents/com.example.myjob.plist`

---

## Key Fields Reference

### Identity

```xml
<!-- Required: unique job identifier -->
<key>Label</key>
<string>com.example.myjob</string>
```

### What to Run

```xml
<!-- Preferred: array of program + arguments (no shell interpretation) -->
<key>ProgramArguments</key>
<array>
  <string>/bin/zsh</string>
  <string>-c</string>
  <string>echo "hello" >> /tmp/out.log</string>
</array>

<!-- Alternative: path to executable only (no arguments) -->
<key>Program</key>
<string>/usr/local/bin/myservice</string>
```

Use `ProgramArguments` always. When you need shell features (pipes, redirects, globs), set the first element to `/bin/zsh` or `/bin/bash` and pass `-c` + the command string.

### Scheduling

```xml
<!-- Run immediately when loaded -->
<key>RunAtLoad</key>
<true/>

<!-- Run every N seconds -->
<key>StartInterval</key>
<integer>3600</integer>  <!-- every hour -->

<!-- Run on a calendar schedule (like cron) -->
<key>StartCalendarInterval</key>
<dict>
  <key>Hour</key>
  <integer>9</integer>
  <key>Minute</key>
  <integer>0</integer>
</dict>
<!-- Every day at 09:00 -->

<!-- Multiple calendar times -->
<key>StartCalendarInterval</key>
<array>
  <dict>
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <dict>
    <key>Hour</key><integer>17</integer>
    <key>Minute</key><integer>30</integer>
  </dict>
</array>
<!-- 09:00 and 17:30 daily -->
```

`StartCalendarInterval` keys: `Minute` (0–59), `Hour` (0–23), `Day` (1–31), `Weekday` (0–7, 0 and 7 = Sunday), `Month` (1–12). Omitting a key means "every value" (like `*` in cron).

Cron → launchd equivalents:
| Cron | launchd |
|---|---|
| `0 9 * * *` | Hour=9, Minute=0 |
| `*/15 * * * *` | StartInterval=900 |
| `0 9 * * 1` | Hour=9, Minute=0, Weekday=1 |
| `@reboot` | RunAtLoad=true (no StartInterval) |

### Restart Behavior

```xml
<!-- Keep alive always (service daemon) -->
<key>KeepAlive</key>
<true/>

<!-- Keep alive only on crash (not on clean exit 0) -->
<key>KeepAlive</key>
<dict>
  <key>SuccessfulExit</key>
  <false/>
</dict>

<!-- Keep alive based on path existence -->
<key>KeepAlive</key>
<dict>
  <key>PathState</key>
  <dict>
    <key>/tmp/keep-running</key>
    <true/>
  </dict>
</dict>

<!-- Throttle rapid restarts (seconds between restarts) -->
<key>ThrottleInterval</key>
<integer>30</integer>
```

### Logging

```xml
<!-- Redirect stdout -->
<key>StandardOutPath</key>
<string>/tmp/myjob.out</string>

<!-- Redirect stderr -->
<key>StandardErrorPath</key>
<string>/tmp/myjob.err</string>
```

Note: Files must exist and be writable, OR launchd will create them. Parent directories must exist.

```bash
# Tail logs in real time
tail -f /tmp/myjob.out /tmp/myjob.err

# Rotate logs manually (launchd does not auto-rotate)
# Use newsyslog or logrotate for production log management
```

### Environment Variables

```xml
<!-- Set environment variables for the job -->
<key>EnvironmentVariables</key>
<dict>
  <key>PATH</key>
  <string>/usr/local/bin:/usr/bin:/bin</string>
  <key>HOME</key>
  <string>/Users/me</string>
  <key>MY_API_KEY</key>
  <string>secret-value</string>
</dict>

<!-- Set working directory -->
<key>WorkingDirectory</key>
<string>/Users/me/projects/myapp</string>
```

Important: launchd jobs do NOT inherit your shell environment. Always set `PATH` explicitly if your script uses tools in `/usr/local/bin` or `/opt/homebrew/bin`.

### User and Group (Daemons Only)

```xml
<!-- Run as specific user (LaunchDaemons only) -->
<key>UserName</key>
<string>nobody</string>

<key>GroupName</key>
<string>nobody</string>
```

---

## launchctl Commands

### Loading and Unloading (Legacy — macOS < 10.10)

```bash
# Load (register and start if RunAtLoad=true)
launchctl load ~/Library/LaunchAgents/com.example.myjob.plist

# Unload (stop and unregister)
launchctl unload ~/Library/LaunchAgents/com.example.myjob.plist

# Load disabled job
launchctl load -w ~/Library/LaunchAgents/com.example.myjob.plist
```

### Bootstrap / Bootout (Modern — macOS 10.11+)

```bash
# Bootstrap (register) — use domain target
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.myjob.plist

# Bootout (unregister + stop)
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.example.myjob.plist

# For system daemons (as root)
sudo launchctl bootstrap system /Library/LaunchDaemons/com.example.daemon.plist
sudo launchctl bootout system /Library/LaunchDaemons/com.example.daemon.plist
```

Domain targets:
- `gui/<uid>` — per-user agent (e.g., `gui/501`)
- `user/<uid>` — user daemon
- `system` — system daemon (root)

### Start, Stop, Kickstart

```bash
# Manually trigger a job now (ignores schedule)
launchctl start com.example.myjob

# Stop a running job
launchctl stop com.example.myjob

# Kickstart (modern, force restart)
launchctl kickstart -k gui/$(id -u)/com.example.myjob

# Kill without restarting (SIGTERM)
launchctl kill TERM gui/$(id -u)/com.example.myjob
```

### Listing and Status

```bash
# List all loaded jobs (with PID and exit status)
launchctl list

# Find a specific job
launchctl list | grep com.example

# Detailed job info (modern)
launchctl print gui/$(id -u)/com.example.myjob

# Check last exit code
launchctl list com.example.myjob
# Output: { "PID" = 12345; "Label" = "com.example.myjob"; "LastExitStatus" = 0; }
```

---

## Debugging and Troubleshooting

### Exit Codes

`launchctl list com.example.myjob` shows `LastExitStatus`. Common codes:

| Exit Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Generic error in script |
| `2` | Misuse of shell built-in |
| `126` | Permission denied (not executable) |
| `127` | Command not found (check PATH) |
| `n * 256 + 11` | Killed by signal (e.g., `11` = SIGSEGV) |
| `-2` | Job failed to spawn |

### Common Pitfalls

```bash
# 1. PATH is not inherited — always set explicitly
# Wrong: relying on /opt/homebrew/bin being in PATH
# Right: set EnvironmentVariables/PATH in plist

# 2. Script not executable
chmod +x /path/to/script.sh

# 3. Plist syntax error — validate with plutil
plutil -lint ~/Library/LaunchAgents/com.example.myjob.plist

# 4. Job not found after copy — must be loaded
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.myjob.plist

# 5. After editing plist — must reload
launchctl bootout gui/$(id -u)/com.example.myjob
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.myjob.plist

# 6. Log files not created — parent dir must exist
mkdir -p /tmp/mylogs
```

### Viewing Logs in Console.app

1. Open Console.app
2. Search for the job Label (e.g., `com.example.myjob`)
3. Filter by Process: `launchd` for spawn errors

### plist Validation

```bash
# Validate plist syntax
plutil -lint ~/Library/LaunchAgents/com.example.myjob.plist
# Output: "OK" or error with line number

# Convert to readable JSON for review
plutil -convert json -o - ~/Library/LaunchAgents/com.example.myjob.plist | jq .

# Convert JSON back to plist
plutil -convert xml1 job.json -o com.example.myjob.plist
```

---

## Complete Examples

### Daily Backup Agent

```xml
<!-- ~/Library/LaunchAgents/com.me.daily-backup.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.me.daily-backup</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-c</string>
    <string>/Users/me/scripts/backup.sh &gt;&gt; /tmp/backup.log 2&gt;&amp;1</string>
  </array>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>2</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>

  <key>StandardOutPath</key>
  <string>/tmp/backup.out</string>

  <key>StandardErrorPath</key>
  <string>/tmp/backup.err</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>/Users/me</string>
  </dict>
</dict>
</plist>
```

### Persistent Service (Keep Alive)

```xml
<!-- ~/Library/LaunchAgents/com.me.myservice.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.me.myservice</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/myservice</string>
    <string>--port</string>
    <string>8080</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>

  <key>ThrottleInterval</key>
  <integer>30</integer>

  <key>StandardOutPath</key>
  <string>/tmp/myservice.out</string>

  <key>StandardErrorPath</key>
  <string>/tmp/myservice.err</string>

  <key>WorkingDirectory</key>
  <string>/usr/local/var/myservice</string>
</dict>
</plist>
```

### Install Helper Script

```bash
#!/bin/zsh
# install-launchagent.sh

LABEL="com.me.myjob"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

# Generate plist
cat > "$PLIST" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.me.myjob</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/me/scripts/myjob.py</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/myjob.out</string>
  <key>StandardErrorPath</key>
  <string>/tmp/myjob.err</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST" || { echo "Plist invalid"; exit 1; }

# Unload existing (ignore errors if not loaded)
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true

# Load
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl list "$LABEL"
echo "Installed and loaded: $LABEL"
```

---
