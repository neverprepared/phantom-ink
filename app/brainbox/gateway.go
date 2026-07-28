package brainbox

import (
	"fmt"
	"net/http"
	"net/url"
	"time"
)

// MCP gateway operator surface (ADR-002 phase 3).
//
// These wrap the operator-only /api/gateway/* routes (all require_api_key):
// the per-profile encrypted env store (phase 1) and Tier-0 token minting
// (phase 3). The env values are plaintext in transit — they are the operator
// editing their own secrets; agents never reach these routes. The gateway
// holds only ciphertext at rest, decrypting with CL_GATEWAY__SECRET_KEY.

// GatewayProfilesInfo is the response from GET /api/gateway/profiles.
type GatewayProfilesInfo struct {
	Profiles []string `json:"profiles"` // profiles that have a stored env file
	Unlocked bool     `json:"unlocked"` // whether the operator key is configured
}

// GatewayToken is the response from POST /api/gateway/tokens — returned once.
type GatewayToken struct {
	Token   string   `json:"token"`
	Profile string   `json:"profile"`
	Scope   []string `json:"scope"`
	Ceiling string   `json:"ceiling"` // residency ceiling ("" = no restriction)
	Expiry  int64    `json:"expiry"`  // epoch ms
}

// GatewayServer is one catalog server + its enable state (DB registry, #152).
type GatewayServer struct {
	Name    string `json:"name"`
	Command string `json:"command"`
	Enabled bool   `json:"enabled"`
}

// GatewayTool is one namespaced tool a profile sees through the gateway.
type GatewayTool struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}

// GatewayToolsResult is the response from GET /api/gateway/profiles/{p}/tools.
type GatewayToolsResult struct {
	Profile string        `json:"profile"`
	Servers []string      `json:"servers"` // operator allowlist (CL_GATEWAY__SERVERS)
	Tools   []GatewayTool `json:"tools"`
}

// ListGatewayProfiles returns which profiles have a stored env file and
// whether the gateway is unlocked (operator key configured).
func (c *Client) ListGatewayProfiles() (GatewayProfilesInfo, error) {
	var info GatewayProfilesInfo
	if err := c.get("/api/gateway/profiles", &info); err != nil {
		return GatewayProfilesInfo{}, err
	}
	return info, nil
}

// ListGatewayServers returns every catalog server with its enable state.
func (c *Client) ListGatewayServers() ([]GatewayServer, error) {
	var resp struct {
		Servers []GatewayServer `json:"servers"`
	}
	if err := c.get("/api/gateway/servers", &resp); err != nil {
		return nil, err
	}
	return resp.Servers, nil
}

// SetGatewayServerEnabled toggles a catalog server on/off (live, no restart).
func (c *Client) SetGatewayServerEnabled(name string, enabled bool) error {
	path := fmt.Sprintf("/api/gateway/servers/%s", url.PathEscape(name))
	return c.patch(path, map[string]interface{}{"enabled": enabled}, nil)
}

// ListGatewayProfileTools returns the namespaced tools a profile would receive
// through the gateway right now (catalog ∩ allowlist, spawned with the
// profile's creds). An empty Tools slice means nothing is allowlisted or the
// downstream server(s) failed to spawn.
func (c *Client) ListGatewayProfileTools(profile string) (GatewayToolsResult, error) {
	var res GatewayToolsResult
	path := fmt.Sprintf("/api/gateway/profiles/%s/tools", url.PathEscape(profile))
	// A cold tools-test spawns each of the profile's enabled MCP servers to
	// enumerate their tools — inherently slow (npx/uvx spawns), and a broken
	// server fails slowly before it's negative-cached. The default 15s client
	// times out on the first call; use a longer one (subsequent calls are fast).
	longClient := &http.Client{Timeout: 60 * time.Second}
	if err := c.doWith(longClient, http.MethodGet, path, nil, &res); err != nil {
		return GatewayToolsResult{}, err
	}
	return res, nil
}

// GetGatewayProfileEnv returns the decrypted env map for a profile. A profile
// with no stored env yet returns an empty map (the API 404s; callers treat
// that as "none stored").
func (c *Client) GetGatewayProfileEnv(profile string) (map[string]string, error) {
	var resp struct {
		Profile string            `json:"profile"`
		Env     map[string]string `json:"env"`
	}
	path := fmt.Sprintf("/api/gateway/profiles/%s/env", url.PathEscape(profile))
	if err := c.get(path, &resp); err != nil {
		return nil, err
	}
	if resp.Env == nil {
		resp.Env = map[string]string{}
	}
	return resp.Env, nil
}

// SetGatewayProfileEnv replaces the stored env for a profile (full
// overwrite; the server re-encrypts the whole map).
func (c *Client) SetGatewayProfileEnv(profile string, env map[string]string) error {
	path := fmt.Sprintf("/api/gateway/profiles/%s/env", url.PathEscape(profile))
	body := map[string]interface{}{"env": env}
	return c.put(path, body, nil)
}

// DeleteGatewayProfileEnv removes a profile's stored env file entirely.
func (c *Client) DeleteGatewayProfileEnv(profile string) error {
	path := fmt.Sprintf("/api/gateway/profiles/%s/env", url.PathEscape(profile))
	return c.delete(path, nil)
}

// MintGatewayToken mints a Tier-0 gateway token bound to a profile with an
// explicit tool scope (empty = all tools). The token is the secret — it is
// returned once and not stored in plaintext anywhere.
func (c *Client) MintGatewayToken(profile string, scope []string, ttlSeconds int, ceiling string) (GatewayToken, error) {
	if scope == nil {
		scope = []string{}
	}
	body := map[string]interface{}{"profile": profile, "scope": scope, "ttl": ttlSeconds}
	if ceiling != "" {
		body["ceiling"] = ceiling
	}
	var tok GatewayToken
	if err := c.post("/api/gateway/tokens", body, &tok); err != nil {
		return GatewayToken{}, err
	}
	return tok, nil
}
