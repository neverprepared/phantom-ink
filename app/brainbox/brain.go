package brainbox

import (
	"fmt"
	"net/url"
)

// Brain memory-binding surface (Phase 2, brain facade).
//
// These wrap the router's /api/brain/profiles* facade, which proxies
// phantom-brain's operator /admin/profiles and threads CL_BRAIN_* into the
// profile's credentials server-side. Provisioning is fire-and-observe; the
// default token stays server-side. The one exception is GetBrainProfileTokens,
// an operator-gated read that returns the per-vault tokens on demand.

// BrainProfileInfo is a profile's memory-binding status, from
// GET /api/brain/profiles/{profile}. An unprovisioned profile has
// Provisioned=false and empty storage fields.
type BrainProfileInfo struct {
	Profile     string `json:"profile"`
	Vault       string `json:"vault"`
	Provisioned bool   `json:"provisioned"`
	Bucket      string `json:"bucket"`
	IndexPrefix string `json:"index_prefix"`
}

// BrainProfileInitResult is the outcome of POST /api/brain/profiles: the
// binding is provisioned (Postgres SoR + MinIO archives) and CL_BRAIN_*
// threaded into the profile's credentials. TokenCreated is false on a re-run
// (the existing token is read back). Idempotent.
type BrainProfileInitResult struct {
	Profile      string `json:"profile"`
	Vault        string `json:"vault"`
	Provisioned  bool   `json:"provisioned"`
	Live         bool   `json:"live"`
	TokenCreated bool   `json:"token_created"`
	Bucket       string `json:"bucket"`
	IndexPrefix  string `json:"index_prefix"`
	Threaded     bool   `json:"threaded"`
}

// GetBrainProfile returns a profile's memory-binding status via the router
// brain facade.
func (c *Client) GetBrainProfile(profile string) (BrainProfileInfo, error) {
	var info BrainProfileInfo
	path := fmt.Sprintf("/api/brain/profiles/%s", url.PathEscape(profile))
	if err := c.get(path, &info); err != nil {
		return BrainProfileInfo{}, err
	}
	return info, nil
}

// InitBrainProfile provisions (or heals) a profile's memory binding and
// threads its CL_BRAIN_* credentials, via the router brain facade. Idempotent.
func (c *Client) InitBrainProfile(profile string) (BrainProfileInitResult, error) {
	var res BrainProfileInitResult
	if err := c.post("/api/brain/profiles", map[string]interface{}{"profile": profile}, &res); err != nil {
		return BrainProfileInitResult{}, err
	}
	return res, nil
}

// BrainVaultToken is one vault's bearer token for a profile. The token IS the
// (profile, vault) scope — an MCP client sends it as Authorization: Bearer.
type BrainVaultToken struct {
	Vault     string `json:"vault"`
	Token     string `json:"token"`
	IsDefault bool   `json:"is_default"`
}

// BrainProfileTokens is a profile's per-vault bearer tokens, from
// GET /api/brain/profiles/{profile}/tokens. SECRET — the UI masks them and
// reveals on demand.
type BrainProfileTokens struct {
	Profile      string            `json:"profile"`
	SessionURL   string            `json:"session_url"`
	DefaultVault string            `json:"default_vault"`
	Tokens       []BrainVaultToken `json:"tokens"`
}

// GetBrainProfileTokens returns a profile's per-vault brain bearer tokens via
// the router facade (operator-gated). Secrets — callers must treat them as such.
func (c *Client) GetBrainProfileTokens(profile string) (BrainProfileTokens, error) {
	var res BrainProfileTokens
	path := fmt.Sprintf("/api/brain/profiles/%s/tokens", url.PathEscape(profile))
	if err := c.get(path, &res); err != nil {
		return BrainProfileTokens{}, err
	}
	return res, nil
}
