package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func readJSON(t *testing.T, path string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("unmarshal %s: %v", path, err)
	}
	return m
}

func TestInjectMCPServerEnv_UpdatesExistingSurgically(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".claude.json")
	seed := `{
  "otherTop": 42,
  "mcpServers": {
    "kroki": {"command": "mcp-kroki", "env": {"EXISTING": "keep"}},
    "brain": {"command": "x"}
  }
}`
	if err := os.WriteFile(path, []byte(seed), 0o644); err != nil {
		t.Fatal(err)
	}

	ok, err := injectMCPServerEnv(path, "mcpServers", "env", "kroki", "KROKI_URL", "http://h:18000")
	if err != nil || !ok {
		t.Fatalf("expected update, got ok=%v err=%v", ok, err)
	}

	root := readJSON(t, path)
	servers := root["mcpServers"].(map[string]any)
	kroki := servers["kroki"].(map[string]any)
	env := kroki["env"].(map[string]any)
	if env["KROKI_URL"] != "http://h:18000" {
		t.Fatalf("KROKI_URL not set: %v", env)
	}
	if env["EXISTING"] != "keep" {
		t.Fatalf("clobbered sibling env value")
	}
	if kroki["command"] != "mcp-kroki" {
		t.Fatalf("clobbered server command")
	}
	if root["otherTop"].(float64) != 42 {
		t.Fatalf("clobbered unrelated top-level key")
	}
	if _, ok := servers["brain"]; !ok {
		t.Fatalf("dropped unrelated server")
	}
}

func TestInjectMCPServerEnv_Idempotent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".claude.json")
	os.WriteFile(path, []byte(`{"mcpServers":{"kroki":{"env":{"KROKI_URL":"http://h:18000"}}}}`), 0o644)
	ok, err := injectMCPServerEnv(path, "mcpServers", "env", "kroki", "KROKI_URL", "http://h:18000")
	if err != nil || ok {
		t.Fatalf("expected no-op on identical value, got ok=%v err=%v", ok, err)
	}
}

func TestInjectMCPServerEnv_CreatesEnvWhenServerLacksIt(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "opencode.json")
	os.WriteFile(path, []byte(`{"mcp":{"kroki":{"type":"local","command":["mcp-kroki"]}}}`), 0o644)
	ok, err := injectMCPServerEnv(path, "mcp", "environment", "kroki", "KROKI_URL", "http://h:18000")
	if err != nil || !ok {
		t.Fatalf("expected update, got ok=%v err=%v", ok, err)
	}
	root := readJSON(t, path)
	envs := root["mcp"].(map[string]any)["kroki"].(map[string]any)["environment"].(map[string]any)
	if envs["KROKI_URL"] != "http://h:18000" {
		t.Fatalf("environment not set: %v", envs)
	}
}

func TestInjectMCPServerEnv_NeverCreatesAbsentServer(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".claude.json")
	os.WriteFile(path, []byte(`{"mcpServers":{"other":{"command":"x"}}}`), 0o644)
	ok, err := injectMCPServerEnv(path, "mcpServers", "env", "kroki", "KROKI_URL", "http://h:18000")
	if err != nil || ok {
		t.Fatalf("expected skip for absent server, got ok=%v err=%v", ok, err)
	}
	root := readJSON(t, path)
	if _, has := root["mcpServers"].(map[string]any)["kroki"]; has {
		t.Fatalf("must not create an absent server")
	}
}

func TestInjectMCPServerEnv_MissingFileIsNoOp(t *testing.T) {
	ok, err := injectMCPServerEnv(filepath.Join(t.TempDir(), "nope.json"), "mcpServers", "env", "kroki", "X", "y")
	if err != nil || ok {
		t.Fatalf("expected skip for missing file, got ok=%v err=%v", ok, err)
	}
}

func TestUpsertMCPServer_CreatesWhenAbsent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".claude.json")
	os.WriteFile(path, []byte(`{"otherTop":42,"mcpServers":{"kroki":{"command":"mcp-kroki"}}}`), 0o644)
	def := map[string]any{
		"command": "pbrainctl",
		"args":    []any{"client", "mcp"},
		"env":     map[string]any{"CL_BRAIN_API": "http://api.neverprepared.com:9998"},
	}
	changed, err := upsertMCPServer(path, "mcpServers", "phantom-brain-memory", def)
	if err != nil || !changed {
		t.Fatalf("upsert: changed=%v err=%v", changed, err)
	}
	root := readJSON(t, path)
	if root["otherTop"].(float64) != 42 {
		t.Fatal("clobbered sibling top-level key")
	}
	servers := root["mcpServers"].(map[string]any)
	if _, ok := servers["kroki"]; !ok {
		t.Fatal("clobbered existing server")
	}
	pb := servers["phantom-brain-memory"].(map[string]any)
	if pb["command"] != "pbrainctl" {
		t.Fatalf("bad command: %v", pb["command"])
	}
}

func TestUpsertMCPServer_ReplacesAndIsIdempotent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".claude.json")
	os.WriteFile(path, []byte(`{"mcpServers":{"phantom-brain-memory":{"command":"old","env":{"CL_BRAIN_API":"http://localhost:9998"}}}}`), 0o644)
	def := map[string]any{"command": "pbrainctl", "env": map[string]any{"CL_BRAIN_API": "http://api.neverprepared.com:9998"}}
	if changed, err := upsertMCPServer(path, "mcpServers", "phantom-brain-memory", def); err != nil || !changed {
		t.Fatalf("replace: changed=%v err=%v", changed, err)
	}
	if got := readJSON(t, path)["mcpServers"].(map[string]any)["phantom-brain-memory"].(map[string]any)["env"].(map[string]any)["CL_BRAIN_API"]; got != "http://api.neverprepared.com:9998" {
		t.Fatalf("not replaced: %v", got)
	}
	// second identical upsert is a no-op
	if changed, err := upsertMCPServer(path, "mcpServers", "phantom-brain-memory", def); err != nil || changed {
		t.Fatalf("expected no-op: changed=%v err=%v", changed, err)
	}
}

func TestUpsertMCPServer_CreatesFileAndMap(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".claude", ".claude.json") // .claude/ does not exist yet
	def := map[string]any{"command": "pbrainctl"}
	if changed, err := upsertMCPServer(path, "mcpServers", "phantom-brain-memory", def); err != nil || !changed {
		t.Fatalf("create-file: changed=%v err=%v", changed, err)
	}
	if _, ok := readJSON(t, path)["mcpServers"].(map[string]any)["phantom-brain-memory"]; !ok {
		t.Fatal("server not written to fresh file")
	}
}

func TestBrainHostAPI_DerivesHostOnBrainPort(t *testing.T) {
	cases := map[string]string{
		"https://api.neverprepared.com":   "http://api.neverprepared.com:9998",
		"http://127.0.0.1:9910":           "http://127.0.0.1:9998",
		"https://api.neverprepared.com/":  "http://api.neverprepared.com:9998",
		"":                                "http://localhost:9998",
	}
	for in, want := range cases {
		if got := brainHostAPI(in); got != want {
			t.Errorf("brainHostAPI(%q)=%q want %q", in, got, want)
		}
	}
}
