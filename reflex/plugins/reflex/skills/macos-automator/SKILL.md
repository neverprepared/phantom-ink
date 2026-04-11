---
name: macos-automator
description: Automator workflow types, Quick Actions, Folder Actions, shell script integration, and CLI testing. Use when building Automator workflows, creating Folder Actions, making Quick Actions (Finder context menu), or automating repetitive file operations on macOS.
---

# macOS Automator Skill

> Build no-code/low-code automation workflows for macOS using Automator.app, Quick Actions, and Folder Actions.

## Overview

Automator is macOS's built-in visual automation tool. It chains actions into workflows that can run as standalone apps, Quick Actions (right-click menu), Folder Actions (trigger on folder changes), print plugins, or calendar alarms. For scriptable steps, use "Run Shell Script" and "Run AppleScript" actions. Automator workflows are stored as `.workflow` bundles.

## When to Use

- Batch file operations (rename, convert, resize, move)
- Adding items to the Finder right-click context menu (Quick Actions)
- Triggering automation when files are added to a folder (Folder Actions)
- Building simple user-facing workflows without writing full apps
- Processing drag-and-dropped files or selected items
- Scheduling automation via Calendar Alarm workflows

---

## Workflow Types

| Type | File Extension | Launch Method | Input Source |
|---|---|---|---|
| **Application** | `.app` | Double-click or CLI | Files/folders dropped onto it |
| **Quick Action** | `.workflow` | Finder right-click / Services menu | Selected files or text in apps |
| **Folder Action** | `.workflow` | Automatic on folder change | Files added to the watched folder |
| **Print Plugin** | `.workflow` | Print dialog | Document being printed |
| **Calendar Alarm** | `.workflow` | Calendar.app event | None (scheduled) |
| **Image Capture Plugin** | `.workflow` | Image Capture app | Images from camera/scanner |

---

## Building Workflows in Automator.app

### Step-by-Step Process

1. Open Automator.app (`/Applications/Automator.app`)
2. Select workflow type at the "Choose a type" dialog
3. Use the **Action Library** (left panel) to search and drag actions
4. Configure each action in the center panel
5. Chain actions — output of one feeds into the next
6. Use **Options** checkbox per action to handle errors (continue on failure)
7. Test with **Run** button (▶) in toolbar
8. Save: `File > Save` — saves to `~/Library/Workflows/` (Quick Actions) or chosen location

### Key Actions

**File & Folder Management**
- `Get Specified Finder Items` — hardcoded file list as starting point
- `Get Selected Finder Items` — items selected in Finder (use in Quick Actions)
- `Filter Finder Items` — filter by name, date, kind, size
- `Rename Finder Items` — batch rename with date, counter, find/replace, case
- `Move Finder Items` — move to specified folder
- `Copy Finder Items` — copy to specified folder
- `New Folder` — create a folder and pass through its path
- `Get Folder Contents` — expand folder to list of files

**Media Processing**
- `Scale Images` — resize images (creates copies or in-place)
- `Crop Images` — crop to dimensions or proportions
- `Convert Format of Images` — JPEG/PNG/TIFF/GIF/BMP conversion
- `New PDF from Images` — combine images into one PDF
- `Combine PDF Pages` — merge multiple PDFs

**Text & Data**
- `Get Contents of TextEdit Document` — extract text from .rtf/.txt
- `Get Contents of Webpage` — fetch URL content
- `Filter Paragraphs` — filter text lines by pattern

**System**
- `Run Shell Script` — execute shell commands, pass/return data
- `Run AppleScript` — run AppleScript, access Automator variables
- `Display Notification` — system notification
- `Speak Text` — text-to-speech
- `Open Finder Items` — open files in default app
- `Pause` — insert a timed delay

---

## Run Shell Script Action

The most flexible action — runs arbitrary shell code with access to workflow input.

```bash
# Pass input as arguments (set "Pass input:" to "as arguments")
for f in "$@"; do
  echo "Processing: $f"
  # do something with each file
  sips -z 800 600 "$f"  # resize image
done

# Pass input via stdin (set "Pass input:" to "to stdin")
# Input arrives as newline-separated file paths
while IFS= read -r line; do
  echo "Got: $line"
done

# Return output to next action
# Print to stdout — becomes input for the next action
echo "/tmp/output.txt"
```

Shell action configuration:
- **Shell**: `/bin/zsh` or `/bin/bash` (avoid `/bin/sh` for arrays)
- **Pass input**: `as arguments` (each item is `$1`, `$2`, ...) or `to stdin`
- Output printed to stdout passes to the next action as input

---

## Run AppleScript Action

Access Automator variables and workflow input with full AppleScript power.

```applescript
-- The 'input' variable contains items passed from previous action
-- 'parameters' contains workflow variables (set via Set Value of Variable action)

on run {input, parameters}
  set fileList to input  -- list of file paths (POSIX strings or aliases)

  repeat with f in fileList
    set fPath to f as string
    -- process each file
    tell application "Finder"
      set fName to name of (POSIX file fPath as alias)
      display notification "Processing: " & fName
    end tell
  end repeat

  -- Return value becomes input for next action
  -- Return input unchanged to pass through
  return input
end run
```

