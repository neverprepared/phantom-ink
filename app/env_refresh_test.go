package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"phantom-ink/brainbox"
)

func TestParseDotenvText(t *testing.T) {
	got := parseDotenvText("# c\nFOO=bar\nexport BAZ=qux\nQ=\"a b\"\nS='x'\nD=\"line\\nbreak\"\n123BAD=skip\nNOEQ\n")
	want := map[string]string{
		"FOO": "bar",
		"BAZ": "qux",
		"Q":   "a b",
		"S":   "x",
		"D":   "line\nbreak", // \n unescaped inside double quotes
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("parseDotenvText = %#v\nwant %#v", got, want)
	}
}

func TestRefreshCuratedValues(t *testing.T) {
	store := map[string]string{"GITHUB_TOKEN": "old", "KEEP": "same", "NO_HOST": "keep"}
	host := map[string]string{"GITHUB_TOKEN": "new", "KEEP": "same", "EXTRA": "notcurated"}

	next, changed := refreshCuratedValues(store, host)

	// only GITHUB_TOKEN's value changed
	if !reflect.DeepEqual(changed, []string{"GITHUB_TOKEN"}) {
		t.Fatalf("changed = %v, want [GITHUB_TOKEN]", changed)
	}
	want := map[string]string{"GITHUB_TOKEN": "new", "KEEP": "same", "NO_HOST": "keep"}
	if !reflect.DeepEqual(next, want) {
		t.Fatalf("next = %#v\nwant %#v", next, want)
	}
	// EXTRA (in host, not curated) must NOT be added
	if _, ok := next["EXTRA"]; ok {
		t.Error("uncurated host key EXTRA must not be added to the store")
	}
	// NO_HOST (curated, absent from host) must be preserved
	if next["NO_HOST"] != "keep" {
		t.Error("curated key absent from host must be preserved, not dropped")
	}
}

func TestRefreshCuratedValues_SkipsBrainEndpoint(t *testing.T) {
	// The brain ENDPOINT keys must never be refreshed from the host .env (its
	// 127.0.0.1 is dead inside a container); the per-vault token DOES refresh.
	store := map[string]string{
		"CL_BRAIN_API":       "http://host.docker.internal:9998",
		"CL_BRAIN_VAULT":     "memory",
		"CL_BRAIN_API_TOKEN": "old-tok",
	}
	host := map[string]string{
		"CL_BRAIN_API":       "http://127.0.0.1:9998",
		"CL_BRAIN_VAULT":     "other",
		"CL_BRAIN_API_TOKEN": "new-tok",
	}
	next, changed := refreshCuratedValues(store, host)

	if next["CL_BRAIN_API"] != "http://host.docker.internal:9998" {
		t.Errorf("CL_BRAIN_API refreshed from host (got %q); must be left alone", next["CL_BRAIN_API"])
	}
	if next["CL_BRAIN_VAULT"] != "memory" {
		t.Errorf("CL_BRAIN_VAULT refreshed from host (got %q); must be left alone", next["CL_BRAIN_VAULT"])
	}
	if next["CL_BRAIN_API_TOKEN"] != "new-tok" {
		t.Errorf("CL_BRAIN_API_TOKEN = %q, want new-tok (tokens ride along)", next["CL_BRAIN_API_TOKEN"])
	}
	if !reflect.DeepEqual(changed, []string{"CL_BRAIN_API_TOKEN"}) {
		t.Fatalf("changed = %v, want [CL_BRAIN_API_TOKEN]", changed)
	}
}

