package brainbox

import (
	"fmt"
	"net/url"
)

// Declarative-orchestration operator surface (trust zones + residency planning).
// Wraps the /api/orchestration/* routes: per-profile trust map (destination →
// zone) + default residency ceiling, a zone-classification view, and a plan
// preview that resolves a step to a compliant provider + tools.

// TrustRule is one destination-glob → trust-zone mapping.
type TrustRule struct {
	Pattern string `json:"pattern"`
	Zone    string `json:"zone"`
}

// TrustConfig is a profile's trust map + effective default ceiling.
type TrustConfig struct {
	Profile        string      `json:"profile"`
	DefaultCeiling string      `json:"default_ceiling"`
	Rules          []TrustRule `json:"rules"`
}

// ProviderZone is a provider's derived zone + curated capabilities.
type ProviderZone struct {
	Name         string   `json:"name"`
	Zone         string   `json:"zone"`
	Capabilities []string `json:"capabilities"`
}

// ToolZone is an MCP server's derived zone.
type ToolZone struct {
	Name string `json:"name"`
	Zone string `json:"zone"`
}

// OrchestrationZones is the classification view for a profile.
type OrchestrationZones struct {
	Profile   string         `json:"profile"`
	Providers []ProviderZone `json:"providers"`
	Tools     []ToolZone     `json:"tools"`
}

// PlannedProvider is the chosen provider in a resolved plan (nil when blocked).
type PlannedProvider struct {
	Name string `json:"name"`
	Zone string `json:"zone"`
}

// StepPlanResult is the resolved (or blocked) plan for a step.
type StepPlanResult struct {
	Profile       string           `json:"profile"`
	Ceiling       string           `json:"ceiling"`
	Blocked       bool             `json:"blocked"`
	Reason        string           `json:"reason"`
	Provider      *PlannedProvider `json:"provider"`
	EligibleTools []string         `json:"eligible_tools"`
	ExcludedTools []ToolZone       `json:"excluded_tools"`
}

// ProfileServerState is one gateway server's include/exclude for a profile:
// the resolution default (zone ≤ ceiling) + the user's manual override.
type ProfileServerState struct {
	Name           string `json:"name"`
	Zone           string `json:"zone"`
	DefaultEnabled bool   `json:"default_enabled"`
	Override       *bool  `json:"override"` // nil = no manual override
	Effective      bool   `json:"effective"`
}

// GetProfileServers lists a profile's gateway servers with their effective on/off.
func (c *Client) GetProfileServers(profile string) ([]ProfileServerState, error) {
	var resp struct {
		Servers []ProfileServerState `json:"servers"`
	}
	path := fmt.Sprintf("/api/orchestration/profiles/%s/servers", url.PathEscape(profile))
	if err := c.get(path, &resp); err != nil {
		return nil, err
	}
	return resp.Servers, nil
}

// SetProfileServerOverride manually includes/excludes a server for a profile.
func (c *Client) SetProfileServerOverride(profile, server string, enabled bool) error {
	path := fmt.Sprintf("/api/orchestration/profiles/%s/servers/%s",
		url.PathEscape(profile), url.PathEscape(server))
	return c.put(path, map[string]interface{}{"enabled": enabled}, nil)
}

// ClearProfileServerOverride removes an override → reverts to the resolution default.
func (c *Client) ClearProfileServerOverride(profile, server string) error {
	path := fmt.Sprintf("/api/orchestration/profiles/%s/servers/%s",
		url.PathEscape(profile), url.PathEscape(server))
	return c.delete(path, nil)
}

// GetTrust returns a profile's trust rules + default ceiling.
func (c *Client) GetTrust(profile string) (TrustConfig, error) {
	var out TrustConfig
	path := fmt.Sprintf("/api/orchestration/profiles/%s/trust", url.PathEscape(profile))
	if err := c.get(path, &out); err != nil {
		return TrustConfig{}, err
	}
	return out, nil
}

// SetTrustRule upserts one destination → zone rule.
func (c *Client) SetTrustRule(profile, pattern, zone string) error {
	path := fmt.Sprintf("/api/orchestration/profiles/%s/trust/rule", url.PathEscape(profile))
	return c.put(path, map[string]interface{}{"pattern": pattern, "zone": zone}, nil)
}

// DeleteTrustRule removes a rule by pattern.
func (c *Client) DeleteTrustRule(profile, pattern string) error {
	path := fmt.Sprintf("/api/orchestration/profiles/%s/trust/rule?pattern=%s",
		url.PathEscape(profile), url.QueryEscape(pattern))
	return c.delete(path, nil)
}

// SetDefaultCeiling sets a profile's default residency ceiling.
func (c *Client) SetDefaultCeiling(profile, zone string) error {
	path := fmt.Sprintf("/api/orchestration/profiles/%s/trust/ceiling", url.PathEscape(profile))
	return c.put(path, map[string]interface{}{"zone": zone}, nil)
}

// GetOrchestrationZones returns provider + tool zones for a profile.
func (c *Client) GetOrchestrationZones(profile string) (OrchestrationZones, error) {
	var out OrchestrationZones
	path := fmt.Sprintf("/api/orchestration/profiles/%s/zones", url.PathEscape(profile))
	if err := c.get(path, &out); err != nil {
		return OrchestrationZones{}, err
	}
	return out, nil
}

// PreviewPlan resolves a step (ceiling + capabilities) to a compliant plan.
// An empty ceiling uses the profile's default.
func (c *Client) PreviewPlan(profile, ceiling string, requires, prefers []string) (StepPlanResult, error) {
	body := map[string]interface{}{"requires": requires, "prefers": prefers}
	if ceiling != "" {
		body["ceiling"] = ceiling
	}
	var out StepPlanResult
	path := fmt.Sprintf("/api/orchestration/profiles/%s/plan", url.PathEscape(profile))
	if err := c.post(path, body, &out); err != nil {
		return StepPlanResult{}, err
	}
	return out, nil
}
