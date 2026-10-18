package langfuse

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"sort"
	"strings"
	"time"
)

// Point is one sample of a time series.
//
// TS is unix SECONDS because that is what the frontend's MetricsChart
// component already consumes; matching it means no adapter on the panel side.
type Point struct {
	TS    int64   `json:"ts"`
	Value float64 `json:"value"`
}

// Share is one slice of a categorical breakdown, shaped for the existing
// PieChart component.
type Share struct {
	Name  string  `json:"name"`
	Value float64 `json:"value"`
	Label string  `json:"label"`
}

// Charts is everything the chart tabs need, from one call.
//
// Assembled server-side rather than leaving the panel to make four requests per
// poll: the panel polls every 15s, and four round trips per tab would quadruple
// that for no benefit.
type Charts struct {
	// Granularity is "hour" or "day", chosen from the window. Reported so the
	// UI can label the axis honestly rather than guessing.
	Granularity string  `json:"granularity"`
	CostSeries  []Point `json:"cost_series"`
	ObsSeries   []Point `json:"obs_series"`
	CostByModel []Share `json:"cost_by_model"`
	ObsByType   []Share `json:"obs_by_type"`
	// CostUnderstated is always true for this source and is not a bug: the
	// Claude Code capture is a Stop hook that misses delegated subagent work,
	// so these figures are lower than OpenSearch's for the same sessions
	// (measured: $318.82 against $515.78 on one session). The UI must say so;
	// a cost chart the reader has to mentally correct is worse than none.
	CostUnderstated bool `json:"cost_understated"`
}

// metricsSeriesRow is one bucket of a timeDimension query. Values are POINTERS
// because the API returns null for an empty bucket rather than 0, and a
// non-pointer would silently read those as zero-cost rather than no-data.
type metricsSeriesRow struct {
	TimeDimension string   `json:"time_dimension"`
	SumTotalCost  *float64 `json:"sum_totalCost"`
	CountCount    *float64 `json:"count_count"`
}

// metricsShareRow is one row of a categorical breakdown. Every dimension field
// is a pointer: the API returns null for observations with no value, which is
// common for providedModelName on tool and span rows.
type metricsShareRow struct {
	ProvidedModelName *string  `json:"providedModelName"`
	Type              *string  `json:"type"`
	SumTotalCost      *float64 `json:"sum_totalCost"`
	CountCount        *float64 `json:"count_count"`
}

// granularityFor picks the time bucket from the window.
//
// The API accepts auto, minute, hour, day, week and month -- it names the set
// in its own validation error. `auto` works, but it decides the bucket without
// telling us which, and the chart axis label has to state it, so the mapping is
// explicit here instead:
//
//	<= 60m  minute  (15 to 60 points)
//	<= 72h  hour    (24 to 72 points)
//	else    day
//
// Hour is kept up to 3 days deliberately: daily buckets over 3 days is three
// points, which is a table pretending to be a chart. The ceiling in the other
// direction is the same judgement -- an hourly series over a week is 168 points
// in a few hundred pixels, which reads as noise.
func granularityFor(from, to time.Time) string {
	switch d := to.Sub(from); {
	case d <= 61*time.Minute:
		return "minute"
	case d <= 73*time.Hour:
		return "hour"
	default:
		return "day"
	}
}

// parseBucket reads the two shapes the API returns for a bucket label: a bare
// date at day granularity ("2026-10-11") and an RFC3339 instant at hour
// granularity ("2026-10-11T00:00:00Z"). Both observed live.
func parseBucket(s string) (time.Time, bool) {
	for _, layout := range []string{time.RFC3339, "2006-01-02T15:04:05Z", "2006-01-02"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.UTC(), true
		}
	}
	return time.Time{}, false
}

func (c *Client) metrics(ctx context.Context, query map[string]any, out any) error {
	body, err := json.Marshal(query)
	if err != nil {
		return fmt.Errorf("metrics query: %w", err)
	}
	q := url.Values{}
	q.Set("query", string(body))
	return c.get(ctx, "/api/public/v2/metrics", q, out)
}

func envFilter(environment string) []any {
	if environment == "" {
		return nil
	}
	return []any{map[string]any{
		"column": "environment", "operator": "=", "value": environment, "type": "string",
	}}
}

