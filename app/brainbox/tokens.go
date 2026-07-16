package brainbox

import (
	"fmt"
	"net/url"
)

// Profile-token surface (T11). These wrap the operator-only /api/tokens routes
// (all require_api_key): persistent, revocable per-profile API/bus tokens keyed
// by brainbox `capabilities` (e.g. agent_events:write). Distinct from the
// Tier-0 gateway tokens in gateway.go — those are TTL'd MCP tool scopes; these
// live until revoked and never expire. The raw bearer value is returned by
// MintProfileToken exactly once and is never persisted (brainbox keeps only a
// hash), so callers must surface it to the operator immediately.

// ProfileToken is the response from POST /api/tokens — the raw value is
// returned once and never stored in plaintext anywhere.
type ProfileToken struct {
	TokenID          string   `json:"token_id"`
	Token            string   `json:"token"` // raw bearer value — shown once
	WorkspaceProfile string   `json:"workspace_profile"`
	Capabilities     []string `json:"capabilities"`
	Label            string   `json:"label"`
}

// ProfileTokenInfo is one masked row from GET /api/tokens. It never carries the
// raw token or its hash — only metadata for the management table.
type ProfileTokenInfo struct {
	TokenID          string   `json:"token_id"`
	WorkspaceProfile string   `json:"workspace_profile"`
	Capabilities     []string `json:"capabilities"`
	Scope            []string `json:"scope"`
	Label            string   `json:"label"`
	Issued           int64    `json:"issued"`    // epoch ms
	Revoked          bool     `json:"revoked"`
	RevokedAt        int64    `json:"revoked_at"` // epoch ms, 0 if live
	LastUsed         int64    `json:"last_used"`  // epoch ms, 0 if never used
}

// MintProfileToken mints a persistent profile token bound to a workspace
// profile with an explicit capability set. The raw token is returned once.
func (c *Client) MintProfileToken(profile string, capabilities []string, label string) (ProfileToken, error) {
	if capabilities == nil {
		capabilities = []string{}
	}
	body := map[string]interface{}{
		"workspace_profile": profile,
		"capabilities":      capabilities,
		"label":             label,
	}
	var tok ProfileToken
	if err := c.post("/api/tokens", body, &tok); err != nil {
		return ProfileToken{}, err
	}
	return tok, nil
}

// ListProfileTokens returns every profile token as a masked row (active and
// revoked). Never returns the raw token or its hash.
func (c *Client) ListProfileTokens() ([]ProfileTokenInfo, error) {
	var resp struct {
		Tokens []ProfileTokenInfo `json:"tokens"`
	}
	if err := c.get("/api/tokens", &resp); err != nil {
		return nil, err
	}
	return resp.Tokens, nil
}

// RevokeProfileToken revokes a profile token. Idempotent server-side: a second
// revoke of a known id still succeeds; only an unknown id 404s.
func (c *Client) RevokeProfileToken(tokenID string) error {
	path := fmt.Sprintf("/api/tokens/%s", url.PathEscape(tokenID))
	return c.delete(path, nil)
}

// ProfileTokenCapabilities returns the capability catalog the mint UI offers as
// a multi-select.
func (c *Client) ProfileTokenCapabilities() ([]string, error) {
	var resp struct {
		Capabilities []string `json:"capabilities"`
	}
	if err := c.get("/api/tokens/capabilities", &resp); err != nil {
		return nil, err
	}
	return resp.Capabilities, nil
}

// ProfileTokenProfiles returns known workspace profiles for the mint dropdown.
// This is a convenience list (sourced from gateway-secrets), not an
// authoritative registry — the operator may mint for any free-text profile.
func (c *Client) ProfileTokenProfiles() ([]string, error) {
	var resp struct {
		Profiles []string `json:"profiles"`
	}
	if err := c.get("/api/tokens/profiles", &resp); err != nil {
		return nil, err
	}
	return resp.Profiles, nil
}
