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
