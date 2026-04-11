package main

import (
	"os"
	"path/filepath"
	"strings"
)

// readDeveloperAPIKey reads the API key, checking new phantom-ink paths first
// then falling back to legacy developer/ paths.
func readDeveloperAPIKey() string {
	candidates := []string{}

	if xdg := os.Getenv("XDG_CONFIG_HOME"); xdg != "" {
		candidates = append(candidates, filepath.Join(xdg, "phantom-ink", "brainbox", ".api-key"))
		candidates = append(candidates, filepath.Join(xdg, "developer", ".api-key"))
	}
	if ws := os.Getenv("WORKSPACE_HOME"); ws != "" {
		candidates = append(candidates, filepath.Join(ws, ".config", "phantom-ink", "brainbox", ".api-key"))
		candidates = append(candidates, filepath.Join(ws, ".config", "developer", ".api-key"))
	}
	home := os.Getenv("HOME")
	candidates = append(candidates, filepath.Join(home, ".config", "phantom-ink", "brainbox", ".api-key"))
	candidates = append(candidates, filepath.Join(home, ".config", "developer", ".api-key"))

	for _, path := range candidates {
		data, err := os.ReadFile(path)
		if err == nil {
			if key := strings.TrimSpace(string(data)); key != "" {
				return key
			}
		}
	}
	return ""
}
