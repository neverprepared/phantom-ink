---
name: macos-shortcuts
description: Shortcuts app CLI patterns, shell-to-shortcut integration, input/output passing, URL scheme automation, and distributing .shortcut files. Use when running Shortcuts from the terminal, passing data between shortcuts and shell scripts, or building shortcuts that invoke shell commands or osascript.
---

# macOS Shortcuts Skill

> Automate macOS and iOS via the Shortcuts app, CLI integration, and URL scheme triggers.

## Overview

Shortcuts (formerly Workflow) is Apple's cross-platform automation app available on macOS 12+, iOS, and iPadOS. On macOS, shortcuts can be run headlessly from the terminal via the `shortcuts` CLI, triggered by URL schemes, and integrated with shell scripts and AppleScript. Shortcuts excels at multi-step automation that spans Apple apps, on-device actions, and internet services with a visual, no-code interface.

## When to Use

- Running pre-built shortcuts from shell scripts or CI pipelines
- Passing data from shell to a shortcut for processing, and receiving results back
- Building shortcuts that invoke shell scripts or AppleScript inside them
- Triggering automation via URL schemes (from scripts, web apps, or other apps)
- Distributing reusable automations to non-technical users as `.shortcut` files
- Creating Siri-triggered automations that also run from CLI

---

## Shortcuts CLI

The `shortcuts` binary is available on macOS 12 Monterey and later.

### Basic Commands

```bash
# Run a shortcut by name
shortcuts run "My Shortcut"

# List all installed shortcuts
shortcuts list

# List with bundle identifiers
shortcuts list --show-identifiers

# Get shortcut details (name, actions, source)
shortcuts view "My Shortcut"

# Sign a shortcut file (required for distribution on macOS 13+)
shortcuts sign -i unsigned.shortcut -o signed.shortcut --mode anyone

# Import a .shortcut file
shortcuts import -i "My Shortcut.shortcut" -n "My Shortcut"
```

### Running with Input

```bash
# Pass a text string as input
echo "Hello, Shortcuts" | shortcuts run "Process Text"

# Pass a file path as input
shortcuts run "Resize Image" --input-path /path/to/image.jpg

# Pass multiple files
shortcuts run "Batch Process" \
  --input-path /path/to/file1.jpg \
  --input-path /path/to/file2.jpg

# Pass clipboard contents as input (set clipboard first)
echo "my data" | pbcopy
shortcuts run "Process Clipboard"

# stdin as input (for shortcuts expecting text)
printf "line1\nline2\nline3" | shortcuts run "Count Lines"
```

### Capturing Output

```bash
# Capture shortcut output (shortcut must use "Stop and Output" action)
result=$(shortcuts run "Get Current Date")
echo "Result: $result"

# Save output to file
shortcuts run "Generate Report" > /tmp/report.txt

# Pipe output to another command
shortcuts run "Get JSON Data" | jq '.name'

# Check exit code (non-zero on error)
if shortcuts run "My Shortcut"; then
  echo "Shortcut succeeded"
else
  echo "Shortcut failed (exit $?)"
fi
```

### Troubleshooting CLI Issues

```bash
# Shortcuts CLI requires login session (won't work in pure SSH without -t)
ssh -t user@host "shortcuts run 'My Shortcut'"

# If shortcut needs UI interaction, run via osascript instead
osascript -e 'tell application "Shortcuts" to run shortcut "My Shortcut"'

# Check if shortcuts binary is available
which shortcuts   # /usr/bin/shortcuts on macOS 12+
shortcuts --version
```

---

## Passing Input and Output

### Text Input from Shell

In Shortcuts, use **"Receive [Text] from Quick Actions"** as the first action with "If there's no input: Ask each time" or "Stop and output nothing".

```bash
# Shell sends text via stdin
echo "Process this text" | shortcuts run "Text Processor"
```

### File Input

```bash
# Single file
shortcuts run "Image Resizer" --input-path ~/Desktop/photo.jpg

# Glob (shell expands, pass each with --input-path)
for f in ~/Desktop/*.jpg; do
  shortcuts run "Image Resizer" --input-path "$f"
done

# Batch (shortcut receives a list of files if it accepts Files input type)
shortcuts run "Batch Resizer" \
  --input-path ~/Desktop/a.jpg \
  --input-path ~/Desktop/b.jpg
```

### Returning Output from Shortcuts

Use the **"Stop and Output"** action as the last step in your shortcut. Without this, `shortcuts run` produces no stdout.

```
[Your Actions] → [Stop and Output: result]
```

The output type must match what the shell script expects (text, number, file).

```bash
# Receive text output
text=$(shortcuts run "Summarize Text")

# Receive a file path
outPath=$(shortcuts run "Export PDF")
open "$outPath"

# JSON output pattern
json=$(shortcuts run "Get Config JSON")
value=$(echo "$json" | jq -r '.key')
```

