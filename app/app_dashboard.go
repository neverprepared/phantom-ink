package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os/exec"
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

// RunMetricScript executes a shell command and returns its stdout parsed as an integer.
// A 30-second timeout is enforced. Commands run via /bin/sh and inherit the app's environment.
func (a *App) RunMetricScript(command string) (int, error) {
	if strings.TrimSpace(command) == "" {
		return 0, fmt.Errorf("no command configured")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "/bin/sh", "-c", command)
	out, err := cmd.Output()
	if err != nil {
		return 0, fmt.Errorf("command failed: %w", err)
	}
	n, err := strconv.Atoi(strings.TrimSpace(string(out)))
	if err != nil {
		return 0, fmt.Errorf("output %q is not an integer", strings.TrimSpace(string(out)))
	}
	return n, nil
}

// FetchMetricUrl fetches a URL and extracts an integer from the JSON response.
// path is a dot-notation key path (e.g. "data.count"); leave empty to parse the root value.
// header is an optional "Header-Name: value" string (e.g. "Authorization: Bearer TOKEN").
func (a *App) FetchMetricUrl(url, path, header string) (int, error) {
	if strings.TrimSpace(url) == "" {
		return 0, fmt.Errorf("no URL configured")
	}
	req, err := http.NewRequestWithContext(context.Background(), "GET", url, nil)
	if err != nil {
		return 0, fmt.Errorf("invalid URL: %w", err)
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
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return 0, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	var data any
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return 0, fmt.Errorf("JSON decode: %w", err)
	}
	val, err := walkJSONPath(data, path)
	if err != nil {
		return 0, err
	}
	return jsonToInt(val)
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
