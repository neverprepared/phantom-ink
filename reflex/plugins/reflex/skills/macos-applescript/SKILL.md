---
name: macos-applescript
description: osascript/AppleScript fundamentals, JXA, app targeting, error handling, and shell bridging for macOS automation. Use when writing AppleScript or JavaScript for Automation (JXA), invoking osascript from shell, automating macOS apps, or bridging shell scripts with GUI automation.
---

# macOS AppleScript Skill

> Automate macOS applications and system interactions via osascript, AppleScript, and JavaScript for Automation (JXA).

## Overview

AppleScript and JXA are macOS automation languages that control scriptable applications via the Apple Events IPC mechanism. `osascript` is the CLI entry point for both. Use these when you need to drive macOS GUI apps, manipulate files via Finder, interact with system services, or bridge shell automation with app-level control.

## When to Use

- Controlling macOS applications (Finder, Mail, Safari, Terminal, etc.)
- Displaying notifications or dialogs from shell scripts
- Reading/writing clipboard contents programmatically
- Automating repetitive GUI tasks not exposed via CLI
- Triggering Calendar events or Reminders (as a complement to MCP servers)
- Bridging shell scripts with app-level automation

---

## Invoking osascript from Shell

```bash
# Inline one-liner (-e flag)
osascript -e 'display notification "Build complete" with title "CI"'

# Multi-line inline (chain -e flags)
osascript \
  -e 'tell application "Finder"' \
  -e '  activate' \
  -e 'end tell'

# Run a script file
osascript ~/scripts/my_automation.applescript

# Run with arguments ($argv in AppleScript)
osascript my_script.applescript arg1 arg2

# Capture return value
result=$(osascript -e 'return (2 + 2) as string')
echo "$result"   # 4

# Suppress output (-ss: machine-readable output, arrays as newline-delimited)
osascript -ss -e 'return {"a", "b", "c"}'

# Run JXA instead of AppleScript
osascript -l JavaScript -e 'Application("Finder").activate()'
```

---

## AppleScript Fundamentals

### Basic Syntax

```applescript
-- Comments use double dash
-- Variables
set myName to "Claude"
set myNum to 42
set myList to {1, 2, 3}

-- String concatenation
set greeting to "Hello, " & myName & "!"

-- Coercion (type conversion)
set numStr to myNum as string
set strNum to "99" as integer

-- Conditionals
if myNum > 10 then
  display dialog "Big number"
else
  display dialog "Small number"
end if

-- Repeat loops
repeat with i from 1 to 5
  log i
end repeat

-- Repeat with list
repeat with item in myList
  log item
end repeat

-- While-style loop
set counter to 0
repeat while counter < 5
  set counter to counter + 1
end repeat
```

### tell Blocks — Targeting Applications

```applescript
-- Single-line tell
tell application "Finder" to activate

-- Multi-line tell (preferred for multiple commands)
tell application "Finder"
  activate
  set desktop to startup disk
  open home
end tell

-- Nested tell (target app then object)
tell application "Safari"
  tell front window
    set URL of current tab to "https://example.com"
  end tell
end tell

-- Using 'with timeout' (default is 2 minutes)
with timeout of 30 seconds
  tell application "Terminal"
    do script "echo hello"
  end tell
end timeout
```

### Handlers (Functions)

```applescript
-- Define a handler
on greet(personName)
  return "Hello, " & personName & "!"
end greet

-- Call it
set msg to greet("Alice")
display dialog msg

-- Handler with labeled parameters
on makeRect given width:w, height:h
  return {width:w, height:h}
end makeRect

set r to makeRect given width:100, height:50
```

---

## Error Handling

```applescript
-- Basic try/on error
try
  tell application "NonExistentApp"
    activate
  end tell
on error errMsg number errNum
  display dialog "Error " & errNum & ": " & errMsg
end try

-- Re-raise specific errors
try
  set x to 1 / 0
on error errMsg number errNum
  if errNum is -2701 then
    -- Division by zero — handle specifically
    display dialog "Cannot divide by zero"
  else
    error errMsg number errNum  -- re-raise
  end if
end try

-- Error in shell exit code
-- osascript exits non-zero when an unhandled error occurs
-- Use try blocks to control exit behavior
```