func TestSetGatewayEnv_StripsBrainEndpoint(t *testing.T) {
	a, putEnv := envStubApp(t, map[string]string{})
	err := a.SetGatewayEnv("personal", map[string]string{
		"CL_BRAIN_API":       "http://127.0.0.1:9998",
		"CL_BRAIN_VAULT":     "memory",
		"CL_BRAIN_API_TOKEN": "tok",
		"GITHUB_TOKEN":       "gh",
	})
	if err != nil {
		t.Fatal(err)
	}
	got := *putEnv
	if _, ok := got["CL_BRAIN_API"]; ok {
		t.Error("CL_BRAIN_API must be stripped before persisting")
	}
	if _, ok := got["CL_BRAIN_VAULT"]; ok {
		t.Error("CL_BRAIN_VAULT must be stripped before persisting")
	}
	if got["CL_BRAIN_API_TOKEN"] != "tok" {
		t.Error("per-vault token must survive (it's the unified token)")
	}
	if got["GITHUB_TOKEN"] != "gh" {
		t.Error("unrelated secrets must survive")
	}
}

// stub broker: GET returns the given store; PUT captures the new env.
func envStubApp(t *testing.T, store map[string]string) (*App, *map[string]string) {
	t.Helper()
	var putEnv map[string]string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.Method {
		case http.MethodGet:
			_ = json.NewEncoder(w).Encode(map[string]any{"profile": "personal", "env": store})
		case http.MethodPut:
			body, _ := io.ReadAll(r.Body)
			var parsed struct {
				Env map[string]string `json:"env"`
			}
			_ = json.Unmarshal(body, &parsed)
			putEnv = parsed.Env
			_, _ = w.Write([]byte(`{"profile":"personal","saved":true,"count":1}`))
		}
	}))
	t.Cleanup(srv.Close)
	return &App{client: brainbox.NewClient(srv.URL, "k"), ctx: context.Background()}, &putEnv
}

func TestRefreshProfileEnv_UpdatesChangedCuratedKeyOnly(t *testing.T) {
	a, putEnv := envStubApp(t, map[string]string{"GITHUB_TOKEN": "old", "KEEP": "same"})
	ws := t.TempDir()
	if err := os.WriteFile(filepath.Join(ws, ".env.secrets"), []byte("GITHUB_TOKEN=new\nEXTRA=junk\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	changed, err := a.refreshProfileEnv("personal", ws)
	if err != nil {
		t.Fatalf("refreshProfileEnv: %v", err)
	}
	if !reflect.DeepEqual(changed, []string{"GITHUB_TOKEN"}) {
		t.Fatalf("changed = %v, want [GITHUB_TOKEN]", changed)
	}
	if *putEnv == nil {
		t.Fatal("expected a PUT with the refreshed env")
	}
	if (*putEnv)["GITHUB_TOKEN"] != "new" || (*putEnv)["KEEP"] != "same" {
		t.Fatalf("PUT env = %v", *putEnv)
	}
	if _, ok := (*putEnv)["EXTRA"]; ok {
		t.Error("uncurated host key EXTRA leaked into the PUT")
	}
}

func TestRefreshProfileEnv_NoChangeNoPut(t *testing.T) {
	a, putEnv := envStubApp(t, map[string]string{"GITHUB_TOKEN": "same"})
	ws := t.TempDir()
	if err := os.WriteFile(filepath.Join(ws, ".env.secrets"), []byte("GITHUB_TOKEN=same\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	changed, err := a.refreshProfileEnv("personal", ws)
	if err != nil || len(changed) != 0 {
		t.Fatalf("expected no change, got changed=%v err=%v", changed, err)
	}
	if *putEnv != nil {
		t.Errorf("no value changed → must not PUT; got %v", *putEnv)
	}
}

func TestRefreshProfileEnv_EmptyStoreNoSeed(t *testing.T) {
	a, putEnv := envStubApp(t, map[string]string{}) // nothing curated
	ws := t.TempDir()
	if err := os.WriteFile(filepath.Join(ws, ".env.secrets"), []byte("GITHUB_TOKEN=new\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	changed, err := a.refreshProfileEnv("personal", ws)
	if err != nil || len(changed) != 0 {
		t.Fatalf("empty store must be a no-op, got changed=%v err=%v", changed, err)
	}
	if *putEnv != nil {
		t.Errorf("empty store must never be seeded/PUT; got %v", *putEnv)
	}
}
