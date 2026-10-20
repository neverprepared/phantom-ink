package main

import (
	"context"
	"os"
	"path/filepath"
	"time"

	"phantom-ink/opensearch"
)

// livenessOverlayTimeout bounds the OpenSearch half of the list.
//
// Shorter than agentSessionQueryTimeout on purpose: the overlay is optional, so
// it should give up well before the panel's own poll interval comes round
// again. The list is already in hand by the time this starts.
const livenessOverlayTimeout = 3 * time.Second

// strictProfileHome resolves a profile's workspace home WITHOUT the ambient
// fallback, and returns "" when it cannot be established for that exact
// profile.
//
// a.profileWorkspaceHome must not be used for credentials. Its last resort is
// envLookup(profileEnv(name), "WORKSPACE_HOME"), and profileEnv starts from
// os.Environ() -- so an unknown or mistyped profile silently resolves to
// whichever workspace launched the app, and this function would then hand back
// THAT profile's credentials. Caught by a test asking for a profile that does
// not exist and getting "personal" back.
//
// Failing closed is the right direction here: the panel says "not configured"
// rather than quietly reading another profile's data.
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

// GetAgentWorkCharts returns the trend and breakdown data for the chart tabs.
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
