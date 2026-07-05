package main

import "phantom-ink/brainbox"

// Wails-bound surface for declarative orchestration (trust zones + residency).
// All delegate to brainbox.Client; errors pass back to the frontend untouched.

// GetTrust returns a profile's trust map + effective default ceiling.
func (a *App) GetTrust(profile string) (brainbox.TrustConfig, error) {
	return a.client.GetTrust(profile)
}

// GetProfileServers lists a profile's gateway servers with effective on/off.
func (a *App) GetProfileServers(profile string) ([]brainbox.ProfileServerState, error) {
	return a.client.GetProfileServers(profile)
}

// SetProfileServerOverride manually includes/excludes a server for a profile.
func (a *App) SetProfileServerOverride(profile, server string, enabled bool) error {
	return a.client.SetProfileServerOverride(profile, server, enabled)
}

// ClearProfileServerOverride reverts a server to the resolution default.
func (a *App) ClearProfileServerOverride(profile, server string) error {
	return a.client.ClearProfileServerOverride(profile, server)
}

// SetTrustRule upserts a destination → zone rule for a profile.
func (a *App) SetTrustRule(profile, pattern, zone string) error {
	return a.client.SetTrustRule(profile, pattern, zone)
}

// DeleteTrustRule removes a trust rule by pattern.
func (a *App) DeleteTrustRule(profile, pattern string) error {
	return a.client.DeleteTrustRule(profile, pattern)
}

// SetDefaultCeiling sets a profile's default residency ceiling.
func (a *App) SetDefaultCeiling(profile, zone string) error {
	return a.client.SetDefaultCeiling(profile, zone)
}

// GetOrchestrationZones returns provider + tool zones for a profile.
func (a *App) GetOrchestrationZones(profile string) (brainbox.OrchestrationZones, error) {
	return a.client.GetOrchestrationZones(profile)
}

// PreviewPlan resolves a step (ceiling + capabilities) into a compliant plan.
func (a *App) PreviewPlan(profile, ceiling string, requires, prefers []string) (brainbox.StepPlanResult, error) {
	if requires == nil {
		requires = []string{}
	}
	if prefers == nil {
		prefers = []string{}
	}
	return a.client.PreviewPlan(profile, ceiling, requires, prefers)
}