// GetCharts returns the chart data for one environment and window.
//
// Each of the four aggregations is independent and a failure in one does not
// lose the others: a chart tab with three of four panels beats an error page.
// The error is returned only when EVERY aggregation failed, which means the
// endpoint or the credentials are wrong rather than one query being unlucky.
func (c *Client) GetCharts(ctx context.Context, environment, since string) (*Charts, error) {
	now := time.Now().UTC()
	from := sinceTime(since, now)
	gran := granularityFor(from, now)

	window := map[string]any{
		"fromTimestamp": from.Format(time.RFC3339),
		"toTimestamp":   now.Add(time.Minute).Format(time.RFC3339),
	}
	filters := envFilter(environment)

	out := &Charts{
		Granularity: gran,
		// Never nil: these marshal to JSON null and the panel iterates them.
		// A nil slice here threw during render once already.
		CostSeries:      []Point{},
		ObsSeries:       []Point{},
		CostByModel:     []Share{},
		ObsByType:       []Share{},
		CostUnderstated: true,
	}

	series := func(measure, agg string) ([]Point, error) {
		q := map[string]any{
			"view":          "observations",
			"dimensions":    []any{},
			"metrics":       []any{map[string]any{"measure": measure, "aggregation": agg}},
			"timeDimension": map[string]any{"granularity": gran},
		}
		for k, v := range window {
			q[k] = v
		}
		if filters != nil {
			q["filters"] = filters
		}
		var resp struct {
			Data []metricsSeriesRow `json:"data"`
		}
		if err := c.metrics(ctx, q, &resp); err != nil {
			return nil, err
		}
		pts := make([]Point, 0, len(resp.Data))
		for _, r := range resp.Data {
			t, ok := parseBucket(r.TimeDimension)
			if !ok {
				continue
			}
			// An empty bucket comes back null. Plot it as zero -- the point
			// exists in time and dropping it would make the line skip the gap,
			// which reads as "no data here" rather than "nothing happened".
			var v float64
			if r.SumTotalCost != nil {
				v = *r.SumTotalCost
			} else if r.CountCount != nil {
				v = *r.CountCount
			}
			pts = append(pts, Point{TS: t.Unix(), Value: v})
		}
		sort.Slice(pts, func(i, j int) bool { return pts[i].TS < pts[j].TS })
		return pts, nil
	}

	breakdown := func(dimension, measure, agg string) ([]Share, error) {
		q := map[string]any{
			"view":       "observations",
			"dimensions": []any{map[string]any{"field": dimension}},
			"metrics":    []any{map[string]any{"measure": measure, "aggregation": agg}},
			// High-cardinality dimensions require both of these; harmless on
			// low-cardinality ones, so applied uniformly.
			"orderBy": []any{map[string]any{
				"field": agg + "_" + measure, "direction": "desc"}},
			"config": map[string]any{"row_limit": 12},
		}
		for k, v := range window {
			q[k] = v
		}
		if filters != nil {
			q["filters"] = filters
		}
		var resp struct {
			Data []metricsShareRow `json:"data"`
		}
		if err := c.metrics(ctx, q, &resp); err != nil {
			return nil, err
		}
		shares := make([]Share, 0, len(resp.Data))
		for _, r := range resp.Data {
			name := ""
			switch dimension {
			case "providedModelName":
				if r.ProvidedModelName != nil {
					name = *r.ProvidedModelName
				}
			case "type":
				if r.Type != nil {
					name = *r.Type
				}
			}
			var v float64
			if r.SumTotalCost != nil {
				v = *r.SumTotalCost
			} else if r.CountCount != nil {
				v = *r.CountCount
			}
			// Drop empty slices. A zero-value wedge renders as a label with no
			// area, and a null dimension is "not applicable" (tool and span
			// rows carry no model), not a category worth charting.
			if v <= 0 || name == "" {
				continue
			}
			shares = append(shares, Share{Name: name, Value: v})
		}
		sort.Slice(shares, func(i, j int) bool { return shares[i].Value > shares[j].Value })
		return shares, nil
	}

	var failures, attempts int
	try := func(err error) {
		attempts++
		if err != nil {
			failures++
		}
	}

	if pts, err := series("totalCost", "sum"); err == nil {
		out.CostSeries = pts
		try(nil)
	} else {
		try(err)
	}
	if pts, err := series("count", "count"); err == nil {
		out.ObsSeries = pts
		try(nil)
	} else {
		try(err)
	}
	if sh, err := breakdown("providedModelName", "totalCost", "sum"); err == nil {
		out.CostByModel = sh
		try(nil)
	} else {
		try(err)
	}
	if sh, err := breakdown("type", "count", "count"); err == nil {
		out.ObsByType = sh
		try(nil)
	} else {
		try(err)
	}

	if failures == attempts {
		return nil, fmt.Errorf("langfuse metrics: all %d aggregations failed", attempts)
	}
	return out, nil
}

// ProjectID returns the project these credentials address, for building deep
// links into the Langfuse UI. Keys are project-scoped, so there is exactly one.
func (c *Client) ProjectID(ctx context.Context) (string, error) {
	var resp struct {
		Data []struct {
			ID   string `json:"id"`
			Name string `json:"name"`
		} `json:"data"`
	}
	if err := c.get(ctx, "/api/public/projects", nil, &resp); err != nil {
		return "", err
	}
	if len(resp.Data) == 0 {
		return "", fmt.Errorf("langfuse: no project for these credentials")
	}
	return resp.Data[0].ID, nil
}

// UILink builds a link into the Langfuse UI for this project.
//
// path is a bare segment ("sessions", "traces", "dashboards"). Verified to
// resolve on this deployment; "metrics" does NOT exist and is not offered.
func (c *Client) UILink(projectID, path string) string {
	if projectID == "" {
		return ""
	}
	return fmt.Sprintf("%s/project/%s/%s", c.baseURL, projectID, strings.TrimPrefix(path, "/"))
}

// Freshness reports the newest observation in the project, regardless of any
// window.
//
// Exists because a SHORT range is routinely empty here and that looks like a
// broken panel rather than a property of the capture: the Claude Code hook
// fires on Stop, once per COMPLETED turn, so nothing is written during a long
// turn. Measured while adding the short ranges -- the newest observation was
// 148 minutes old while the session was actively working. A panel that can say
// that is telling the truth; one that just shows an empty chart is not.
//
// Returns the zero time when the project has no observations at all.
func (c *Client) Freshness(ctx context.Context) (time.Time, error) {
	q := url.Values{}
	q.Set("limit", "1")
	// `basic` carries startTime; `core` alone omits the fields we need.
	q.Set("fields", "core,basic")

	var resp obsResponse
	if err := c.get(ctx, "/api/public/v2/observations", q, &resp); err != nil {
		return time.Time{}, err
	}
	if len(resp.Data) == 0 {
		return time.Time{}, nil
	}
	t, err := time.Parse(time.RFC3339, resp.Data[0].StartTime)
	if err != nil {
		return time.Time{}, nil
	}
	return t.UTC(), nil
}
