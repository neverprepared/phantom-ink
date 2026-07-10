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
// optionally filtered by status. api must be one of: "sessions", "hub_tasks".
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

	default:
		return 0, fmt.Errorf("unknown api %q: must be sessions, hub_tasks", api)
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

// resolveProfileEnv builds the environment for a profile-scoped job
// deterministically from files on disk — no direnv. direnv keeps its .envrc
// approvals in a per-profile store ($XDG_DATA_HOME/direnv/allow); the app runs
// under a single profile and therefore cannot satisfy approvals for OTHER
// profiles' .envrc files, so "direnv exec <other-profile>" fails closed with
// "is blocked" until each .envrc is manually re-approved from a shell in the
// app's own profile — and re-breaks on every .envrc edit. We sidestep that
// entirely by replicating what a shell-profiler .envrc does for env purposes:
// export the profile identity, prepend {home}/bin to PATH, and layer the
// dotenv files ({home}/.env then {home}/.envrc.local) that hold the profile's
// vars and secrets.
//
// All bundled profiles are static-dotenv only (no `op read` / dynamic
// resolution), so the on-disk .env is the complete, authoritative source —
// unlike the volatile shell-profiler cache profileEnv reads, which is written
// only on `cd` and is frequently absent. Falls back to that cache only when the
// workspace home can't be located on disk.
func (a *App) resolveProfileEnv(profile string) []string {
	base := os.Environ()
	if profile == "" {
		return base
	}
	wh := a.profileWorkspaceHome(profile)
	if wh == "" {
		return profileEnv(profile)
	}

	overrides := map[string]string{
		"WORKSPACE_PROFILE": profile,
		"WORKSPACE_HOME":    wh,
	}
	// dotenv files in .envrc order (.env, then .envrc.local); later wins.
	for _, name := range []string{".env", ".envrc.local"} {
		if data, err := os.ReadFile(filepath.Join(wh, name)); err == nil {
			for k, v := range parseEnvFile(data) {
				overrides[k] = v
			}
		}
	}

	merged := make([]string, len(base))
	copy(merged, base)
	index := make(map[string]int, len(base))
	for i, kv := range base {
		if k, _, ok := strings.Cut(kv, "="); ok {
			index[k] = i
		}
	}
	setVar := func(k, v string) {
		entry := k + "=" + v
		if i, ok := index[k]; ok {
			merged[i] = entry
		} else {
			index[k] = len(merged)
			merged = append(merged, entry)
		}
	}
	for k, v := range overrides {
		setVar(k, v)
	}
	// PATH_add bin: prepend {home}/bin so jobs can call profile-local scripts.
	if binDir := filepath.Join(wh, "bin"); dirExists(binDir) {
		setVar("PATH", binDir+string(os.PathListSeparator)+envLookup(merged, "PATH"))
	}
	return merged
}

// dirExists reports whether path exists and is a directory.
func dirExists(path string) bool {
	fi, err := os.Stat(path)
	return err == nil && fi.IsDir()
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
// When a profile is set, the command runs with that profile's environment
// resolved deterministically from disk (see resolveProfileEnv): the profile
// identity plus its dotenv files ({home}/.env, .envrc.local). A 30-second
// timeout is enforced.
func (a *App) RunMetricScript(profile, command string) (string, error) {
	if strings.TrimSpace(command) == "" {
		return "", fmt.Errorf("no command configured")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, "/bin/sh", "-c", command)
	cmd.Env = a.resolveProfileEnv(profile)

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
		envSlice := a.resolveProfileEnv(profile)
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
