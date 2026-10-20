package main

import (
	"os"
	"testing"
	"time"
)

// Live exercise of the chart tabs' App method. AGENTWORK_LIVE=1 to run.
func TestLiveGetAgentWorkCharts(t *testing.T) {
	if os.Getenv("AGENTWORK_LIVE") == "" {
		t.Skip("set AGENTWORK_LIVE=1 to run")
	}
	root := os.Getenv("WORKSPACES_ROOT")
	if root == "" {
		root = "/Users/cdowning/workspaces/profiles"
	}
	a := &App{config: &Config{WorkspacesRoot: root}}
	for _, since := range []string{"now-24h", "now-7d"} {
		start := time.Now()
		c, err := a.GetAgentWorkCharts("personal", since)
		if err != nil {
			t.Fatalf("%s: %v", since, err)
		}
		var cost float64
		for _, p := range c.CostSeries {
			cost += p.Value
		}
		t.Logf("%s granularity=%s in %v: cost pts=%d ($%.2f) models=%d sources=%d",
			since, c.Granularity, time.Since(start).Round(time.Millisecond),
			len(c.CostSeries), cost, len(c.CostByModel), len(c.CostBySource))
		if len(c.CostSeries) == 0 {
			t.Error("no cost series; the charts read metrics-otel and a live window should have points")
		}
		// The source split is the point of moving these charts off Langfuse: a
		// non-trivial window always has at least a "main" bucket, and the
		// subagent/auxiliary buckets are the spend Langfuse never saw.
		if len(c.CostBySource) == 0 {
			t.Error("no cost-by-source buckets; the breakdown is the reason this reads OpenSearch")
		}
	}
}

// Every range the panel offers, end to end. AGENTWORK_LIVE=1 to run.
func TestLiveAllRanges(t *testing.T) {
	if os.Getenv("AGENTWORK_LIVE") == "" {
		t.Skip("set AGENTWORK_LIVE=1 to run")
	}
	root := os.Getenv("WORKSPACES_ROOT")
	if root == "" {
		root = "/Users/cdowning/workspaces/profiles"
	}
	a := &App{config: &Config{WorkspacesRoot: root}}

	for _, since := range []string{"now-15m", "now-30m", "now-60m", "now-24h", "now-3d"} {
		c, err := a.GetAgentWorkCharts("personal", since)
		if err != nil {
			t.Fatalf("%s charts: %v", since, err)
		}
		var cost float64
		for _, p := range c.CostSeries {
			cost += p.Value
		}
		t.Logf("%-8s granularity=%-6s points=%-3d cost=$%.2f",
			since, c.Granularity, len(c.CostSeries), cost)
		// A chart with fewer than two points is a number, not a trend. Empty is
		// fine (nothing happened); one point means the bucket is too coarse.
		if len(c.CostSeries) == 1 {
			t.Errorf("%s produced a single-point series at %q granularity",
				since, c.Granularity)
		}
	}
}
