// Package opensearch is a minimal typed client for the local OpenSearch instance
// used by the Observability panel. Security is disabled on the local stack so
// no auth is required.
package opensearch

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

type Client struct {
	baseURL string
	http    *http.Client
	// now is the clock liveness ages are measured against. Injectable so the
	// thresholds can be tested at their exact boundaries instead of with
	// sleeps. Nil is safe — see clock().
	now func() time.Time
}

func NewClient(baseURL string) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: 5 * time.Second},
		now:     time.Now,
	}
}

// search runs a POST /<index>/_search and decodes into result.
func (c *Client) search(ctx context.Context, index string, body any, result any) error {
	data, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshal: %w", err)
	}
	url := fmt.Sprintf("%s/%s/_search", c.baseURL, index)
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(data))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("opensearch %d: %s", resp.StatusCode, strings.TrimSpace(string(b)))
	}
	return json.NewDecoder(resp.Body).Decode(result)
}

// Health is a lightweight liveness probe — GET /_cluster/health — returning nil
// when OpenSearch answers 2xx. Used to gate UI (e.g. the Stream Logs tab) on
// real reachability, since the endpoint is often the remote platform node.
func (c *Client) Health(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, "GET", c.baseURL+"/_cluster/health", nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("opensearch %d: %s", resp.StatusCode, strings.TrimSpace(string(b)))
	}
	return nil
}

// Overview is the snapshot rendered in the Observability panel.
type Overview struct {
	CostTodayUSD   float64 `json:"cost_today_usd"`
	TokensToday    int64   `json:"tokens_today"`
	APIRequests1h  int64   `json:"api_requests_1h"`
	AvgLatencyMs1h float64 `json:"avg_latency_ms_1h"`
	AsOf           string  `json:"as_of"`
	// Workspace is the workspace filter applied to this query, "" for unfiltered.
	Workspace string `json:"workspace"`
	// MatchedWorkspace is true when the workspace filter matched ≥1 doc in the
	// last 24h.  Always true when Workspace is "". The panel uses this to decide
	// whether to show the "set OTEL_RESOURCE_ATTRIBUTES" hint.
	MatchedWorkspace bool `json:"matched_workspace"`
}

type aggSumResp struct {
	Aggregations struct {
		Total struct {
			Value float64 `json:"value"`
		} `json:"total"`
	} `json:"aggregations"`
}

type aggAvgCountResp struct {
	Hits struct {
		Total struct {
			Value int64 `json:"value"`
		} `json:"total"`
	} `json:"hits"`
	Aggregations struct {
		Avg struct {
			// avg of an empty bucket comes back as null; pointer distinguishes
			// "no data" from a real 0.
			Value *float64 `json:"value"`
		} `json:"avg"`
	} `json:"aggregations"`
}

// workspaceFilter returns the extra "must" clause for a workspace filter, or
// nil when workspace is empty (= unfiltered, show everything).
func workspaceFilter(workspace string) []any {
	if workspace == "" {
		return nil
	}
	return []any{
		map[string]any{"term": map[string]any{
			"resource.attributes.workspace.keyword": workspace,
		}},
	}
}

// sumOfMetric runs a sum-of-value aggregation filtered by metric name and time,
// optionally constrained to a workspace.
func (c *Client) sumOfMetric(ctx context.Context, name, since, workspace string) (float64, error) {
	must := []any{
		map[string]any{"term": map[string]any{"name": name}},
		map[string]any{"range": map[string]any{"time": map[string]any{"gte": since}}},
	}
	must = append(must, workspaceFilter(workspace)...)
	body := map[string]any{
		"size":             0,
		"track_total_hits": false,
		"query":            map[string]any{"bool": map[string]any{"must": must}},
		"aggs": map[string]any{
			"total": map[string]any{"sum": map[string]any{"field": "value"}},
		},
	}
	var out aggSumResp
	if err := c.search(ctx, "metrics-otel-*", body, &out); err != nil {
		return 0, err
	}
	return out.Aggregations.Total.Value, nil
}

// countAndAvgLatency counts api_request log events and averages their
// duration_ms, optionally constrained to a workspace. The two are queried
// SEPARATELY on purpose: log.attributes.duration_ms is dynamically mapped as
// text on some daily indices, so an avg agg over it throws an
// illegal_argument_exception on those shards. OpenSearch answers such a partial
// failure with HTTP 200 + a truncated hits.total (only the surviving shards),
// which would silently zero the request count if count and avg shared one query.
// Count therefore runs agg-free (authoritative); latency is best-effort and
// tolerates the shard failure (falls back to 0 until the mapping is corrected).
func (c *Client) countAndAvgLatency(ctx context.Context, since, workspace string) (int64, float64, error) {
	must := []any{
		map[string]any{"term": map[string]any{
			"log.attributes.event@name.keyword": "api_request",
		}},
		map[string]any{"range": map[string]any{"time": map[string]any{"gte": since}}},
	}
	must = append(must, workspaceFilter(workspace)...)
	query := map[string]any{"bool": map[string]any{"must": must}}

	var cnt aggAvgCountResp
	if err := c.search(ctx, "logs-otel-*", map[string]any{
		"size":             0,
		"track_total_hits": true,
		"query":            query,
	}, &cnt); err != nil {
		return 0, 0, err
	}

	var lat aggAvgCountResp
	// Best-effort: a mapping-conflict shard failure here must not fail the card.
	_ = c.search(ctx, "logs-otel-*", map[string]any{
		"size":  0,
		"query": query,
		"aggs": map[string]any{
			"avg": map[string]any{"avg": map[string]any{"field": "log.attributes.duration_ms"}},
		},
	}, &lat)
	var avg float64
	if lat.Aggregations.Avg.Value != nil {
		avg = *lat.Aggregations.Avg.Value
	}
	return cnt.Hits.Total.Value, avg, nil
}

