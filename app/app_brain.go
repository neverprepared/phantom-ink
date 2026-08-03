package main

import "phantom-ink/brainbox"

// Phantom-brain memory-binding surface for the Profiles panel's Memory
// section. Thin pass-throughs to the router's /api/brain/profiles facade
// (which proxies the brain admin endpoint + threads CL_BRAIN_* into the
// profile's credentials server-side). Bound to the frontend by Wails.

// GetBrainProfile returns a profile's phantom-brain memory-binding status.
func (a *App) GetBrainProfile(profile string) (brainbox.BrainProfileInfo, error) {
	return a.client.GetBrainProfile(profile)
}

// InitBrainProfile provisions a profile's memory binding and threads its
// CL_BRAIN_* credentials (server-side). Idempotent.
func (a *App) InitBrainProfile(profile string) (brainbox.BrainProfileInitResult, error) {
	return a.client.InitBrainProfile(profile)
}

// GetBrainProfileTokens returns a profile's per-vault brain bearer tokens
// (operator-gated, via the router facade). These are secrets — the Memory
// section masks them and reveals/copies on demand.
func (a *App) GetBrainProfileTokens(profile string) (brainbox.BrainProfileTokens, error) {
	return a.client.GetBrainProfileTokens(profile)
}

// Skills CRUD — the Memory section's skills editor. Brain-authoritative: the
// skills vault is the source of truth, rendered to SKILL.md. These proxy the
// router's /api/brain/profiles/{profile}/skills facade (token minted
// server-side). Delete is the operator act — the UI gates it behind a confirm.

// ListBrainSkills lists a profile's skills (metadata only).
func (a *App) ListBrainSkills(profile string) ([]brainbox.BrainSkill, error) {
	return a.client.ListBrainSkills(profile)
}

// GetBrainSkill returns one skill's full SKILL.md body.
func (a *App) GetBrainSkill(profile, name string) (brainbox.BrainSkillDetail, error) {
	return a.client.GetBrainSkill(profile, name)
}

// CreateBrainSkill creates a skill from a full SKILL.md body (409 on dup name).
func (a *App) CreateBrainSkill(profile, body string) (brainbox.BrainSkillWriteResult, error) {
	return a.client.CreateBrainSkill(profile, body)
}

// UpdateBrainSkill replaces a skill's body by name (upsert).
func (a *App) UpdateBrainSkill(profile, name, body string) (brainbox.BrainSkillWriteResult, error) {
	return a.client.UpdateBrainSkill(profile, name, body)
}

// DeleteBrainSkill hard-deletes a skill by name (operator-gated in the UI).
func (a *App) DeleteBrainSkill(profile, name string) (brainbox.BrainSkillWriteResult, error) {
	return a.client.DeleteBrainSkill(profile, name)
}
