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
		// A 401 is a configuration answer, not a transient failure, and it is
		// worth naming the profile: each profile carries its OWN Langfuse key
		// pair, so "invalid credentials" means THAT profile's keys, not the
		// app's. Observed with a profile whose pair was stale while another
		// profile's worked against the same host.
		if strings.Contains(err.Error(), "http 401") {
			return nil, fmt.Errorf(
				"Langfuse rejected profile %q's credentials (HTTP 401). Each "+
					"profile has its own key pair; check LANGFUSE_PUBLIC_KEY and "+
					"LANGFUSE_SECRET_KEY in that profile's env file, or remove them "+
					"to hide this panel for it", profile)
		}
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
	// The goroutine must NOT touch `out`: on the timeout path we return it to
	// Wails for marshalling while the goroutine is still running, so a merge
	// there would race the marshaller -- under exactly the slow-OpenSearch
	// condition this deadline exists to handle. So it only fetches, and the
	// merge happens here, on the one goroutine that owns the slice.
	//
	// Buffered so a late send cannot block, which would leak the goroutine.
	overlayCh := make(chan []opensearch.AgentSession, 1)
	go func() {
		// A panic in the overlay must not take down the list either.
		defer func() {
			if r := recover(); r != nil {
				overlayCh <- nil
			}
		}()
		overlayCh <- a.fetchLiveness(profile, since)
	}()
	select {
	case live := <-overlayCh:
		// nil on any failure: mergeLiveness then leaves every row untouched.
		mergeLiveness(out, live)
	case <-time.After(livenessOverlayTimeout):
		// Rows keep LivenessAvailable false, so the UI shows a hollow dot and
		// explains that liveness was unavailable. The work itself still lists.
		logErr("agent-work: liveness overlay timed out after %s; listing without it",
			livenessOverlayTimeout)
	}
	return out, nil
}

// fetchLiveness reads OpenSearch's liveness for a profile. Best effort: on any
// failure it returns nil, so the caller's merge leaves rows with
// LivenessAvailable false and the UI shows no dot rather than the list failing.
//
// It deliberately returns the sessions instead of stamping rows itself -- see
// the race note at the call site.
func (a *App) fetchLiveness(profile, since string) []opensearch.AgentSession {
	url, err := a.opensearchAPIURL()
	if err != nil {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), agentSessionQueryTimeout)
	defer cancel()
	// Ask for more than the page size: the two stores order differently, so a
	// session present in the Langfuse page may sit further down OpenSearch's.
	live, err := opensearch.NewClient(url).ListAgentSessions(ctx, profile, since, 200)
	if err != nil {
		return nil
	}
	return live
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

// ChartPoint is one sample of a chart time series. TS is unix SECONDS, which
// is what the frontend's MetricsChart component consumes.
type ChartPoint struct {
	TS    int64   `json:"ts"`
	Value float64 `json:"value"`
}

// ChartShare is one slice of a categorical breakdown, for PieChart.
type ChartShare struct {
	Name  string  `json:"name"`
	Value float64 `json:"value"`
}

// AgentWorkCharts is the chart-tab payload: the aggregations plus deep links
// into Langfuse's own UI for the same data.
//
// Declared FLAT with its own types rather than embedding *langfuse.Charts.
// Wails' TypeScript generator does not follow an embedded pointer to another
// package's struct: it emitted a class referencing langfuse.Point and
// langfuse.Share without defining either, and the generated models.ts stopped
// type-checking. Keeping the binding surface in this package also stops the
// read layer's types leaking into the frontend contract.
type AgentWorkCharts struct {
	Granularity string       `json:"granularity"`
	CostSeries  []ChartPoint `json:"cost_series"`
	CostByModel []ChartShare `json:"cost_by_model"`
	// CostBySource is main / subagent / auxiliary. It replaced an
	// "observations by type" breakdown that came from Langfuse, and it is the
	// view Langfuse cannot provide: the delegated work its Stop hook misses is
	// precisely the subagent bucket.
	CostBySource []ChartShare `json:"cost_by_source"`
}

// chartsQueryTimeout is longer than the list's: GetCharts issues four
// aggregations, and a metrics query scans a wider window than a session page.
const chartsQueryTimeout = 20 * time.Second

