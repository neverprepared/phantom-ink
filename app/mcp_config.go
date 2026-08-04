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
