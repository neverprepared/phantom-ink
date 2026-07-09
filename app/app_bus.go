package main

import (
	"fmt"

	"phantom-ink/brainbox"
)

// Wails-bound thin shims over the brainbox bus REST endpoints. The frontend
// uses these instead of fetching brainbox directly so all HTTP goes through
// the Go-side client (auth, retries, future caching live in one place).

// AgentStateFilter mirrors brainbox.ListAgentStateOptions but in a shape the
// Wails JS bridge will marshal cleanly.
type AgentStateFilter struct {
	Status    string `json:"status"`     // comma-separated; e.g. "failed,blocked"
	Workspace string `json:"workspace"`
	Source    string `json:"source"`
	ParentID  string `json:"parent_id"`
	Limit     int    `json:"limit"`
}

// ListAgentState returns current-state envelopes from the bus matching the
// supplied filter. Returns an empty slice when brainbox is unreachable so the
// UI can render "no items" without an error path.
func (a *App) ListAgentState(filter AgentStateFilter) ([]brainbox.AgentStateItem, error) {
	if a.client == nil {
		return []brainbox.AgentStateItem{}, nil
	}
	items, err := a.client.ListAgentState(brainbox.ListAgentStateOptions{
		Status:    filter.Status,
		Workspace: filter.Workspace,
		Source:    filter.Source,
		ParentID:  filter.ParentID,
		Limit:     filter.Limit,
	})
	if err != nil {
		return nil, fmt.Errorf("list agent_state: %w", err)
	}
	return items, nil
}

// GetAgentState fetches one envelope by id. The second return mirrors
// brainbox's not-found path; the UI maps it to "envelope no longer exists".
func (a *App) GetAgentState(id string) (brainbox.AgentStateItem, error) {
	if a.client == nil {
		return brainbox.AgentStateItem{}, fmt.Errorf("brainbox not configured")
	}
	item, ok, err := a.client.GetAgentState(id)
	if err != nil {
		return brainbox.AgentStateItem{}, err
	}
	if !ok {
		return brainbox.AgentStateItem{}, fmt.Errorf("envelope %q not found", id)
	}
	return item, nil
}

// ListAgentEvents returns the append-only audit log for one envelope id or
// one parent_id family (or both). Empty strings skip a filter.
func (a *App) ListAgentEvents(envelopeID, parentID string, limit int) ([]brainbox.AgentEventEntry, error) {
	if a.client == nil {
		return []brainbox.AgentEventEntry{}, nil
	}
	return a.client.ListAgentEvents(envelopeID, parentID, limit)
}

// OutboxPending returns the current count of agent-event envelopes waiting to
// ship to brainbox. Surfaces in the Stream panel header so the user notices
// when delivery is backed up (brainbox down, transient network).
func (a *App) OutboxPending() int {
	if a.outbox == nil {
		return 0
	}
	return a.outbox.PendingFromDB()
}

// SearchAgentEvents queries event history through the daemon — OpenSearch
// when the sink is configured, Postgres fallback otherwise. The result's
// Backend field says which one answered.
func (a *App) SearchAgentEvents(opts brainbox.SearchAgentEventsOptions) (brainbox.SearchAgentEventsResult, error) {
	if a.client == nil {
		return brainbox.SearchAgentEventsResult{Backend: "postgres"}, nil
	}
	return a.client.SearchAgentEvents(opts)
}