// DEPRECATED, 2026-10-19: ListAgentWork, GetAgentWorkTimeline and
// GetAgentWorkFreshness below are no longer called by any panel. The Agent Work
// panel reads ListAgentSessions / GetAgentSessionTimeline (app_agent_sessions.go,
// OpenSearch) instead, and this file's charts read metrics-otel.
//
// They are left in place for one release rather than deleted in the same change
// that switched the reader, so a regression can be compared against the old
// path. Delete them and app/langfuse with the rest of the Langfuse teardown —
// leaving two session readers with the same method names is what made the
// OpenSearch one sit unused and unnoticed in the first place.

// GetAgentWorkCharts returns the trend and breakdown data for the chart tabs.
//
// Sourced from OpenSearch metrics, NOT Langfuse. Langfuse's Claude Code capture
// is a Stop hook and never sees delegated subagent work: the spec measured 813
// tool calls / $309.05 there against 1,786 / $416.26 in OpenSearch for the same
// session. These charts previously carried a CostUnderstated flag so the UI
// could label a figure that was wrong by roughly half; the figure is now right
// and the flag is gone.
//
// This does NOT move session reading. Langfuse still owns that — it holds the
// prompt and completion text, which OpenSearch strips by design — so the links
// below still point there. Aggregates were never Langfuse's job.
func (a *App) GetAgentWorkCharts(profile, since string) (*AgentWorkCharts, error) {
	url, err := a.opensearchAPIURL()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), chartsQueryTimeout)
	defer cancel()

	charts, err := opensearch.NewClient(url).GetAgentCharts(ctx, profile, since)
	if err != nil {
		return nil, err
	}

	pts := func(in []opensearch.ChartPoint) []ChartPoint {
		// Never nil: these marshal as JSON null and the panel iterates them.
		out := make([]ChartPoint, 0, len(in))
		for _, p := range in {
			out = append(out, ChartPoint{TS: p.TS, Value: p.Value})
		}
		return out
	}
	shares := func(in []opensearch.ChartShare) []ChartShare {
		out := make([]ChartShare, 0, len(in))
		for _, sh := range in {
			out = append(out, ChartShare{Name: sh.Name, Value: sh.Value})
		}
		return out
	}

	out := &AgentWorkCharts{
		Granularity:  charts.Granularity,
		CostSeries:   pts(charts.CostSeries),
		CostByModel:  shares(charts.CostByModel),
		CostBySource: shares(charts.CostBySource),
	}

	// The Langfuse deep links are gone with the panel buttons that opened them.
	// Resolving a project id costs a round trip, and the URLs pointed at a
	// store that is being decommissioned.
	return out, nil
}

// AgentWorkFreshness reports how far behind Langfuse is.
//
// Called by the panel when a list comes back EMPTY, which is routine on a short
// range and looks exactly like a broken panel otherwise. The Claude Code
// capture is a Stop hook: it writes once per COMPLETED turn, so nothing lands
// during a long turn. Measured while the short ranges were added -- the newest
// observation was 148 minutes old while the session was actively working, so
// every window under an hour was legitimately empty.
type AgentWorkFreshness struct {
	// NewestISO is empty when the project holds no observations at all, which
	// the UI must distinguish from "nothing in this window".
	NewestISO string `json:"newest_iso,omitempty"`
	AgeMS     int64  `json:"age_ms"`
	HasData   bool   `json:"has_data"`
}

// GetAgentWorkFreshness returns the age of the newest observation.
func (a *App) GetAgentWorkFreshness(profile string) (*AgentWorkFreshness, error) {
	c := a.langfuseClient(profile)
	if c == nil {
		return nil, fmt.Errorf("Langfuse is not configured for profile %q", profile)
	}
	ctx, cancel := context.WithTimeout(context.Background(), agentSessionQueryTimeout)
	defer cancel()

	t, err := c.Freshness(ctx)
	if err != nil {
		return nil, err
	}
	out := &AgentWorkFreshness{}
	if t.IsZero() {
		return out, nil
	}
	out.HasData = true
	out.NewestISO = t.UTC().Format(time.RFC3339)
	// Clamp: the app's clock and the ingest path's are independent, and a
	// slightly-future timestamp is skew rather than an age of minus three
	// minutes.
	if ms := time.Since(t).Milliseconds(); ms > 0 {
		out.AgeMS = ms
	}
	return out, nil
}
