package main

import (
	"fmt"
	"os/exec"
	"regexp"
	"strings"
)

// ttyPattern matches valid TTY paths returned by ps (e.g. "s001", "ttys003", "pts/0").
// Only alphanumeric characters, dots, forward slashes, and hyphens are permitted.
var ttyPattern = regexp.MustCompile(`^[a-zA-Z0-9./\-]+$`)

// pidPattern matches a valid numeric process ID.
var pidPattern = regexp.MustCompile(`^\d+$`)

// LocalProcess represents a running process we can link to a terminal tab.
type LocalProcess struct {
	PID              string `json:"pid"`
	TTY              string `json:"tty"`
	Command          string `json:"command"`
	Name             string `json:"name"`
	CPUPerc          string `json:"cpu_perc"`
	MemMB            string `json:"mem_mb"`
	WorkspaceProfile string `json:"workspace_profile"`
	WorkspaceHome    string `json:"workspace_home"`
}

// FindClaudeProcesses finds running AI agent processes on the host (Claude, Codex, Ollama),
// including their workspace profile from the process environment.
func (a *App) FindClaudeProcesses() ([]LocalProcess, error) {
	// First get PID, TTY, CPU, RSS, command
	cmd := exec.Command("bash", "-c",
		`ps -eo pid=,tty=,%cpu=,rss=,comm= | grep -iE 'claude|codex|ollama' | grep -v grep | grep -v 'phantom-ink'`)
	out, err := cmd.Output()
	if err != nil {
		return nil, nil
	}

	var procs []LocalProcess
	seen := make(map[string]bool) // dedupe by TTY

	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 5 {
			continue
		}
		pid := fields[0]
		tty := fields[1]
		cpuPerc := fields[2]
		rssKB := fields[3]

		if tty == "??" || tty == "-" {
			continue
		}
		if seen[tty] {
			continue
		}
		seen[tty] = true

		// Convert RSS KB → MB
		memMB := "0"
		var kb int
		if _, err := fmt.Sscanf(rssKB, "%d", &kb); err == nil && kb > 0 {
			memMB = fmt.Sprintf("%.1f", float64(kb)/1024.0)
		}

		// Read environment from the process using `ps eww`
		profile, wsHome := readProcessEnv(pid)

		// Derive agent name from the command
		commLower := strings.ToLower(strings.Join(fields[4:], " "))
		name := "claude"
		if strings.Contains(commLower, "codex") {
			name = "codex"
		} else if strings.Contains(commLower, "ollama") {
			name = "ollama"
		}

		procs = append(procs, LocalProcess{
			PID:              pid,
			TTY:              "/dev/" + tty,
			Command:          strings.Join(fields[4:], " "),
			Name:             name,
			CPUPerc:          cpuPerc + "%",
			MemMB:            memMB + " MiB",
			WorkspaceProfile: profile,
			WorkspaceHome:    wsHome,
		})
	}
	return procs, nil
}

// readProcessEnv reads WORKSPACE_PROFILE and WORKSPACE_HOME from a process's
// environment using `ps eww` (macOS). Returns empty strings if not found.
func readProcessEnv(pid string) (profile, wsHome string) {
	// Validate pid is purely numeric before using it in an exec call.
	if !pidPattern.MatchString(pid) {
		return "", ""
	}
	cmd := exec.Command("ps", "eww", "-o", "command=", "-p", pid)
	out, err := cmd.Output()
	if err != nil {
		return "", ""
	}
	// The output is: command args ENV_VAR=value ENV_VAR2=value ...
	// Split on spaces and look for our vars
	for _, part := range strings.Fields(string(out)) {
		if strings.HasPrefix(part, "WORKSPACE_PROFILE=") {
			profile = strings.TrimPrefix(part, "WORKSPACE_PROFILE=")
		} else if strings.HasPrefix(part, "WORKSPACE_HOME=") {
			wsHome = strings.TrimPrefix(part, "WORKSPACE_HOME=")
		}
	}
	return profile, wsHome
}

// FocusTerminalTab finds and activates the terminal tab that owns the given TTY.
// Tries iTerm2 first, then Terminal.app.
func (a *App) FocusTerminalTab(tty string) error {
	// Validate the tty path before embedding it in AppleScript to prevent
	// script injection. TTY paths from ps output consist only of alphanumeric
	// characters, dots, forward slashes, and hyphens (e.g. "/dev/ttys001").
	// Strip a leading "/dev/" prefix that the caller may include, then
	// re-normalise so we always embed the full path ourselves.
	stripped := strings.TrimPrefix(tty, "/dev/")
	if !ttyPattern.MatchString(stripped) {
		return fmt.Errorf("invalid tty path: %q", tty)
	}
	// Re-build the canonical path used in AppleScript.
	safeTTY := "/dev/" + stripped

	// Try iTerm2
	itermScript := fmt.Sprintf(`
tell application "System Events"
	if exists (process "iTerm2") then
		tell application "iTerm2"
			repeat with w in windows
				repeat with t in tabs of w
					repeat with s in sessions of t
						if tty of s is "%s" then
							select t
							tell w to select
							activate
							return "found"
						end if
					end repeat
				end repeat
			end repeat
		end tell
	end if
end tell
return "not_found"`, safeTTY)

	cmd := exec.Command("osascript", "-e", itermScript)
	out, err := cmd.Output()
	if err == nil && strings.TrimSpace(string(out)) == "found" {
		return nil
	}

	// Try Terminal.app
	termScript := fmt.Sprintf(`
tell application "System Events"
	if exists (process "Terminal") then
		tell application "Terminal"
			repeat with w in windows
				repeat with t in tabs of w
					if tty of t is "%s" then
						set selected tab of w to t
						set index of w to 1
						activate
						return "found"
					end if
				end repeat
			end repeat
		end tell
	end if
end tell
return "not_found"`, safeTTY)

	cmd = exec.Command("osascript", "-e", termScript)
	out, err = cmd.Output()
	if err == nil && strings.TrimSpace(string(out)) == "found" {
		return nil
	}

	return fmt.Errorf("could not find terminal tab for %s", safeTTY)
}

