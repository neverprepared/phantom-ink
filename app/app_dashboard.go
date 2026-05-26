package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// GetDashboardLayout returns the stored JSON layout for a profile.
// Returns "" when no layout has been saved (frontend falls back to default).
func (a *App) GetDashboardLayout(profile string) string {
	if a.db == nil {
		return ""
	}
	key := "dashboard_layout:" + strings.TrimSpace(profile)
	return a.db.GetSetting(key, "")
}

// SaveDashboardLayout persists the JSON layout string for a profile.
func (a *App) SaveDashboardLayout(profile, layout string) error {
	if a.db == nil {
		return fmt.Errorf("database not initialized")
	}
	key := "dashboard_layout:" + strings.TrimSpace(profile)
	return a.db.SetSetting(key, layout)
}

// GetWidgetCount returns a count of items in the given brainbox collection,
// optionally filtered by status. api must be one of: "sessions", "hub_tasks", "repos".
func (a *App) GetWidgetCount(api, filterJSON string) (int, error) {
	var filter struct {
		Status string `json:"status"`
	}
	if filterJSON != "" {
		if err := json.Unmarshal([]byte(filterJSON), &filter); err != nil {
			return 0, fmt.Errorf("invalid filter JSON: %w", err)
		}
	}

	switch api {
	case "sessions":
		sessions, err := a.client.ListSessions()
		if err != nil {
			return 0, err
		}
		if filter.Status == "" {
			return len(sessions), nil
		}
		// Sessions have an Active bool, not a status string.
		// Treat "active" as filter.Status == "active".
		count := 0
		want := strings.EqualFold(filter.Status, "active")
		for _, s := range sessions {
			if s.Active == want {
				count++
			}
		}
		return count, nil

	case "hub_tasks":
		tasks, err := a.client.ListTasks(filter.Status, "")
		if err != nil {
			return 0, err
		}
		return len(tasks), nil

	case "repos":
		repos, err := a.client.ListRepos("")
		if err != nil {
			return 0, err
		}
		return len(repos), nil

	default:
		return 0, fmt.Errorf("unknown api %q: must be sessions, hub_tasks, or repos", api)
	}
}

// profileEnv returns os.Environ() overlaid with vars from the shell-profiler
// volatile cache at $TMPDIR/sp-profiles/{name}/.env. Unknown profile names or
// missing cache files are silently ignored — the base env is returned as-is.
func profileEnv(name string) []string {
	base := os.Environ()
	if name == "" {
		return base
	}
	tmpdir := os.Getenv("TMPDIR")
	if tmpdir == "" {
		tmpdir = os.TempDir()
	}
	cachePath := filepath.Join(tmpdir, "sp-profiles", name, ".env")
	data, err := os.ReadFile(cachePath)
	if err != nil {
		return base
	}
	overrides := parseEnvFile(data)
	if len(overrides) == 0 {
		return base
	}
	// Build a map of existing keys so we can replace rather than duplicate.
	merged := make([]string, 0, len(base)+len(overrides))
	existing := make(map[string]int, len(base))
	for i, kv := range base {
		if k, _, ok := strings.Cut(kv, "="); ok {
			existing[k] = i
		}
	}
	merged = append(merged, base...)
	for k, v := range overrides {
		entry := k + "=" + v
		if idx, found := existing[k]; found {
			merged[idx] = entry
		} else {
			merged = append(merged, entry)
		}
	}
	return merged
}

// parseEnvFile parses a KEY=VALUE env file, ignoring comments and blank lines.
// Lines may optionally start with "export ".
func parseEnvFile(data []byte) map[string]string {
	result := make(map[string]string)
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimPrefix(line, "export ")
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		// Strip optional surrounding quotes.
		if len(v) >= 2 && ((v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'')) {
			v = v[1 : len(v)-1]
		}
		result[strings.TrimSpace(k)] = v
	}
	return result
}

// expandEnvVars expands $VAR and ${VAR} references using the provided env map.
func expandEnvVars(s string, env map[string]string) string {
	return os.Expand(s, func(key string) string {
		if v, ok := env[key]; ok {
			return v
		}
		return os.Getenv(key)
	})
}

