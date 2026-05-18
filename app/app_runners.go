package main

import "phantom-ink/brainbox"

// ListRunners returns all registered runners on the active API.
func (a *App) ListRunners() ([]brainbox.Runner, error) {
	return a.client.ListRunners()
}

// DeleteRunner deregisters a runner by name.
func (a *App) DeleteRunner(name string) error {
	return a.client.DeleteRunner(name)
}

// StartRunnerPairing issues a one-time pairing token for a new runner.
// The Wails frontend shows the resulting token to the user (copy / QR);
// the new runner claims it via /api/runners/pair/claim.
func (a *App) StartRunnerPairing(runnerNameSuggestion string, ttlSeconds int) (brainbox.PairingTicket, error) {
	return a.client.StartRunnerPairing(runnerNameSuggestion, ttlSeconds)
}

// GetAuthorityStatus returns the credential-authority health snapshot used by
// the status-bar dot and the Credentials modal. Returns the zero value with
// an error if the API is unreachable so the frontend can render "unknown".
func (a *App) GetAuthorityStatus() (brainbox.AuthorityStatus, error) {
	return a.client.GetAuthorityStatus()
}
