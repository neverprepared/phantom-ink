package main

import (
	"phantom-ink/brainbox"
)

// Wails-bound surface for the T11 profile-token routes (/api/tokens): persistent,
// revocable per-profile API/bus tokens. All delegate to brainbox.Client and pass
// errors back to the frontend untouched. These are distinct from the gateway
// tokens in app_gateway.go — see the "API / profile tokens" panel, which the UI
// keeps separate from the Tier-0 gateway-token surface.

// MintProfileToken mints a persistent profile token for a workspace profile with
// an explicit capability set. The raw token is returned once — the frontend must
// show it to the operator immediately and never re-fetch it.
func (a *App) MintProfileToken(profile string, capabilities []string, label string) (brainbox.ProfileToken, error) {
	return a.client.MintProfileToken(profile, capabilities, label)
}

// ListProfileTokens returns every profile token as a masked row (active and
// revoked), for the token management table.
func (a *App) ListProfileTokens() ([]brainbox.ProfileTokenInfo, error) {
	return a.client.ListProfileTokens()
}

// RevokeProfileToken revokes a profile token by id. Any client using it 401s
// immediately.
func (a *App) RevokeProfileToken(tokenID string) error {
	return a.client.RevokeProfileToken(tokenID)
}

// ProfileTokenCapabilities returns the capability catalog for the mint UI's
// multi-select.
func (a *App) ProfileTokenCapabilities() ([]string, error) {
	return a.client.ProfileTokenCapabilities()
}

// ProfileTokenProfiles returns known workspace profiles for the mint dropdown.
// Free-text profile names are still accepted at mint time — this only
// pre-populates the picker.
func (a *App) ProfileTokenProfiles() ([]string, error) {
	return a.client.ProfileTokenProfiles()
}
