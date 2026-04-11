package main

import (
	"fmt"
	"os/exec"
	"strings"
)

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

// FindClaudeProcesses finds running Claude Code processes on the host,
// including their workspace profile from the process environment.
func (a *App) FindClaudeProcesses() ([]LocalProcess, error) {
	// First get PID, TTY, CPU, RSS, command
	cmd := exec.Command("bash", "-c",
		`ps -eo pid=,tty=,%cpu=,rss=,comm= | grep -i 'claude' | grep -v grep | grep -v 'phantom-ink'`)
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

		name := "claude"
		if profile != "" {
			name = "claude (" + profile + ")"
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
return "not_found"`, tty)

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
return "not_found"`, tty)

	cmd = exec.Command("osascript", "-e", termScript)
	out, err = cmd.Output()
	if err == nil && strings.TrimSpace(string(out)) == "found" {
		return nil
	}

	return fmt.Errorf("could not find terminal tab for %s", tty)
}