// hasWorkspaceData returns true when ≥1 doc in the last 24h carries the given
// workspace tag. Used to drive the "set OTEL_RESOURCE_ATTRIBUTES" hint when the
// user has selected a profile but no data matches it.
func (c *Client) hasWorkspaceData(ctx context.Context, workspace string) (bool, error) {
	if workspace == "" {
		return true, nil
	}
	body := map[string]any{
		"size":             0,
		"track_total_hits": true,
		"terminate_after":  1,
		"query": map[string]any{"bool": map[string]any{"must": []any{
			map[string]any{"range": map[string]any{"time": map[string]any{"gte": "now-24h"}}},
			map[string]any{"term": map[string]any{
				"resource.attributes.workspace.keyword": workspace,
			}},
		}}},
	}
	var out aggAvgCountResp
	if err := c.search(ctx, "logs-otel-*,metrics-otel-*", body, &out); err != nil {
		return false, err
	}
	return out.Hits.Total.Value > 0, nil
}

// LogEntry is a single row rendered in the Stream panel's Live tab.
type LogEntry struct {
	Time      string `json:"time"`
	Body      string `json:"body"` // event name (e.g. claude_code.api_request)
	Session   string `json:"session,omitempty"`
	Workspace string `json:"workspace,omitempty"`
	Model     string `json:"model,omitempty"`
	// DurationMs is normalised across the several spellings emitters use:
	// duration_ms (api_request, tool_result) and total_duration_ms (hook
	// events). Before this, hook events rendered with no duration at all
	// because only the first spelling was read.
	DurationMs int64 `json:"duration_ms,omitempty"`

	// Severity is severityText. Currently empty on every claude-code record,
	// which is why log levels cannot be filtered on; carried anyway so our own
	// emitters (which do set it) are usable the day they start shipping.
	Severity string `json:"severity,omitempty"`
	// Service is resource.attributes.service@name — the only thing separating
	// claude-code's own telemetry from a platform service's.
	Service string `json:"service,omitempty"`
	// Event is log.attributes.event@name, e.g. "hook_execution_complete".
	// Preferred over parsing Body, which is a text field with no keyword
	// subfield and so cannot be aggregated on.
	Event string `json:"event,omitempty"`

	// Attrs carries the remaining event-specific attributes, keyed without the
	// "log.attributes." prefix (hook_name, tool_name, num_success, success,
	// error_type, …). These are what make a line meaningful, and the previous
	// _source allowlist never fetched them: a failed tool_result rendered
	// identically to a successful one.
	Attrs map[string]any `json:"attrs,omitempty"`
}

// TailLogs returns the most-recent `limit` log entries, newest first, optionally
// constrained to a workspace.  Designed for a polled live tail in the UI; the
// caller bounds list size, OpenSearch handles pagination internally.
func (c *Client) TailLogs(ctx context.Context, workspace string, limit int) ([]LogEntry, error) {
	if limit <= 0 || limit > 1000 {
		limit = 1000
	}
	must := []any{
		map[string]any{"range": map[string]any{"time": map[string]any{"gte": "now-24h"}}},
	}
	must = append(must, workspaceFilter(workspace)...)
	body := map[string]any{
		"size":  limit,
		"sort":  []any{map[string]any{"time": map[string]any{"order": "desc"}}},
		"query": map[string]any{"bool": map[string]any{"must": must}},
		// Take every event attribute rather than an allowlist. The previous
		// list named six fields, so the attributes that carry the actual
		// meaning (hook_name, tool_name, success, error_type, the num_* counts)
		// were never fetched and a line could only ever render its event name.
		// Documents are small and the query is capped at 1000.
		//
		// user@* and organization@* are excluded: they are identical on every
		// record from a given machine, so they are pure noise in a tail, and
		// there is no reason to pull an email address into the UI to render a
		// log line.
		"_source": map[string]any{
			"includes": []string{
				"time", "body", "severityText",
				"resource.attributes.workspace",
				"resource.attributes.service@name",
				"log.attributes.*",
			},
			"excludes": []string{
				"log.attributes.user@*",
				"log.attributes.organization@*",
			},
		},
	}
	var resp struct {
		Hits struct {
			Hits []struct {
				Source map[string]any `json:"_source"`
			} `json:"hits"`
		} `json:"hits"`
	}
	if err := c.search(ctx, "logs-otel-*", body, &resp); err != nil {
		return nil, err
	}
	out := make([]LogEntry, 0, len(resp.Hits.Hits))
	for _, h := range resp.Hits.Hits {
		out = append(out, projectLogEntry(h.Source))
	}
	return out, nil
}