---

## Shortcuts That Run Shell Scripts

Inside Shortcuts, use the **"Run Shell Script"** action to execute arbitrary shell code.

### Run Shell Script Action Settings

- **Shell**: `/bin/zsh` (recommended) or `/bin/bash`
- **Input**: Shortcut Input (passed as `$SHORTCUT_INPUT`), or use "from variable" to pass a specific value
- **Run as Administrator**: Check only when needed (prompts for password)

```bash
# Example: inside a Run Shell Script action
# $SHORTCUT_INPUT contains the text/path passed from previous actions
echo "Got: $SHORTCUT_INPUT"
ls -la "$SHORTCUT_INPUT"

# Return value from shell to Shortcuts (use stdout)
date +%Y-%m-%d

# Multi-line shell in the action
#!/bin/zsh
FILE="$SHORTCUT_INPUT"
if [[ -f "$FILE" ]]; then
  wc -l "$FILE" | awk '{print $1}'
else
  echo "0"
fi
```

### Shortcuts That Run AppleScript

Use the **"Run AppleScript"** action:

```applescript
-- 'input' variable = shortcut input passed to this action
on run {input, parameters}
  -- Process input
  set result to "Processed: " & (input as string)
  return result
end run
```

---

## URL Scheme Automation

### Triggering Shortcuts via URL

macOS supports the `shortcuts://` URL scheme for opening and running shortcuts.

```
shortcuts://run-shortcut?name=<shortcut-name>
shortcuts://run-shortcut?name=My%20Shortcut
shortcuts://run-shortcut?name=My%20Shortcut&input=text&text=Hello
```

Launch from shell:
```bash
# Open/run a shortcut (brings Shortcuts.app to foreground)
open "shortcuts://run-shortcut?name=My%20Shortcut"

# With text input
open "shortcuts://run-shortcut?name=Process%20Text&input=text&text=Hello%20World"

# With clipboard as input
echo "payload" | pbcopy
open "shortcuts://run-shortcut?name=My%20Shortcut&input=clipboard"
```

### URL Scheme Parameters

| Parameter | Values | Description |
|---|---|---|
| `name` | URL-encoded shortcut name | Required |
| `input` | `text`, `clipboard`, `url` | Input type |
| `text` | URL-encoded string | Text when `input=text` |

### Triggering from AppleScript

```applescript
-- Run without bringing Shortcuts to foreground
tell application "Shortcuts Events"
  run shortcut "My Shortcut"
end tell

-- Run with input text
tell application "Shortcuts Events"
  run shortcut "My Shortcut" with input "Hello"
end tell

-- Run and get output
set result to (tell application "Shortcuts Events" to run shortcut "My Shortcut" with input "data")
```

Note: `Shortcuts Events` runs shortcuts headlessly; `Shortcuts` (full app) brings the UI to front.

---

## Building Integration Patterns

### Shell → Shortcut → Shell Pipeline

```bash
#!/bin/zsh
# Prepare data
data="$(cat input.txt)"

# Send to shortcut for processing (must use "Stop and Output")
result=$(echo "$data" | shortcuts run "Process and Return")

# Use the result
echo "$result" > output.txt
```

### Shortcut as a Formatter

Build a shortcut that:
1. Receives text input
2. Applies formatting (Markdown → HTML, date formatting, etc.)
3. Outputs via "Stop and Output"

```bash
# Use as a filter in a shell pipeline
cat raw.md | shortcuts run "Markdown to HTML" > formatted.html
```

### Scheduled Shortcut via launchd

```xml
<!-- ~/Library/LaunchAgents/com.me.daily-shortcut.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.me.daily-shortcut</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/shortcuts</string>
    <string>run</string>
    <string>Daily Summary</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/daily-shortcut.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/daily-shortcut.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.me.daily-shortcut.plist
```

---

## Exporting and Distributing Shortcuts

### Export a Shortcut

1. Open Shortcuts.app
2. Right-click the shortcut → Share → Save File → choose location
3. File saved as `Shortcut Name.shortcut`

Or via CLI:
```bash
# Export (macOS 13+: must sign for distribution to others)
shortcuts sign \
  -i "My Shortcut.shortcut" \
  -o "My Shortcut Signed.shortcut" \
  --mode anyone
```

### Signing Modes

| Mode | Description |
|---|---|
| `self` | Signed for your account only |
| `anyone` | Signed for distribution to any Mac |
| (unsigned) | macOS 13+ will warn users on import |

### Importing

```bash
# Import from CLI
shortcuts import -i "My Shortcut.shortcut" -n "My Shortcut"

# Or double-click the .shortcut file in Finder
# Or share via iCloud link → opens Shortcuts.app on any Apple device
```

### iCloud Sharing

In Shortcuts.app: right-click → Share → Copy iCloud Link
Link format: `https://www.icloud.com/shortcuts/<uuid>`

---
