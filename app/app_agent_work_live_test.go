package main

import (
	"os"
	"testing"
	"time"
)

// Exercises the REAL App method against the live stores, including the
// credential read from disk and the OpenSearch liveness overlay. Skipped unless
// AGENTWORK_LIVE is set, so it never runs in a normal suite.
//
// Exists because two rounds of reasoning about why the panel hung were wrong.
// This reproduces the actual call path in a process, with a hard deadline, so a
// hang is a located failure instead of a spinner.
func TestLiveListAgentWork(t *testing.T) {
	if os.Getenv("AGENTWORK_LIVE") == "" {
		t.Skip("set AGENTWORK_LIVE=1 to run")
	}
	root := os.Getenv("WORKSPACES_ROOT")
	if root == "" {
		root = "/Users/cdowning/workspaces/profiles"
	}
	profile := os.Getenv("AGENTWORK_PROFILE")
	if profile == "" {
		profile = "personal"
	}

	a := &App{config: &Config{WorkspacesRoot: root, BaseURL: os.Getenv("CL_BASE_URL")}}

	t.Run("strictProfileHome", func(t *testing.T) {
		if h := a.strictProfileHome(profile); h == "" {
			t.Fatalf("no home for %q under %q", profile, root)
		} else {
			t.Logf("home = %s", h)
		}
	})

	t.Run("langfuseClient", func(t *testing.T) {
		done := make(chan bool, 1)
		start := time.Now()
		go func() { done <- a.langfuseClient(profile) != nil }()
		select {
		case ok := <-done:
			t.Logf("client built=%v in %v", ok, time.Since(start).Round(time.Millisecond))
			if !ok {
				t.Fatal("client nil — credentials not resolved")
			}
		case <-time.After(10 * time.Second):
			t.Fatal("langfuseClient BLOCKED >10s — this is the hang")
		}
	})

	t.Run("environment", func(t *testing.T) {
		t.Logf("environment = %q", a.langfuseEnvironment(profile))
	})

	t.Run("ListAgentWork", func(t *testing.T) {
		type res struct {
			rows []AgentWorkRow
			err  error
		}
		ch := make(chan res, 1)
		start := time.Now()
		go func() {
			rows, err := a.ListAgentWork(profile, "now-24h", 50)
			ch <- res{rows, err}
		}()
		select {
		case r := <-ch:
			el := time.Since(start).Round(time.Millisecond)
			if r.err != nil {
				t.Fatalf("after %v: %v", el, r.err)
			}
			t.Logf("%d rows in %v", len(r.rows), el)
			for _, row := range r.rows {
				t.Logf("  %.8s obs=%-5d cost=%.2f liveness=%q available=%v",
					row.SessionID, row.Observations, row.CostUSD,
					row.Liveness, row.LivenessAvailable)
			}
		case <-time.After(30 * time.Second):
			t.Fatal("ListAgentWork BLOCKED >30s — this is the hang")
		}
	})
}

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
		t.Logf("%s granularity=%s in %v: cost pts=%d ($%.2f) obs pts=%d models=%d types=%d",
			since, c.Granularity, time.Since(start).Round(time.Millisecond),
			len(c.CostSeries), cost, len(c.ObsSeries), len(c.CostByModel), len(c.ObsByType))
		if c.DashboardURL == "" {
			t.Error("no dashboard link resolved")
		}
		if !c.CostUnderstated {
			t.Error("CostUnderstated must be true for this source")
		}
	}
}