// logAttrPrefix is how Data Prepper flattens OTEL log attributes: the document
// key is the literal dotted string "log.attributes.hook_name", not a nested
// object. Reading it as nested yields nothing, which is how these records got
// mistaken for attribute-free.
const logAttrPrefix = "log.attributes."

// promotedAttrs are lifted into typed LogEntry fields, so they are not repeated
// in Attrs.
var promotedAttrs = map[string]bool{
	"session@id":        true,
	"event@name":        true,
	"model":             true,
	"duration_ms":       true,
	"total_duration_ms": true,
}

// isIdentityAttr reports attributes that identify the human or org rather than
// the event. They are already excluded by the query's _source, but a tail must
// never render an email address just to draw a log line, and an exclude list in
// a query body is easy to edit away by accident — so the projection refuses
// them too.
func isIdentityAttr(name string) bool {
	return strings.HasPrefix(name, "user@") || strings.HasPrefix(name, "organization@")
}

// projectLogEntry turns one OpenSearch _source into a LogEntry, promoting the
// common envelope into typed fields and keeping the event-specific remainder in
// Attrs for the caller to render.
func projectLogEntry(s map[string]any) LogEntry {
	e := LogEntry{
		Time:      strOf(s["time"]),
		Body:      strOf(s["body"]),
		Severity:  strOf(s["severityText"]),
		Session:   strOf(s[logAttrPrefix+"session@id"]),
		Workspace: strOf(s["resource.attributes.workspace"]),
		Service:   strOf(s["resource.attributes.service@name"]),
		Model:     strOf(s[logAttrPrefix+"model"]),
		Event:     strOf(s[logAttrPrefix+"event@name"]),
	}

	// Emitters disagree on the spelling; take whichever is present.
	if d := intOf(s[logAttrPrefix+"duration_ms"]); d > 0 {
		e.DurationMs = d
	} else {
		e.DurationMs = intOf(s[logAttrPrefix+"total_duration_ms"])
	}

	for k, v := range s {
		name, ok := strings.CutPrefix(k, logAttrPrefix)
		if !ok || promotedAttrs[name] || v == nil || isIdentityAttr(name) {
			continue
		}
		if e.Attrs == nil {
			e.Attrs = make(map[string]any, 8)
		}
		e.Attrs[name] = v
	}
	return e
}

func strOf(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

// intOf coerces a numeric attribute regardless of how OpenSearch typed it.
//
// The same field name arrives as different JSON types depending on the event,
// because these indices are dynamically mapped and whichever document landed
// first in an index decided the type: duration_ms is a string on tool_result
// but a float64 on api_request, and every num_* on a hook event is a string.
// Reading only the numeric forms silently yielded 0 — a duration that renders
// as absent rather than wrong, which is the harder kind of bug to notice.
func intOf(v any) int64 {
	switch x := v.(type) {
	case float64:
		return int64(x)
	case int64:
		return x
	case int:
		return int64(x)
	case json.Number:
		if n, err := x.Int64(); err == nil {
			return n
		}
		if f, err := x.Float64(); err == nil {
			return int64(f)
		}
	case string:
		s := strings.TrimSpace(x)
		if s == "" {
			return 0
		}
		if n, err := strconv.ParseInt(s, 10, 64); err == nil {
			return n
		}
		if f, err := strconv.ParseFloat(s, 64); err == nil {
			return int64(f)
		}
	}
	return 0
}

// GetOverview fetches all four panel cards. When workspace is non-empty all
// queries are constrained to that resource attribute; an additional probe runs
// to populate MatchedWorkspace so the UI can offer setup guidance when needed.
func (c *Client) GetOverview(ctx context.Context, workspace string) (*Overview, error) {
	cost, err := c.sumOfMetric(ctx, "claude_code.cost.usage", "now/d", workspace)
	if err != nil {
		return nil, fmt.Errorf("cost: %w", err)
	}
	tokens, err := c.sumOfMetric(ctx, "claude_code.token.usage", "now/d", workspace)
	if err != nil {
		return nil, fmt.Errorf("tokens: %w", err)
	}
	reqs, latency, err := c.countAndAvgLatency(ctx, "now-1h", workspace)
	if err != nil {
		return nil, fmt.Errorf("requests: %w", err)
	}
	matched, err := c.hasWorkspaceData(ctx, workspace)
	if err != nil {
		// Don't fail the overview if just the probe errors — fall back to true
		// so the UI shows numbers rather than the unrelated setup hint.
		matched = true
	}
	return &Overview{
		CostTodayUSD:     cost,
		TokensToday:      int64(tokens),
		APIRequests1h:    reqs,
		AvgLatencyMs1h:   latency,
		Workspace:        workspace,
		MatchedWorkspace: matched,
		AsOf:             time.Now().UTC().Format(time.RFC3339),
	}, nil
}
