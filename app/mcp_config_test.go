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
