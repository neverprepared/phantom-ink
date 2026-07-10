package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// envMap turns a KEY=VALUE slice into a map for assertions.
func envMap(env []string) map[string]string {
	m := make(map[string]string, len(env))
	for _, kv := range env {
		if k, v, ok := strings.Cut(kv, "="); ok {
			m[k] = v
		}
	}
	return m
}

func TestResolveProfileEnv_LayersDotenvAndIdentity(t *testing.T) {
	root := t.TempDir()
	wh := filepath.Join(root, "lakeview")
	if err := os.MkdirAll(filepath.Join(wh, "bin"), 0o755); err != nil {
		t.Fatal(err)
	}
	// .env holds the profile's vars/secrets (as shell-profiler writes them).
	dotenv := "# comment\nJIRA_API_TOKEN=secret-token\nexport JIRA_URL=\"https://jira.example.com\"\n"
	if err := os.WriteFile(filepath.Join(wh, ".env"), []byte(dotenv), 0o600); err != nil {
		t.Fatal(err)
	}
	// .envrc.local overrides .env (later-wins order).
	if err := os.WriteFile(filepath.Join(wh, ".envrc.local"), []byte("JIRA_URL=https://override.example.com\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	a := &App{config: &Config{WorkspacesRoot: root}}
	got := envMap(a.resolveProfileEnv("lakeview"))

	if got["JIRA_API_TOKEN"] != "secret-token" {
		t.Errorf("JIRA_API_TOKEN = %q, want secret-token", got["JIRA_API_TOKEN"])
	}
	if got["JIRA_URL"] != "https://override.example.com" {
		t.Errorf("JIRA_URL = %q, want the .envrc.local override", got["JIRA_URL"])
	}
	if got["WORKSPACE_PROFILE"] != "lakeview" {
		t.Errorf("WORKSPACE_PROFILE = %q, want lakeview", got["WORKSPACE_PROFILE"])
	}
	if got["WORKSPACE_HOME"] != wh {
		t.Errorf("WORKSPACE_HOME = %q, want %q", got["WORKSPACE_HOME"], wh)
	}
	// {home}/bin is prepended to PATH (replicates PATH_add bin).
	binDir := filepath.Join(wh, "bin")
	if !strings.HasPrefix(got["PATH"], binDir+string(os.PathListSeparator)) {
		t.Errorf("PATH = %q, want it to start with %q", got["PATH"], binDir)
	}
}

func TestResolveProfileEnv_EmptyProfileIsBaseEnv(t *testing.T) {
	a := &App{config: &Config{}}
	if len(a.resolveProfileEnv("")) != len(os.Environ()) {
		t.Errorf("empty profile should return the base process environment unchanged")
	}
}

func TestResolveProfileEnv_MissingHomeDoesNotPanic(t *testing.T) {
	// No WorkspacesRoot and no volatile cache — must degrade, not crash.
	a := &App{config: &Config{}}
	if got := a.resolveProfileEnv("nonexistent-profile"); got == nil {
		t.Error("expected a non-nil env slice even when the profile home can't be located")
	}
}
