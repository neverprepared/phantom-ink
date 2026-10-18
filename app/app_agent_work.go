package main

import (
	"context"
	"fmt"
	"time"

	"phantom-ink/brainbox"
	"phantom-ink/langfuse"
	"phantom-ink/opensearch"
)

// AgentWorkRow is one row of the Agent Work list, assembled from BOTH stores.
//
// The split is forced by measurement, not preference:
//
//   - Langfuse owns the work itself. It carries the real prompt and completion
//     text; OpenSearch has none (stripped from spans by design, `<REDACTED>` in
//     the logs), so "what did this session say" is only answerable there.
//   - OpenSearch owns liveness. Measured together on one live session,
//     OpenSearch was 33s behind and Langfuse 725s. Langfuse's Claude Code
//     capture is a Stop hook, firing once per COMPLETED turn, so a session
//     mid-turn is invisible until the turn ends. A liveness dot built on
//     Langfuse recency marks a working session dead.
//
// Both halves degrade independently: either store being unreachable costs its
// own columns, never the row.
type AgentWorkRow struct {
	SessionID string `json:"session_id"`
	Workspace string `json:"workspace"`
	FirstSeen string `json:"first_seen"`
	LastSeen  string `json:"last_seen"`

	// --- from Langfuse ---
	Observations int64    `json:"observations"`
	Generations  int64    `json:"generations"`
	ToolCalls    int64    `json:"tool_calls"`
	Tokens       int64    `json:"tokens"`
	Models       []string `json:"models"`
	// CostUSD is LANGFUSE'S view of spend and is lower than OpenSearch's for
	// the same session -- $318.82 against $515.78 on one measured session. The
	// gap is the Stop hook's blind spot. Labelled as such in the UI.
	CostUSD float64 `json:"cost_usd"`

	// --- from OpenSearch ---
	// Liveness is "" when the OpenSearch overlay was unavailable, which the UI
	// must render as unknown rather than as dormant.
	Liveness        string `json:"liveness,omitempty"`
	LastActivityMS  int64  `json:"last_activity_ms,omitempty"`
	LastHeartbeatMS *int64 `json:"last_heartbeat_ms,omitempty"`
	// LivenessAvailable distinguishes "no overlay" from "overlay says unknown".
	LivenessAvailable bool `json:"liveness_available"`
}

// langfuseClient builds a reader for the given profile, or nil when the
// profile has no Langfuse credentials.
//
// Credentials come from the profile's env files on disk, not the process
// environment: the app is a GUI binary and does not inherit a direnv shell.
// Langfuse API keys are project-scoped -- there is no key that reads across
// projects -- so resolving them per profile is also what keeps one profile's
// panel from reading another's traces.
func (a *App) langfuseClient(profile string) *langfuse.Client {
	home := a.profileWorkspaceHome(profile)
	if home == "" {
		return nil
	}
	env := brainbox.ReadProfileEnv(home)
	base := env["LANGFUSE_BASE_URL"]
	if base == "" {
		// Fall back to the configured integration URL so a profile that sets
		// only the keys still works.
		if cfg := a.getIntegrationConfig("langfuse"); cfg.Enabled {
			base = cfg.ActiveURL("")
		}
	}
	return langfuse.NewClient(base, env["LANGFUSE_PUBLIC_KEY"], env["LANGFUSE_SECRET_KEY"])
}

// ListAgentWork returns the Agent Work list: sessions from Langfuse, liveness
// overlaid from OpenSearch.
//
// profile is required and is never empty in practice -- it selects the Langfuse
// credentials, and those are project-scoped. An "all profiles" view is not
// available from Langfuse for that reason, which is a real narrowing versus the
// OpenSearch-only version and is stated in the UI.
func (a *App) ListAgentWork(profile, since string, limit int) ([]AgentWorkRow, error) {
	c := a.langfuseClient(profile)
	if c == nil {
		return nil, fmt.Errorf("Langfuse is not configured for profile %q "+
			"(needs LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY)", profile)
	}
	ctx, cancel := context.WithTimeout(context.Background(), agentSessionQueryTimeout)
	defer cancel()

	sessions, err := c.ListAgentSessions(ctx, profile, since, limit)
	if err != nil {
		return nil, err
	}

	out := make([]AgentWorkRow, 0, len(sessions))
	for _, s := range sessions {
		out = append(out, AgentWorkRow{
			SessionID:    s.SessionID,
			Workspace:    s.Workspace,
			FirstSeen:    s.FirstSeen,
			LastSeen:     s.LastSeen,
			Observations: s.Observations,
			Generations:  s.Generations,
			ToolCalls:    s.ToolCalls,
			Tokens:       s.Tokens,
			Models:       s.Models,
			CostUSD:      s.CostUSD,
		})
	}
	a.overlayLiveness(out, profile, since)
	return out, nil
}

// overlayLiveness stamps OpenSearch's liveness onto rows that have a matching
// session. Best effort: on any failure the rows keep LivenessAvailable false
// and the UI shows no dot, rather than the list failing.
func (a *App) overlayLiveness(rows []AgentWorkRow, profile, since string) {
	if len(rows) == 0 {
		return
	}
	url, err := a.opensearchAPIURL()
	if err != nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), agentSessionQueryTimeout)
	defer cancel()
	// Ask for more than len(rows): the two stores order differently, so a
	// session present in the Langfuse page may sit further down OpenSearch's.
	live, err := opensearch.NewClient(url).ListAgentSessions(ctx, profile, since, 200)
	if err != nil {
		return
	}
	mergeLiveness(rows, live)
}

// mergeLiveness stamps OpenSearch liveness onto matching rows.
//
// Pure, so the merge is testable without an App: the interesting cases are a
// Langfuse session OpenSearch has never seen (keeps no dot) and the reverse.
func mergeLiveness(rows []AgentWorkRow, live []opensearch.AgentSession) {
	byID := make(map[string]opensearch.AgentSession, len(live))
	for _, s := range live {
		byID[s.SessionID] = s
	}
	for i := range rows {
		s, ok := byID[rows[i].SessionID]
		if !ok {
			// No OpenSearch row: leave LivenessAvailable false so the UI shows
			// no dot. Defaulting to dormant would assert something unmeasured.
			continue
		}
		rows[i].Liveness = s.Liveness
		rows[i].LastActivityMS = s.LastActivityMS
		rows[i].LastHeartbeatMS = s.LastHeartbeatMS
		rows[i].LivenessAvailable = true
		// OpenSearch sees activity Langfuse has not ingested yet (33s vs
		// 725s measured), so it is the better answer for "last active".
		// RFC3339 in UTC with the same precision compares correctly as text.
		if s.LastSeen > rows[i].LastSeen {
			rows[i].LastSeen = s.LastSeen
		}
	}
}

// GetAgentWorkTimeline returns one session's observations as a tree, with the
// prompt and completion text.
//
// This is the view OpenSearch cannot serve at all.
func (a *App) GetAgentWorkTimeline(sessionID, profile string, limit int) (*langfuse.SessionTimeline, error) {
	c := a.langfuseClient(profile)
	if c == nil {
		return nil, fmt.Errorf("Langfuse is not configured for profile %q", profile)
	}
	// A timeline fetches up to 500 observations with their content, so it gets
	// a longer ceiling than the list's.
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	return c.GetSessionTimeline(ctx, sessionID, limit)
}
