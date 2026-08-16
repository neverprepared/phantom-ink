package main

import (
	"fmt"
	"net/url"
	"path/filepath"
)

// Wire a profile's phantom-brain vault into that profile's Claude Code MCP
// config — the "Add to Claude MCP" button in the Memory section. Saves the
// operator from hand-copying the generated server block into .claude.json.

// BrainHostAPI is the host-reachable brain daemon URL a laptop/desktop Claude
// Code should use as CL_BRAIN_API. It is derived from the configured platform
// base_url's host on the brain port (9998), NOT from the router's session_url
// (which is the in-container host.docker.internal address — rewriting that to
// localhost is wrong whenever the brain is remote). api.neverprepared.com and
// pbrain.neverprepared.com resolve to the same host, and the brain daemon on
// :9998 is not vhost-filtered, so http://<base-host>:9998 hits the same socket.
// Local dev (base_url 127.0.0.1:9910) correctly yields http://127.0.0.1:9998.
func (a *App) BrainHostAPI() string {
	return brainHostAPI(a.config.BaseURL)
}

func brainHostAPI(baseURL string) string {
	u, err := url.Parse(baseURL)
	if err != nil || u.Hostname() == "" {
		return "http://localhost:9998"
	}
	return "http://" + u.Hostname() + ":9998"
}

// AddBrainMCP writes (create-or-replace) a `phantom-brain-<vault>` MCP server
// into the target profile's ~/.claude/.claude.json, using the host-reachable
// brain API and that vault's bearer token. Returns the config path written.
// The env shape matches BrainMemoryEditor's mcpConfig() exactly. Note: Claude
// Code reads MCP servers at launch, so that profile's session must be restarted
// to pick it up.
func (a *App) AddBrainMCP(profile, vault string) (string, error) {
	if profile == "" || vault == "" {
		return "", fmt.Errorf("profile and vault are required")
	}
	// Reuse the vault-token resolver; its api return is the localhost-rewritten
	// value we intentionally replace with the correct host-reachable URL.
	_, token, err := a.brainVaultCreds(profile, vault)
	if err != nil {
		return "", err
	}
	home := a.profileWorkspaceHome(profile)
	if home == "" {
		return "", fmt.Errorf("no workspace_home for profile %q", profile)
	}
	path := filepath.Join(home, ".claude", ".claude.json")
	def := map[string]any{
		"command": "pbrainctl",
		"args":    []any{"client", "mcp"},
		"env": map[string]any{
			"CL_BRAIN_API":         a.BrainHostAPI(),
			"CL_BRAIN_API_TOKEN":   token,
			"CL_WORKSPACE_PROFILE": profile,
			"CL_BRAIN_VAULT":       vault,
		},
	}
	if _, err := upsertMCPServer(path, "mcpServers", "phantom-brain-"+vault, def); err != nil {
		return "", err
	}
	return path, nil
}
