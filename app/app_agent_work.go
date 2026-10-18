package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"phantom-ink/brainbox"
	"phantom-ink/langfuse"
	"phantom-ink/opensearch"
)

// livenessOverlayTimeout bounds the OpenSearch half of the list.
//
// Shorter than agentSessionQueryTimeout on purpose: the overlay is optional, so
// it should give up well before the panel's own poll interval comes round
// again. The list is already in hand by the time this starts.
const livenessOverlayTimeout = 3 * time.Second

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

// strictProfileHome resolves a profile's workspace home WITHOUT the ambient
// fallback, and returns "" when it cannot be established for that exact
// profile.
//
// a.profileWorkspaceHome must not be used for credentials. Its last resort is
// envLookup(profileEnv(name), "WORKSPACE_HOME"), and profileEnv starts from
// os.Environ() -- so an unknown or mistyped profile silently resolves to
// whichever workspace launched the app, and this function would then hand back
// THAT profile's Langfuse keys. Caught by a test asking for a profile that does
// not exist and getting "personal" back.
//
// Failing closed is the right direction here: the panel says "not configured"
// rather than quietly reading another profile's traces.
func (a *App) strictProfileHome(profile string) string {
	if profile == "" || a.config == nil || a.config.WorkspacesRoot == "" {
		return ""
	}
	// Reject anything that could escape the workspaces root.
	if profile != filepath.Base(profile) || profile == "." || profile == ".." {
		return ""
	}
	home := filepath.Join(a.config.WorkspacesRoot, profile)
	if fi, err := os.Stat(home); err != nil || !fi.IsDir() {
		return ""
	}
	return home
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
	home := a.strictProfileHome(profile)
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

// langfuseEnvironment is the value to filter observations on for this profile.
//
// It is NOT the profile name. The environment is stamped at emission from
// LANGFUSE_TRACING_ENVIRONMENT, so that variable is the authority; the profile
// name is only a fallback for a profile that never set it. Assuming the two are
// equal is how the list ends up correctly empty and silently unexplained -- they
// happen to match on this machine, which is exactly what hides the bug.
func (a *App) langfuseEnvironment(profile string) string {
	if home := a.strictProfileHome(profile); home != "" {
		if env := brainbox.ReadProfileEnv(home)["LANGFUSE_TRACING_ENVIRONMENT"]; env != "" {
			return env
		}
	}
	return profile
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

	environment := a.langfuseEnvironment(profile)
	sessions, err := c.ListAgentSessions(ctx, environment, since, limit)
	if err != nil {
		return nil, err
	}
	if len(sessions) == 0 {
		// An empty list is ambiguous: nothing ran, or the environment filter
		// matches nothing. Name the environments that DO exist so the second
		// case is visible instead of looking like the first.
		if envs, eerr := c.Environments(ctx, since); eerr == nil && len(envs) > 0 {
			if _, ok := envs[environment]; !ok {
				names := make([]string, 0, len(envs))
				for name, n := range envs {
					names = append(names, fmt.Sprintf("%s (%d)", name, n))
				}
				sort.Strings(names)
				return nil, fmt.Errorf(
					"no sessions for environment %q; this project has: %s. "+
						"The environment is stamped from LANGFUSE_TRACING_ENVIRONMENT, "+
						"not from the profile name",
					environment, strings.Join(names, ", "))
			}
		}
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
	// HARD DEADLINE on the overlay. It is a decoration, and it must never be
	// able to stop the list from loading -- which is exactly what happened:
	// the panel sat on a permanent spinner because this half blocked.
	//
	// The blocking call is not the HTTP request (that is capped by the client
	// and the context) but a.opensearchAPIURL -> a.db.GetIntegration, a SQLite
	// read with NO context. database/sql waits indefinitely for a free
	// connection, and this app keeps a busy write-ahead log, so a contended
	// pool stalls the whole method. A goroutine plus a timeout makes that
	// unreachable regardless of the cause.
	overlayDone := make(chan struct{})
	go func() {
		defer close(overlayDone)
		// A panic in the overlay must not take down the list either.
		defer func() { _ = recover() }()
		a.overlayLiveness(out, profile, since)
	}()
	select {
	case <-overlayDone:
	case <-time.After(livenessOverlayTimeout):
		// Rows keep LivenessAvailable false, so the UI shows a hollow dot and
		// explains that liveness was unavailable. The work itself still lists.
		logErr("agent-work: liveness overlay timed out after %s; listing without it",
			livenessOverlayTimeout)
	}
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