// RunMetricScript executes a shell command and returns its trimmed stdout.
// When a profile is set and direnv is available, the command runs via
// "direnv exec <workspace_home>" so the full activated environment
// (including 1Password-sourced secrets) is available. workspace_home is
// derived from the configured WorkspacesRoot, not the volatile cache.
// A 30-second timeout is enforced.
func (a *App) RunMetricScript(profile, command string) (string, error) {
	if strings.TrimSpace(command) == "" {
		return "", fmt.Errorf("no command configured")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	var cmd *exec.Cmd
	if profile != "" {
		if workspaceHome := a.profileWorkspaceHome(profile); workspaceHome != "" {
			if direnvBin := findDirenv(); direnvBin != "" {
				cmd = exec.CommandContext(ctx, direnvBin, "exec", workspaceHome, "/bin/sh", "-c", command)
				cmd.Env = os.Environ()
			}
		}
	}
	if cmd == nil {
		cmd = exec.CommandContext(ctx, "/bin/sh", "-c", command)
		cmd.Env = profileEnv(profile)
	}

	out, err := cmd.Output()
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) && len(exitErr.Stderr) > 0 {
			return "", fmt.Errorf("exit %d: %s", exitErr.ExitCode(), strings.TrimSpace(string(exitErr.Stderr)))
		}
		return "", fmt.Errorf("command failed: %w", err)
	}
	return strings.TrimSpace(string(out)), nil
}

// profileWorkspaceHome returns the workspace home for a profile by joining
// WorkspacesRoot + profile name. Falls back to the volatile cache value if the
// derived path doesn't exist on disk.
func (a *App) profileWorkspaceHome(profile string) string {
	if profile == "" {
		return ""
	}
	if root := a.config.WorkspacesRoot; root != "" {
		candidate := filepath.Join(root, profile)
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
	}
	return envLookup(profileEnv(profile), "WORKSPACE_HOME")
}

// findDirenv returns the path to the direnv binary, checking PATH and common
// Homebrew locations. Returns "" if not found.
func findDirenv() string {
	if p, err := exec.LookPath("direnv"); err == nil {
		return p
	}
	for _, candidate := range []string{"/opt/homebrew/bin/direnv", "/usr/local/bin/direnv"} {
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
	}
	return ""
}

// envLookup returns the value of key in an env slice (KEY=VALUE format).
func envLookup(env []string, key string) string {
	prefix := key + "="
	for _, kv := range env {
		if strings.HasPrefix(kv, prefix) {
			return kv[len(prefix):]
		}
	}
	return ""
}

// FetchMetricUrl fetches a URL and extracts a value from the JSON response as a string.
// path is a dot-notation key path (e.g. "data.count"); leave empty to parse the root value.
// header is an optional "Header-Name: value" string (e.g. "Authorization: Bearer $MY_TOKEN").
// $VAR references in url and header are expanded using the profile's env vars.
func (a *App) FetchMetricUrl(profile, url, path, header string) (string, error) {
	if strings.TrimSpace(url) == "" {
		return "", fmt.Errorf("no URL configured")
	}
	if profile != "" {
		envSlice := profileEnv(profile)
		envMap := make(map[string]string, len(envSlice))
		for _, kv := range envSlice {
			if k, v, ok := strings.Cut(kv, "="); ok {
				envMap[k] = v
			}
		}
		url = expandEnvVars(url, envMap)
		header = expandEnvVars(header, envMap)
	}
	req, err := http.NewRequestWithContext(context.Background(), "GET", url, nil)
	if err != nil {
		return "", fmt.Errorf("invalid URL: %w", err)
	}
	if header != "" {
		parts := strings.SplitN(header, ": ", 2)
		if len(parts) == 2 {
			req.Header.Set(parts[0], parts[1])
		}
	}
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	var data any
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return "", fmt.Errorf("JSON decode: %w", err)
	}
	val, err := walkJSONPath(data, path)
	if err != nil {
		return "", err
	}
	return jsonToString(val)
}

func walkJSONPath(data any, path string) (any, error) {
	if path == "" {
		return data, nil
	}
	parts := strings.SplitN(path, ".", 2)
	m, ok := data.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("expected object at %q, got %T", parts[0], data)
	}
	val, exists := m[parts[0]]
	if !exists {
		return nil, fmt.Errorf("key %q not found", parts[0])
	}
	if len(parts) == 1 {
		return val, nil
	}
	return walkJSONPath(val, parts[1])
}

func jsonToInt(v any) (int, error) {
	switch n := v.(type) {
	case float64:
		return int(n), nil
	case int:
		return n, nil
	case string:
		return strconv.Atoi(strings.TrimSpace(n))
	default:
		return 0, fmt.Errorf("value is %T, not a number", v)
	}
}

func jsonToString(v any) (string, error) {
	switch n := v.(type) {
	case float64:
		if n == float64(int(n)) {
			return strconv.Itoa(int(n)), nil
		}
		return strconv.FormatFloat(n, 'f', -1, 64), nil
	case int:
		return strconv.Itoa(n), nil
	case string:
		return n, nil
	case bool:
		if n {
			return "true", nil
		}
		return "false", nil
	case nil:
		return "", nil
	default:
		return fmt.Sprintf("%v", v), nil
	}
}
