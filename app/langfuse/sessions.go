package langfuse

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"time"
)

// AgentSession is one row of the session list.
//
// Field names match app/opensearch.AgentSession where the meaning is the same,
// so the panel renders either source without a second set of bindings.
type AgentSession struct {
	SessionID string `json:"session_id"`
	// Workspace is Langfuse's `environment`, which is where the profile is
	// stamped (LANGFUSE_TRACING_ENVIRONMENT). Named for the panel's vocabulary.
	Workspace string `json:"workspace"`
	FirstSeen string `json:"first_seen"`
	LastSeen  string `json:"last_seen"`
	// Observations counts every observation in the session — generations, tool
	// calls and spans together. It is NOT comparable to the OpenSearch
	// ToolCalls figure, which counts tool_result docs only.
	Observations int64 `json:"observations"`
	Generations  int64 `json:"generations"`
	ToolCalls    int64 `json:"tool_calls"`
	// CostUSD comes from Langfuse's own pricing of generations.
	//
	// It is LOWER than the OpenSearch figure for the same session, and the gap
	// is the hook's blind spot, not a rounding difference: measured on one
	// session, $318.82 here against $515.78 in OpenSearch. Present it as
	// Langfuse's view, never as the authoritative spend.
	CostUSD float64 `json:"cost_usd"`
	Tokens  int64   `json:"tokens"`
	// LatencyMS is the SUM of observation latencies, so it exceeds wall-clock
	// duration whenever anything ran concurrently. Use FirstSeen/LastSeen for
	// elapsed time.
	LatencyMS int64    `json:"latency_ms"`
	Models    []string `json:"models"`
}

// SessionTimeline is the drill-down: one session's observations as a tree.
type SessionTimeline struct {
	SessionID string `json:"session_id"`
	Workspace string `json:"workspace"`
	// Observations are ordered by start time, with Depth precomputed from the
	// parent links so the frontend can render indentation without walking.
	Observations []Observation `json:"observations"`
	Total        int           `json:"total"`
	// Truncated reports that the session has more observations than were
	// fetched, so a partial tree is never shown as a complete one.
	Truncated bool `json:"truncated"`
	// ContentAvailable is false when no observation carried input or output.
	// Native OTLP spans carry neither (verified: input and output are null on
	// claude_code.* observations), so a session captured only by the span path
	// has structure but no conversation.
	ContentAvailable bool `json:"content_available"`
}

// Observation is one node of the timeline.
type Observation struct {
	ID       string `json:"id"`
	ParentID string `json:"parent_id,omitempty"`
	Depth    int    `json:"depth"`
	Name     string `json:"name"`
	// Type is GENERATION, TOOL, SPAN or EVENT as Langfuse classifies it. A
	// native OTLP span whose attributes follow the gen_ai.* conventions is
	// classified GENERATION automatically.
	Type      string  `json:"type"`
	StartTime string  `json:"start_time"`
	EndTime   string  `json:"end_time,omitempty"`
	LatencyMS float64 `json:"latency_ms,omitempty"`
	Model     string  `json:"model,omitempty"`
	CostUSD   float64 `json:"cost_usd,omitempty"`
	// Level and StatusMessage carry an error when the observation failed.
	Level         string `json:"level,omitempty"`
	StatusMessage string `json:"status_message,omitempty"`
	// Input and Output are the conversation. Rendered as compact JSON when the
	// API returns a structure rather than a string.
	Input  string `json:"input,omitempty"`
	Output string `json:"output,omitempty"`
}

// --- wire types -------------------------------------------------------------

type obsRow struct {
	ID                  string          `json:"id"`
	Name                *string         `json:"name"`
	Type                string          `json:"type"`
	StartTime           string          `json:"startTime"`
	EndTime             *string         `json:"endTime"`
	Latency             *float64        `json:"latency"`
	SessionID           *string         `json:"sessionId"`
	TraceID             *string         `json:"traceId"`
	ParentObservationID *string         `json:"parentObservationId"`
	Environment         *string         `json:"environment"`
	Level               *string         `json:"level"`
	StatusMessage       *string         `json:"statusMessage"`
	ModelID             *string         `json:"modelId"`
	Model               *string         `json:"model"`
	TotalPrice          *float64        `json:"totalPrice"`
	Input               json.RawMessage `json:"input"`
	Output              json.RawMessage `json:"output"`
}

