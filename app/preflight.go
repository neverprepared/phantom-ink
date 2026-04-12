package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

// PreflightCheck represents a startup requirement and its status.
type PreflightCheck struct {
	Name    string `json:"name"`
	Status  string `json:"status"` // "ok", "warning", "error"
	Message string `json:"message"`
}

// RunPreflightChecks validates the environment on startup.
func (a *App) RunPreflightChecks() []PreflightCheck {
	var checks []PreflightCheck

	// Check Docker is running
	checks = append(checks, checkDockerRunning())

	// Check Docker file sharing (macOS only)
	if isPortOpen(2375) || isDockerAvailable() {
		checks = append(checks, checkDockerFileSharing()...)
	}

	// Check brainbox API reachable
	checks = append(checks, checkBrainboxAPI(a.config.BaseURL))

	return checks
}

func isDockerAvailable() bool {
	_, err := os.Stat("/var/run/docker.sock")
	return err == nil
}

func checkDockerRunning() PreflightCheck {
	if isDockerAvailable() {
		return PreflightCheck{Name: "Docker", Status: "ok", Message: "Docker is running"}
	}
	return PreflightCheck{Name: "Docker", Status: "error", Message: "Docker is not running — start Docker Desktop"}
}

func checkBrainboxAPI(baseURL string) PreflightCheck {
	if isPortOpen(9999) {
		return PreflightCheck{Name: "Brainbox API", Status: "ok", Message: "API reachable at " + baseURL}
	}
	return PreflightCheck{Name: "Brainbox API", Status: "warning", Message: "API not reachable at " + baseURL}
}

// checkDockerFileSharing reads Docker Desktop's settings to verify required
// paths are in the file sharing list. macOS only.
func checkDockerFileSharing() []PreflightCheck {
	var checks []PreflightCheck

	home := os.Getenv("HOME")
	settingsPath := filepath.Join(home, "Library", "Group Containers",
		"group.com.docker", "settings-store.json")

	data, err := os.ReadFile(settingsPath)
	if err != nil {
		// Not Docker Desktop, or can't read settings — skip
		return nil
	}

	var settings map[string]interface{}
	if err := json.Unmarshal(data, &settings); err != nil {
		return nil
	}

	raw, ok := settings["FilesharingDirectories"]
	if !ok {
		return nil
	}

	// Parse the shared directories
	var shared []string
	switch v := raw.(type) {
	case string:
		if err := json.Unmarshal([]byte(v), &shared); err != nil {
			return nil
		}
	case []interface{}:
		for _, item := range v {
			if s, ok := item.(string); ok {
				shared = append(shared, s)
			}
		}
	}

	// Paths that need to be shared
	required := map[string]string{
		"/opt/homebrew": "Homebrew (required for reflex plugin mount)",
		filepath.Join(home, "workspaces"): "Workspaces (required for profile mounts)",
	}

	for path, desc := range required {
		if isPathShared(path, shared) {
			checks = append(checks, PreflightCheck{
				Name:    "Docker File Sharing: " + desc,
				Status:  "ok",
				Message: path + " is shared",
			})
		} else {
			checks = append(checks, PreflightCheck{
				Name:    "Docker File Sharing: " + desc,
				Status:  "error",
				Message: path + " is not shared — add it in Docker Desktop → Settings → Resources → File Sharing",
			})
		}
	}

	return checks
}

// isPathShared checks if a path is covered by any of the shared directories.
func isPathShared(path string, shared []string) bool {
	path = filepath.Clean(path)
	for _, s := range shared {
		s = filepath.Clean(s)
		if path == s || strings.HasPrefix(path, s+"/") {
			return true
		}
	}
	return false
}
