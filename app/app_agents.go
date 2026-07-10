package main

import (
	"context"
	"fmt"
	"os"
)

// ---------------------------------------------------------------------------
// Agents (installed coding-agent CLIs: claude, codex, aider, gemini, …)
// ---------------------------------------------------------------------------

// ListAgents returns the cached set of detected agents from SQLite. UI calls
// this for initial render; RescanAgents refreshes the cache.
//
// Invocation metadata is not persisted (it's compile-time per AgentDescriptor),
// so we re-hydrate it from knownAgents before returning.
func (a *App) ListAgents() ([]DetectedAgent, error) {
	if a.db == nil {
		return []DetectedAgent{}, errNoDB
	}
	rows, err := a.db.ListAgents()
	if err != nil {
		return nil, err
	}
	if rows == nil {
		rows = []DetectedAgent{}
	}
	descByID := make(map[string]AgentDescriptor, len(knownAgents))
	for _, d := range knownAgents {
		descByID[d.ID] = d
	}
	for i := range rows {
		if d, ok := descByID[rows[i].ID]; ok {
			rows[i].Invocation = d.Invocation
		}
	}
	return rows, nil
}

// UsableAgents returns the subset of agents that are both detected on PATH
// and toggled enabled by the user. This is the visibility rule consumed by
// loop step pickers, command palette entries, and any other surface that
// runs an agent (as opposed to managing its enabled state).
func (a *App) UsableAgents() ([]DetectedAgent, error) {
	all, err := a.ListAgents()
	if err != nil {
		return nil, err
	}
	out := make([]DetectedAgent, 0, len(all))
	for _, r := range all {
		if r.Detected && r.Enabled {
			out = append(out, r)
		}
	}
	return out, nil
}

// agentDescriptor looks up a known agent by ID for loop execution. Returns
// false if the ID isn't in the catalog.
func agentDescriptor(id string) (AgentDescriptor, bool) {
	for _, d := range knownAgents {
		if d.ID == id {
			return d, true
		}
	}
	return AgentDescriptor{}, false
}

// RescanAgents probes PATH for every known agent, upserts results into SQLite
// (preserving each agent's persisted enabled flag), and returns the fresh set.
func (a *App) RescanAgents() ([]DetectedAgent, error) {
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	fresh := detectAgents(ctx)
	if a.db != nil {
		for i := range fresh {
			fresh[i].Enabled = a.db.GetAgentEnabled(fresh[i].ID)
			if err := a.db.UpsertAgent(fresh[i]); err != nil {
				fmt.Fprintf(os.Stderr, "warning: failed to upsert agent %q: %v\n", fresh[i].ID, err)
			}
		}
	}
	return fresh, nil
}

// SetAgentEnabled flips the persisted enabled flag for a single agent.
func (a *App) SetAgentEnabled(id string, enabled bool) error {
	if err := a.requireDB(); err != nil {
		return err
	}
	return a.db.SetAgentEnabled(id, enabled)
}