type obsResponse struct {
	Data []obsRow `json:"data"`
	Meta struct {
		Cursor string `json:"cursor"`
	} `json:"meta"`
}

// --- helpers ----------------------------------------------------------------

var dateMath = regexp.MustCompile(`^now-(\d+)([smhd])$`)

// sinceTime translates the OpenSearch-style date math the panel already sends
// ("now-24h") into the absolute timestamp Langfuse requires. Anything
// unrecognised falls back to 24h rather than erroring: a list that covers the
// wrong window is recoverable, one that refuses to load is not.
func sinceTime(since string, now time.Time) time.Time {
	m := dateMath.FindStringSubmatch(since)
	if m == nil {
		return now.Add(-24 * time.Hour)
	}
	n, err := strconv.Atoi(m[1])
	if err != nil || n <= 0 {
		return now.Add(-24 * time.Hour)
	}
	unit := map[string]time.Duration{
		"s": time.Second, "m": time.Minute, "h": time.Hour, "d": 24 * time.Hour,
	}[m[2]]
	return now.Add(-time.Duration(n) * unit)
}

func str(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

func f64(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}

// jsonText renders an input/output field for display. The API returns either a
// JSON string or a structure; both become a single string so the frontend does
// not branch on shape.
func jsonText(raw json.RawMessage) string {
	if len(raw) == 0 || string(raw) == "null" {
		return ""
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s
	}
	return string(raw)
}

// --- session list -----------------------------------------------------------

// metricsRow is one grouped row from /api/public/v2/metrics. The API names
// result keys `<aggregation>_<measure>`.
type metricsRow struct {
	SessionID     *string  `json:"sessionId"`
	CountCount    *float64 `json:"count_count"`
	SumTotalCost  *float64 `json:"sum_totalCost"`
	SumTotalToken *float64 `json:"sum_totalTokens"`
	SumLatency    *float64 `json:"sum_latency"`
}

type metricsResponse struct {
	Data []metricsRow `json:"data"`
}

// ListAgentSessions returns recent sessions, most-recently-active first.
//
// TWO calls, because neither endpoint alone can do it:
//
//  1. /api/public/v2/observations gives RECENCY. It returns newest-first, and
//     paging one window of it yields the distinct sessions in the order the
//     panel wants.
//  2. /api/public/v2/metrics gives the AGGREGATES — observation count, cost,
//     tokens, summed latency — in one grouped query.
//
// The metrics call cannot supply the ordering: `sessionId` is a high-cardinality
// dimension, so the API demands `config.row_limit` plus `orderBy` DESC on a
// MEASURE field, and rejects ordering by `startTime` outright. Ordering by
// volume is not ordering by recency, which is the question this panel answers.
//
// The aggregates are a best-effort overlay: if the metrics call fails the list
// still returns, with counts derived from the observation page alone. A list
// that loads without cost beats one that does not load.
func (c *Client) ListAgentSessions(ctx context.Context, environment, since string, limit int) ([]AgentSession, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	from := sinceTime(since, time.Now().UTC())

	// Page size is deliberately generous relative to limit: a session has many
	// observations, so covering `limit` DISTINCT sessions needs far more than
	// `limit` rows. 1000 is the API's practical ceiling per page.
	q := url.Values{}
	q.Set("limit", "1000")
	// `core,basic` is required, not a nicety: with `fields=core` alone the
	// response omits sessionId ENTIRELY -- verified against the live API, 50 of
	// 50 rows with no such key -- so every row is unattributable and the list
	// comes back empty. environment and model live in `basic` too. This is the
	// same field-GROUP trap as `io` on the timeline.
	q.Set("fields", "core,basic")
	q.Set("fromStartTime", from.UTC().Format(time.RFC3339))
	if environment != "" {
		q.Set("environment", environment)
	}

	var page obsResponse
	if err := c.get(ctx, "/api/public/v2/observations", q, &page); err != nil {
		return nil, err
	}

	type acc struct {
		s                AgentSession
		models           map[string]bool
		firstT, lastT    time.Time
		obs, gens, tools int64
	}
	byID := map[string]*acc{}
	var order []string

	for _, r := range page.Data {
		// A null sessionId is an observation with no session — the router's
		// own spans land this way. They are real work but not an agent
		// session, and grouping them under one synthetic row would invent a
		// session that does not exist.
		sid := str(r.SessionID)
		if sid == "" {
			continue
		}
		a, ok := byID[sid]
		if !ok {
			a = &acc{
				s:      AgentSession{SessionID: sid, Workspace: str(r.Environment)},
				models: map[string]bool{},
			}
			byID[sid] = a
			// Observations arrive newest-first, so first sighting establishes
			// the recency order the panel wants.
			order = append(order, sid)
		}
		a.obs++
		switch r.Type {
		case "GENERATION":
			a.gens++
		case "TOOL":
			a.tools++
		}
		if m := str(r.Model); m != "" {
			a.models[m] = true
		} else if m := str(r.ModelID); m != "" {
			a.models[m] = true
		}
		if t, err := time.Parse(time.RFC3339, r.StartTime); err == nil {
			if a.firstT.IsZero() || t.Before(a.firstT) {
				a.firstT = t
			}
			if t.After(a.lastT) {
				a.lastT = t
			}
		}
	}

	// Aggregates overlay.
	agg := c.sessionAggregates(ctx, environment, from, limit)

	out := make([]AgentSession, 0, len(order))
	for _, sid := range order {
		if len(out) >= limit {
			break
		}
		a := byID[sid]
		s := a.s
		s.Observations, s.Generations, s.ToolCalls = a.obs, a.gens, a.tools
		if !a.firstT.IsZero() {
			s.FirstSeen = a.firstT.UTC().Format(time.RFC3339)
		}
		if !a.lastT.IsZero() {
			s.LastSeen = a.lastT.UTC().Format(time.RFC3339)
		}
		for m := range a.models {
			s.Models = append(s.Models, m)
		}
		sort.Strings(s.Models)
		if m, ok := agg[sid]; ok {
			// The metrics figures cover the whole window, not just the page,
			// so they supersede the page-derived count.
			if m.CountCount != nil {
				s.Observations = int64(*m.CountCount)
			}
			s.CostUSD = f64(m.SumTotalCost)
			s.Tokens = int64(f64(m.SumTotalToken))
			s.LatencyMS = int64(f64(m.SumLatency))
		}
		out = append(out, s)
	}
	return out, nil
}

// sessionAggregates returns per-session totals, keyed by session id. Errors are
// swallowed on purpose — see ListAgentSessions.
func (c *Client) sessionAggregates(ctx context.Context, environment string, from time.Time, limit int) map[string]metricsRow {
	query := map[string]any{
		"view":       "observations",
		"dimensions": []any{map[string]any{"field": "sessionId"}},
		"metrics": []any{
			map[string]any{"measure": "count", "aggregation": "count"},
			map[string]any{"measure": "totalCost", "aggregation": "sum"},
			map[string]any{"measure": "totalTokens", "aggregation": "sum"},
			map[string]any{"measure": "latency", "aggregation": "sum"},
		},
		"fromTimestamp": from.UTC().Format(time.RFC3339),
		"toTimestamp":   time.Now().UTC().Add(time.Minute).Format(time.RFC3339),
		// Both required for a high-cardinality dimension; the API rejects the
		// query without them.
		"orderBy": []any{map[string]any{"field": "count_count", "direction": "desc"}},
		"config":  map[string]any{"row_limit": limit * 4},
	}
	if environment != "" {
		query["filters"] = []any{map[string]any{
			"column": "environment", "operator": "=", "value": environment, "type": "string",
		}}
	}
	body, err := json.Marshal(query)
	if err != nil {
		return nil
	}
	q := url.Values{}
	q.Set("query", string(body))

	var resp metricsResponse
	if err := c.get(ctx, "/api/public/v2/metrics", q, &resp); err != nil {
		return nil
	}
	out := make(map[string]metricsRow, len(resp.Data))
	for _, r := range resp.Data {
		if sid := str(r.SessionID); sid != "" {
			out[sid] = r
		}
	}
	return out
}

// --- timeline ---------------------------------------------------------------

// GetSessionTimeline returns one session's observations as a tree, with the
// prompt and completion text.
//
// This is the view OpenSearch cannot serve: content is stripped from spans
// there by design, and the log attributes read `<REDACTED>`.
func (c *Client) GetSessionTimeline(ctx context.Context, sessionID string, limit int) (*SessionTimeline, error) {
	if sessionID == "" {
		return nil, fmt.Errorf("session id required")
	}
	if limit <= 0 || limit > 1000 {
		limit = 500
	}

	q := url.Values{}
	q.Set("sessionId", sessionID)
	// `io` is a field GROUP, not two field names. Passing "input,output" here
	// silently returns neither, which once led to a reviewer concluding
	// content was unavailable in this deployment.
	q.Set("fields", "core,basic,io")
	q.Set("limit", strconv.Itoa(limit))

	var resp obsResponse
	if err := c.get(ctx, "/api/public/v2/observations", q, &resp); err != nil {
		return nil, err
	}

	tl := &SessionTimeline{SessionID: sessionID, Total: len(resp.Data)}
	tl.Truncated = len(resp.Data) >= limit

	rows := make([]obsRow, len(resp.Data))
	copy(rows, resp.Data)
	// The API returns newest-first; a timeline reads forwards.
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].StartTime < rows[j].StartTime })

	parentOf := make(map[string]string, len(rows))
	present := make(map[string]bool, len(rows))
	for _, r := range rows {
		present[r.ID] = true
		parentOf[r.ID] = str(r.ParentObservationID)
	}

	// depth walks parent links, treating a parent outside this page as a root
	// so a truncated fetch cannot produce runaway indentation. The visited set
	// guards against a cycle rather than trusting the data.
	depth := func(id string) int {
		d, visited := 0, map[string]bool{id: true}
		for p := parentOf[id]; p != "" && present[p] && !visited[p]; p = parentOf[p] {
			visited[p] = true
			d++
			if d > 32 {
				break
			}
		}
		return d
	}

	for _, r := range rows {
		if tl.Workspace == "" {
			tl.Workspace = str(r.Environment)
		}
		o := Observation{
			ID:            r.ID,
			ParentID:      str(r.ParentObservationID),
			Depth:         depth(r.ID),
			Name:          str(r.Name),
			Type:          r.Type,
			StartTime:     r.StartTime,
			EndTime:       str(r.EndTime),
			LatencyMS:     f64(r.Latency),
			CostUSD:       f64(r.TotalPrice),
			Level:         str(r.Level),
			StatusMessage: str(r.StatusMessage),
			Input:         jsonText(r.Input),
			Output:        jsonText(r.Output),
		}
		if m := str(r.Model); m != "" {
			o.Model = m
		} else {
			o.Model = str(r.ModelID)
		}
		if o.Input != "" || o.Output != "" {
			tl.ContentAvailable = true
		}
		tl.Observations = append(tl.Observations, o)
	}
	if tl.Observations == nil {
		tl.Observations = []Observation{}
	}
	return tl, nil
}

