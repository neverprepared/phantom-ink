package main

import "phantom-ink/brainbox"

// ListRunners returns all registered runners on the active API.
func (a *App) ListRunners() ([]brainbox.Runner, error) {
	return a.client.ListRunners()
}

// StartRunnerPairing issues a one-time pairing token for a new runner.
// The Wails frontend shows the resulting token to the user (copy / QR);
// the new runner claims it via /api/runners/pair/claim.
func (a *App) StartRunnerPairing(runnerNameSuggestion string, ttlSeconds int) (brainbox.PairingTicket, error) {
	return a.client.StartRunnerPairing(runnerNameSuggestion, ttlSeconds)
}