Common AppleScript error numbers:
- `-1708` — message not understood (app doesn't support the command)
- `-1719` — can't get (object not found)
- `-128` — user cancelled a dialog
- `-2700` — general scripting error
- `-10000` — Apple event handler failed

---

## Common Patterns

### Notifications and Dialogs

```applescript
-- System notification (no user interaction required)
display notification "Task finished" with title "My Script" subtitle "Step 3 of 3" sound name "Glass"

-- Dialog with buttons
set result to display dialog "Proceed?" buttons {"Cancel", "OK"} default button "OK"
if button returned of result is "OK" then
  -- user confirmed
end if

-- Dialog with text input
set result to display dialog "Enter name:" default answer ""
set userName to text returned of result

-- Choose file
set chosen to choose file with prompt "Select a file:" of type {"public.plain-text"}
set filePath to POSIX path of chosen

-- Choose folder
set chosenFolder to choose folder with prompt "Select folder:"
```

### Clipboard

```applescript
-- Read clipboard
set clipContents to the clipboard

-- Write to clipboard
set the clipboard to "Hello from AppleScript"

-- Write file reference to clipboard (for Finder paste)
set the clipboard to (POSIX file "/path/to/file" as alias)
```

### Finder Operations

```applescript
tell application "Finder"
  -- Get desktop path
  set desktopPath to POSIX path of (path to desktop)

  -- List files in a folder
  set items to every file of folder (POSIX file "/tmp") as alias

  -- Move file
  move POSIX file "/tmp/source.txt" as alias to POSIX file "/tmp/dest/" as alias

  -- Delete (moves to trash)
  delete POSIX file "/tmp/unwanted.txt" as alias

  -- Reveal file in Finder
  reveal POSIX file "/path/to/file" as alias
  activate
end tell
```

### Safari / Web Browsers

```applescript
tell application "Safari"
  -- Open URL in new tab
  tell front window
    set newTab to make new tab
    set URL of newTab to "https://example.com"
  end tell

  -- Get current URL
  set currentURL to URL of current tab of front window

  -- Execute JavaScript in current tab
  do JavaScript "document.title" in current tab of front window
end tell
```

### Running Shell Commands from AppleScript

```applescript
-- do shell script runs in /bin/sh
set output to do shell script "ls /tmp"

-- With admin privileges
do shell script "softwareupdate -l" with administrator privileges

-- Quote file paths properly
set safePath to quoted form of "/path/with spaces/file.txt"
do shell script "cat " & safePath
```

---

## JavaScript for Automation (JXA)

JXA uses the same Apple Events infrastructure but with JavaScript syntax. Invoke with `osascript -l JavaScript`.

```javascript
// Basic app targeting
const app = Application('Finder')
app.activate()

// Get frontmost app
const se = Application('System Events')
const frontApp = se.processes.whose({ frontmost: true })[0].name()

// Notifications
const app2 = Application.currentApplication()
app2.includeStandardAdditions = true
app2.displayNotification('Hello from JXA', {
  withTitle: 'My Script',
  subtitle: 'Step 1'
})

// Run shell command
const app3 = Application.currentApplication()
app3.includeStandardAdditions = true
const result = app3.doShellScript('ls /tmp')

// Safari automation
const safari = Application('Safari')
safari.activate()
safari.windows[0].currentTab.url = 'https://example.com'

// Return value to shell (must be a string or coercible)
'done'
```

JXA gotchas:
- Must add `includeStandardAdditions = true` to get `display dialog`, `do shell script`, etc.
- Object references are lazy — accessing `.name()` calls the Apple Event
- Use `ObjC.import('Foundation')` for Objective-C bridge (advanced)

---

## Returning Values to Shell

```bash
# String return
result=$(osascript -e 'return "hello"')

# Number — gets coerced to string automatically
count=$(osascript -e 'return (42 as string)')

# Boolean
bool=$(osascript -e 'return true as string')  # "true"

# List (comma-separated by default, or use -ss for newlines)
osascript -ss -e 'return {"a", "b", "c"}'
# a
# b
# c

# Parse clipboard content
clip=$(osascript -e 'return the clipboard as text')

# Error detection via exit code
if osascript -e 'tell app "BadApp" to activate' 2>/dev/null; then
  echo "Success"
else
  echo "Failed (exit $?)"
fi
```

---

## Script File Structure

```applescript
-- my_script.applescript
-- Usage: osascript my_script.applescript <arg1>

-- Access command-line args
on run argv
  set firstArg to item 1 of argv
  -- main logic
  return "Result: " & firstArg
end run
```

---

## Refinement Notes

- [ ] Add Mail.app patterns (compose, send, search)
- [ ] Add Music.app automation (play/pause/track info)
- [ ] Add Terminal.app patterns (new window, run commands)
- [ ] Add window management patterns (resize, move, tile)
