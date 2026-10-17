package main

import (
	"context"
	"phantom-ink/opensearch"
	"time"
)

// agentSessionQueryTimeout bounds a drill-down query. Measured against the live
// platform these run in tens of milliseconds even for a 500-event timeline, so
// this is a ceiling for an unreachable endpoint, not a budget.
//
// Kept at or below the 5s http.Client timeout inside opensearch.NewClient,
// which caps every request regardless of the context deadline — a larger value
// here would be documentation of a limit that does not apply.
const agentSessionQueryTimeout = 5 * time.Second

// ListAgentSessions returns recent agent sessions for the Agent Work panel,
// most-recently-active first.
//
// workspace "" means every profile; a profile name constrains the query to
// sessions stamped with it. The stamping is native on the OTEL event
// (resource.attributes.workspace), so scoping is correct at the source — no
// inheritance across derived edges, which is what makes cross-profile leakage
// possible elsewhere in the app.
//
// since is an OpenSearch date-math expression ("now-24h", "now-7d").
func (a *App) ListAgentSessions(workspace, since string, limit int) ([]opensearch.AgentSession, error) {
	url, err := a.opensearchAPIURL()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), agentSessionQueryTimeout)
	defer cancel()
	return opensearch.NewClient(url).ListAgentSessions(ctx, workspace, since, limit)
}

// GetAgentSessionTimeline returns one session's events in execution order, for
// the drill-down view.
//
// workspace, when non-empty, constrains the lookup to that profile so a session
// id alone cannot read another profile's events.
//
// Prompt and completion text appears only when Claude Code's content-logging
// flags are enabled; otherwise each turn reports Redacted with its length. The
// timeline is useful either way — tool calls, durations, outcomes, per-turn cost
// and model are all present regardless, and model calls carry the agent that
// issued them so subagent work is distinguishable from the main thread.
func (a *App) GetAgentSessionTimeline(sessionID, workspace string, limit int) (*opensearch.SessionTimeline, error) {
	url, err := a.opensearchAPIURL()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), agentSessionQueryTimeout)
	defer cancel()
	return opensearch.NewClient(url).GetSessionTimeline(ctx, sessionID, workspace, limit)
}