// Environments returns the distinct `environment` values this project holds,
// newest window first, with an observation count each.
//
// Exists to make an EMPTY session list diagnosable. The environment is stamped
// from LANGFUSE_TRACING_ENVIRONMENT at emission; if the value the reader filters
// on does not match any of these, the list is correctly empty and silently
// unexplained. Showing the real values turns that into a visible mismatch.
func (c *Client) Environments(ctx context.Context, since string) (map[string]int64, error) {
	from := sinceTime(since, time.Now().UTC())
	query := map[string]any{
		"view":          "observations",
		"dimensions":    []any{map[string]any{"field": "environment"}},
		"metrics":       []any{map[string]any{"measure": "count", "aggregation": "count"}},
		"fromTimestamp": from.UTC().Format(time.RFC3339),
		"toTimestamp":   time.Now().UTC().Add(time.Minute).Format(time.RFC3339),
	}
	body, err := json.Marshal(query)
	if err != nil {
		return nil, err
	}
	q := url.Values{}
	q.Set("query", string(body))

	var resp struct {
		Data []struct {
			Environment *string  `json:"environment"`
			CountCount  *float64 `json:"count_count"`
		} `json:"data"`
	}
	if err := c.get(ctx, "/api/public/v2/metrics", q, &resp); err != nil {
		return nil, err
	}
	out := make(map[string]int64, len(resp.Data))
	for _, r := range resp.Data {
		name := "(none)"
		if r.Environment != nil && *r.Environment != "" {
			name = *r.Environment
		}
		out[name] = int64(f64(r.CountCount))
	}
	return out, nil
}
