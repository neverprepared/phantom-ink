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
	"strings"
	"time"
)

type Client struct {
	baseURL string
	http    *http.Client
}

func NewClient(baseURL string) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: 5 * time.Second},
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

// Overview is the snapshot rendered in the Observability panel.
type Overview struct {
	CostTodayUSD     float64 `json:"cost_today_usd"`
	TokensToday      int64   `json:"tokens_today"`
	APIRequests1h    int64   `json:"api_requests_1h"`
	AvgLatencyMs1h   float64 `json:"avg_latency_ms_1h"`
	AsOf             string  `json:"as_of"`
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

// countAndAvgLatency runs a count + avg(duration_ms) over api_request log
// events, optionally constrained to a workspace.
func (c *Client) countAndAvgLatency(ctx context.Context, since, workspace string) (int64, float64, error) {
	must := []any{
		map[string]any{"term": map[string]any{
			"log.attributes.event@name.keyword": "api_request",
		}},
		map[string]any{"range": map[string]any{"time": map[string]any{"gte": since}}},
	}
	must = append(must, workspaceFilter(workspace)...)
	body := map[string]any{
		"size":             0,
		"track_total_hits": true,
		"query":            map[string]any{"bool": map[string]any{"must": must}},
		"aggs": map[string]any{
			"avg": map[string]any{"avg": map[string]any{"field": "log.attributes.duration_ms"}},
		},
	}
	var out aggAvgCountResp
	if err := c.search(ctx, "logs-otel-*", body, &out); err != nil {
		return 0, 0, err
	}
	var avg float64
	if out.Aggregations.Avg.Value != nil {
		avg = *out.Aggregations.Avg.Value
	}
	return out.Hits.Total.Value, avg, nil
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
	Time       string `json:"time"`
	Body       string `json:"body"`                  // event name (e.g. claude_code.api_request)
	Session    string `json:"session,omitempty"`
	Workspace  string `json:"workspace,omitempty"`
	Model      string `json:"model,omitempty"`
	DurationMs int64  `json:"duration_ms,omitempty"`
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
		"size": limit,
		"sort": []any{map[string]any{"time": map[string]any{"order": "desc"}}},
		"query": map[string]any{"bool": map[string]any{"must": must}},
		"_source": []string{
			"time", "body",
			"log.attributes.session@id",
			"resource.attributes.workspace",
			"log.attributes.model",
			"log.attributes.duration_ms",
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
		s := h.Source
		out = append(out, LogEntry{
			Time:       strOf(s["time"]),
			Body:       strOf(s["body"]),
			Session:    strOf(s["log.attributes.session@id"]),
			Workspace:  strOf(s["resource.attributes.workspace"]),
			Model:      strOf(s["log.attributes.model"]),
			DurationMs: intOf(s["log.attributes.duration_ms"]),
		})
	}
	return out, nil
}

func strOf(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

func intOf(v any) int64 {
	switch x := v.(type) {
	case float64:
		return int64(x)
	case int64:
		return x
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
