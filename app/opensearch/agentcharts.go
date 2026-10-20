package opensearch

import (
	"context"
	"strings"
)

// AgentCharts is the trend and breakdown data behind the Agent Work chart tabs.
//
// Sourced from metrics-otel rather than Langfuse. Langfuse's Claude Code
// capture is a Stop hook, so it never sees delegated subagent work: the spec
// measured 813 tool calls / $309.05 there against 1,786 / $416.26 in OpenSearch
// for the SAME session. A cost chart that understates by roughly half is worse
// than no chart, because the figure looks authoritative.
//
// The source split is the view Langfuse cannot provide at all — the work its
// hook misses is precisely the subagent bucket.
type AgentCharts struct {
	// Granularity is the histogram interval actually used, so the UI can label
	// the axis instead of guessing.
	Granularity  string       `json:"granularity"`
	CostSeries   []ChartPoint `json:"cost_series"`
	CostByModel  []ChartShare `json:"cost_by_model"`
	CostBySource []ChartShare `json:"cost_by_source"`
}

// ChartPoint is one histogram bucket. TS is epoch milliseconds.
type ChartPoint struct {
	TS    int64   `json:"ts"`
	Value float64 `json:"value"`
}

// ChartShare is one slice of a breakdown.
type ChartShare struct {
	Name  string  `json:"name"`
	Value float64 `json:"value"`
}

// costMetric is the counter Claude Code reports spend on; it carries model,
// query_source and effort as metric attributes.
const costMetric = "claude_code.cost.usage"

// intervalFor picks a histogram interval from the relative window the panel
// asked for. A 15-minute window bucketed at 3h is a single bar; a 3-day window
// bucketed at a minute is 4,320 of them.
func intervalFor(since string) string {
	switch {
	case strings.HasSuffix(since, "-15m"), strings.HasSuffix(since, "-30m"):
		return "1m"
	case strings.HasSuffix(since, "-60m"), strings.HasSuffix(since, "-1h"):
		return "5m"
	case strings.HasSuffix(since, "-24h"):
		return "1h"
	default:
		return "3h"
	}
}

// GetAgentCharts aggregates Claude Code spend over the given window.
func (c *Client) GetAgentCharts(ctx context.Context, workspace, since string) (*AgentCharts, error) {
	interval := intervalFor(since)

	must := []any{
		map[string]any{"term": map[string]any{"name": costMetric}},
		dayRange(since),
	}
	must = append(must, workspaceFilter(workspace)...)

	// Sum of `value` in every bucket: the metric is a DELTA counter
	// (aggregationTemporality AGGREGATION_TEMPORALITY_DELTA), so summing the
	// points in a window is the window's spend. A max or an avg would be wrong.
	usd := map[string]any{"usd": map[string]any{"sum": map[string]any{"field": "value"}}}

	body := map[string]any{
		"size":             0,
		"track_total_hits": false,
		"query":            map[string]any{"bool": map[string]any{"must": must}},
		"aggs": map[string]any{
			"trend": map[string]any{
				"date_histogram": map[string]any{
					"field":          "time",
					"fixed_interval": interval,
					// Buckets are labelled in the operator's day, matching the
					// "today" cards; UTC labels on an evening chart read as
					// tomorrow.
					"time_zone": localDayOffset(),
				},
				"aggs": usd,
			},
			"by_model": map[string]any{
				"terms": map[string]any{"field": "metric.attributes.model.keyword", "size": 8},
				"aggs":  usd,
			},
			"by_source": map[string]any{
				"terms": map[string]any{"field": "metric.attributes.query_source.keyword", "size": 8},
				"aggs":  usd,
			},
		},
	}

	var resp struct {
		Aggregations struct {
			Trend struct {
				Buckets []struct {
					Key int64 `json:"key"`
					USD struct {
						Value float64 `json:"value"`
					} `json:"usd"`
				} `json:"buckets"`
			} `json:"trend"`
			ByModel  termsWithSum `json:"by_model"`
			BySource termsWithSum `json:"by_source"`
		} `json:"aggregations"`
	}
	if err := c.search(ctx, "metrics-otel-*", body, &resp); err != nil {
		return nil, err
	}

	// Never nil: these marshal as JSON null and the panel iterates them.
	out := &AgentCharts{
		Granularity:  interval,
		CostSeries:   make([]ChartPoint, 0, len(resp.Aggregations.Trend.Buckets)),
		CostByModel:  resp.Aggregations.ByModel.shares(),
		CostBySource: resp.Aggregations.BySource.shares(),
	}
	for _, b := range resp.Aggregations.Trend.Buckets {
		out.CostSeries = append(out.CostSeries, ChartPoint{TS: b.Key, Value: b.USD.Value})
	}
	return out, nil
}

type termsWithSum struct {
	Buckets []struct {
		Key string `json:"key"`
		USD struct {
			Value float64 `json:"value"`
		} `json:"usd"`
	} `json:"buckets"`
}

func (t termsWithSum) shares() []ChartShare {
	out := make([]ChartShare, 0, len(t.Buckets))
	for _, b := range t.Buckets {
		out = append(out, ChartShare{Name: b.Key, Value: b.USD.Value})
	}
	return out
}
