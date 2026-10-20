package main

import (
	"context"
	"time"

	"phantom-ink/opensearch"
)

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

// AgentWorkCharts is the chart-tab payload.
//
// Declared FLAT with its own types rather than embedding the read layer's
// structs. Wails' TypeScript generator does not follow an embedded pointer to
// another package's struct: it emitted a class referencing the other package's
// Point and Share types without defining either, and the generated models.ts
// stopped type-checking. Keeping the binding surface in this package also stops
// the read layer's types leaking into the frontend contract.
type AgentWorkCharts struct {
	Granularity string       `json:"granularity"`
	CostSeries  []ChartPoint `json:"cost_series"`
	CostByModel []ChartShare `json:"cost_by_model"`
	// CostBySource is main / subagent / auxiliary.
	CostBySource []ChartShare `json:"cost_by_source"`
}

// chartsQueryTimeout is longer than the list's: GetCharts issues four
// aggregations, and a metrics query scans a wider window than a session page.
const chartsQueryTimeout = 20 * time.Second

// GetAgentWorkCharts returns everything the Telemetry panel's costs tab shows:
// cost over time, cost by model, and cost by source. One call, one tab -- the
// trend and the breakdowns were split across two tabs over this same payload.
//
// Sourced from OpenSearch metrics. The previous reader was a Stop hook that
// never saw delegated subagent work: the spec measured 813 tool calls /
// $309.05 there against 1,786 / $416.26 in OpenSearch for the same session.
// These charts previously carried a CostUnderstated flag so the UI could label
// a figure that was wrong by roughly half; the figure is now right and the flag
// is gone.
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

	return &AgentWorkCharts{
		Granularity:  charts.Granularity,
		CostSeries:   pts(charts.CostSeries),
		CostByModel:  shares(charts.CostByModel),
		CostBySource: shares(charts.CostBySource),
	}, nil
}
