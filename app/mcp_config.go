package main

import (
	"encoding/json"
	"os"
	"path/filepath"
)

// injectMCPServerEnv surgically sets env[envVar]=value on an EXISTING MCP server
// entry in a JSON config file (.claude.json or opencode.json), preserving
// everything else. It never CREATES the server — if the server (or the file) is
// absent it returns (false, nil), so it can't leave a partial/broken entry in a
// config that doesn't already use that server.
//
//   - topKey: the servers map — "mcpServers" (.claude.json) or "mcp" (opencode)
//   - envKey: the per-server env object — "env" (.claude.json) or "environment" (opencode)
//
// Returns whether the file was changed. The write is atomic (temp file + rename).
func injectMCPServerEnv(path, topKey, envKey, server, envVar, value string) (bool, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil // no such config on this host — nothing to wire
		}
		return false, err
	}
	var root map[string]any
	if err := json.Unmarshal(raw, &root); err != nil {
		return false, err
	}
	servers, ok := root[topKey].(map[string]any)
	if !ok {
		return false, nil
	}
	entry, ok := servers[server].(map[string]any)
	if !ok {
		return false, nil // server not configured here — don't create it
	}
	env, ok := entry[envKey].(map[string]any)
	if !ok {
		env = map[string]any{}
		entry[envKey] = env
	}
	if cur, _ := env[envVar].(string); cur == value {
		return false, nil // already correct — avoid a needless rewrite
	}
	env[envVar] = value
	return true, writeJSONAtomic(path, root)
}

// upsertMCPServer creates-or-replaces a WHOLE MCP server entry (command, args,
// env, …) under topKey in a JSON config file, preserving everything else. Unlike
// injectMCPServerEnv (which only edits an existing server's env), this adds the
// server if absent — and creates the file and the topKey map if they don't exist
// yet. Returns whether the file was changed (a byte-identical entry is a no-op).
// The write is atomic (temp file + rename).
func upsertMCPServer(path, topKey, server string, def map[string]any) (bool, error) {
	var root map[string]any
	raw, err := os.ReadFile(path)
	switch {
	case err == nil:
		if err := json.Unmarshal(raw, &root); err != nil {
			return false, err
		}
	case os.IsNotExist(err):
		root = map[string]any{}
	default:
		return false, err
	}
	if root == nil {
		root = map[string]any{}
	}
	servers, ok := root[topKey].(map[string]any)
	if !ok {
		servers = map[string]any{}
		root[topKey] = servers
	}
	// Idempotent: skip the rewrite if the entry is already byte-identical.
	if cur, ok := servers[server]; ok {
		a, e1 := json.Marshal(cur)
		b, e2 := json.Marshal(def)
		if e1 == nil && e2 == nil && string(a) == string(b) {
			return false, nil
		}
	}
	servers[server] = def
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return false, err
	}
	return true, writeJSONAtomic(path, root)
}

// writeJSONAtomic marshals v (2-space indent) and replaces path atomically.
func writeJSONAtomic(path string, v any) error {
	out, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	out = append(out, '\n')
	tmp, err := os.CreateTemp(filepath.Dir(path), ".mcpcfg-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := tmp.Write(out); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}