---

## Quick Actions (Finder Context Menu)

Quick Actions appear in Finder's right-click menu and the Touch Bar.

### Creating a Quick Action

1. New Document → **Quick Action**
2. Set "Workflow receives current" to **files or folders** in **Finder.app**
3. Add your actions
4. Save to `~/Library/Services/My Action.workflow`

Quick Actions install automatically — right-click a file in Finder to verify.

### Install/Remove Quick Actions

```bash
# Quick Actions live here (auto-discovered by macOS)
ls ~/Library/Services/

# Install (copy workflow bundle)
cp -r "My Action.workflow" ~/Library/Services/

# Remove
rm -rf ~/Library/Services/"My Action.workflow"

# System-wide Quick Actions (requires admin)
ls /Library/Services/
```

### Enable/Disable via System Settings

System Settings → Privacy & Security → Extensions → Finder Extensions

---

## Folder Actions

Folder Actions trigger automatically when files are added to a watched folder.

### Creating a Folder Action

1. New Document → **Folder Action**
2. Set the watched folder at the top of the workflow
3. Add actions (input = newly added files)
4. Save to `~/Library/Workflows/Applications/Folder Actions/`

### Attaching a Folder Action

**Via Finder**: Right-click any folder → Services → Folder Actions Setup
**Via script**:

```applescript
-- Attach a workflow to a folder
tell application "System Events"
  set folderPath to POSIX file "/Users/me/Downloads" as alias
  set workflowPath to POSIX file "~/Library/Workflows/Applications/Folder Actions/My Action.workflow" as alias
  make new folder action at end of folder actions of folder folderPath ¬
    with properties {name:"My Action", path:workflowPath}
end tell
```

### Enable/Disable Folder Actions System-Wide

```bash
# Enable folder actions daemon
osascript -e 'tell application "System Events" to set folder actions enabled to true'

# Check status
osascript -e 'tell application "System Events" to return folder actions enabled'
```

Folder action logs appear in Console.app — filter by `com.apple.automator` or `FolderActionsDispatcher`.

---

## Testing Workflows via CLI

```bash
# Run any workflow from the command line
automator /path/to/My\ Workflow.workflow

# Pass input files as arguments
automator -i /path/to/file1.jpg ~/Library/Services/"Resize Image.workflow"

# Run with input from stdin (newline-delimited paths)
echo -e "/tmp/a.jpg\n/tmp/b.jpg" | automator ~/Library/Services/"My Action.workflow"

# Check exit code
automator My\ Workflow.workflow
echo "Exit: $?"

# Run in verbose mode (no direct flag, but Console shows logs)
# Watch Console.app for Automator messages during testing
```

---

## Variables in Workflows

Automator has a built-in variable system for passing values between non-adjacent actions.

- **Set Value of Variable** action — store a value
- **Get Value of Variable** action — retrieve it later
- **Ask for Text** action — prompt user at runtime
- **Date** variable — current date/time (useful in rename patterns)

```applescript
-- In Run AppleScript, access workflow variables via the 'parameters' dict
on run {input, parameters}
  set myVar to value of variable "MyVarName" of parameters
  return input
end run
```

---

## Exporting and Distributing Workflows

```bash
# Quick Actions: already installable by copying .workflow bundle
cp -r "My Action.workflow" ~/Library/Services/

# Application: save as .app — double-clickable, drag-and-drop target
# File > Export... → select "Application" type

# Zip for sharing
zip -r "My\ Workflow.zip" "My Action.workflow"

# Share as Application
# Recipients double-click the .app — no Automator.app needed
```

---

## Common Recipes

### Batch Rename Files with Date Prefix

1. **Get Selected Finder Items**
2. **Rename Finder Items** → Add Date or Time → Before Name → Format: YYYY-MM-DD

### Convert Images to JPEG

1. **Get Selected Finder Items**
2. **Copy Finder Items** → `~/Desktop/Converted/` (preserve originals)
3. **Convert Format of Images** → JPEG, quality 85%

### Auto-Process Downloads

Folder Action on `~/Downloads`:
1. **Filter Finder Items** → Kind is PDF
2. **Move Finder Items** → `~/Documents/PDFs/`

### Run Script on Selected Files

Quick Action on Files in Finder:
1. **Get Selected Finder Items**
2. **Run Shell Script** → Pass input as arguments

```bash
for f in "$@"; do
  # your processing here
  md5 "$f"
done
```

---

## Refinement Notes

- [ ] Add PDF compression workflow example
- [ ] Add "combine PDFs" step-by-step
- [ ] Document troubleshooting Folder Actions (permissions, SIP, Full Disk Access)
- [ ] Add Calendar Alarm workflow example for scheduled tasks
