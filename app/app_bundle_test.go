package main

import (
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"phantom-ink/brainbox"
)

// newBundleTestApp builds an App with an in-memory DB (real schema, no bundle
// sources) and a client pointed at the given base URL.
func newBundleTestApp(t *testing.T, baseURL string) *App {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	db := &DB{conn: conn}
	if err := db.migrate(); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	return &App{db: db, client: brainbox.NewClient(baseURL, "hub-key")}
}

// TestSyncProfileBundle_EnvOnly is the "just add GITHUB_TOKEN to 1Password"
// case: no bundle sources enabled, but .env.secrets has vars — env must mirror
// and the missing bundle must be a graceful skip, not an error.
func TestSyncProfileBundle_EnvOnly(t *testing.T) {
	var gotPath, gotKey string
	var gotEnv map[string]string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotKey = r.Header.Get("X-API-Key")
		body, _ := io.ReadAll(r.Body)
		var parsed struct {
			Env map[string]string `json:"env"`
		}
		_ = json.Unmarshal(body, &parsed)
		gotEnv = parsed.Env
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"profile":"personal","saved":true,"count":2}`))
	}))
	t.Cleanup(srv.Close)

	ws := t.TempDir()
	if err := os.WriteFile(filepath.Join(ws, ".env"), []byte("FOO=bar\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ws, ".env.secrets"), []byte("GITHUB_TOKEN=ghs_fake\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	a := newBundleTestApp(t, srv.URL)
	res, err := a.syncProfileBundle("personal", ws, nil)
	if err != nil {
		t.Fatalf("env-only sync should not error (bundle is a graceful skip): %v", err)
	}
	if gotPath != "/api/gateway/profiles/personal/env" {
		t.Errorf("env PUT path = %q", gotPath)
	}
	if gotKey != "hub-key" {
		t.Errorf("auth header = %q, want hub-key", gotKey)
	}
	if gotEnv["FOO"] != "bar" || gotEnv["GITHUB_TOKEN"] != "ghs_fake" {
		t.Errorf("mirrored env = %v", gotEnv)
	}
	if res.EnvCount != 2 {
		t.Errorf("result.EnvCount = %d, want 2", res.EnvCount)
	}
	if res.Saved {
		t.Errorf("bundle should be skipped (Saved=false) with no sources; got Saved=true")
	}
}

// TestSyncProfileBundle_EnvMirrorErrorSurfaces: a broker/hub failure on the env
// PUT must surface as an error (Sync now shows it), not be swallowed.
func TestSyncProfileBundle_EnvMirrorErrorSurfaces(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte(`{"detail":"credentials broker unavailable"}`))
	}))
	t.Cleanup(srv.Close)

	ws := t.TempDir()
	if err := os.WriteFile(filepath.Join(ws, ".env.secrets"), []byte("GITHUB_TOKEN=ghs_fake\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	a := newBundleTestApp(t, srv.URL)
	if _, err := a.syncProfileBundle("personal", ws, nil); err == nil {
		t.Fatal("expected env-mirror failure to surface as an error")
	}
}

// TestSyncProfileBundle_NoEnvNoSources: nothing to sync is not an error.
func TestSyncProfileBundle_NoEnvNoSources(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("no HTTP call expected when there is nothing to sync; got %s %s", r.Method, r.URL.Path)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	t.Cleanup(srv.Close)

	a := newBundleTestApp(t, srv.URL)
	if _, err := a.syncProfileBundle("personal", t.TempDir(), nil); err != nil {
		t.Fatalf("empty sync should be a no-op, not an error: %v", err)
	}
}
