package opensearch

import (
	"context"
	"encoding/json"
	"os"
	"testing"
)

// Live smoke test against the real platform; skipped unless OS_URL is set.
func TestLiveSmoke(t *testing.T) {
	url := os.Getenv("OS_URL")
	if url == "" {
		t.Skip("set OS_URL")
	}
	c := NewClient(url)
	ss, err := c.ListAgentSessions(context.Background(), "", "now-24h", 10)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	b, _ := json.MarshalIndent(ss, "", "  ")
	t.Logf("sessions (%d):\n%s", len(ss), b)
	if len(ss) == 0 {
		t.Skip("no sessions in window")
	}
	tl, err := c.GetSessionTimeline(context.Background(), ss[0].SessionID, "", 2000)
	if err != nil {
		t.Fatalf("timeline: %v", err)
	}
	t.Logf("timeline %s: %d of %d events, truncated=%v contentAvailable=%v ws=%q",
		tl.SessionID, len(tl.Events), tl.TotalEvents, tl.Truncated, tl.ContentAvailable, tl.Workspace)
	// A partial search presented as a complete history is the failure this
	// field exists to prevent; the live cluster really does fail shards here.
	if tl.Warning != "" {
		t.Errorf("partial search: %s", tl.Warning)
	}
	agents := map[string]int{}
	for _, e := range tl.Events {
		if e.Agent != "" {
			agents[e.Agent]++
		}
		if e.Kind == "subagent_completed" {
			t.Logf("  CHILD %s (%s) %dms tools=%d tokens=%d",
				e.AgentType, e.AgentSource, e.DurationMs, e.ToolUses, e.TotalTokens)
		}
	}
	t.Logf("agents=%v", agents)
	kinds := map[string]int{}
	zeroSeq, redacted := 0, 0
	for _, e := range tl.Events {
		kinds[e.Kind]++
		if e.Sequence == 0 {
			zeroSeq++
		}
		if e.Redacted {
			redacted++
		}
	}
	t.Logf("kinds=%v zeroSequence=%d redacted=%d", kinds, zeroSeq, redacted)
	for i, e := range tl.Events {
		if i >= 6 {
			break
		}
		t.Logf("  seq=%d %s tool=%s dur=%dms in=%d out=%d model=%s cost=%v len=%d redacted=%v",
			e.Sequence, e.Kind, e.ToolName, e.DurationMs, e.InputSize, e.ResultSize,
			e.Model, e.CostUSD, e.ContentLength, e.Redacted)
	}
	// Ordering must be non-decreasing by sequence.
	for i := 1; i < len(tl.Events); i++ {
		if tl.Events[i].Sequence < tl.Events[i-1].Sequence {
			t.Fatalf("out of order at %d: %d < %d", i, tl.Events[i].Sequence, tl.Events[i-1].Sequence)
		}
	}
}

// The list is range-scoped; the drill-down must not be. Proves the totals do
// not move with the window the list was fetched under.
func TestLiveTotalsAreUnclipped(t *testing.T) {
	url := os.Getenv("OS_URL")
	if url == "" {
		t.Skip("set OS_URL")
	}
	c := NewClient(url)
	ss, err := c.ListAgentSessions(context.Background(), "", "now-24h", 5)
	if err != nil || len(ss) == 0 {
		t.Skipf("no sessions: %v", err)
	}
	s := ss[0]
	tl, err := c.GetSessionTimeline(context.Background(), s.SessionID, "", 10)
	if err != nil {
		t.Fatalf("timeline: %v", err)
	}
	t.Logf("session %s", s.SessionID[:8])
	t.Logf("  list (24h window): turns=%d calls=%d cost=$%.2f", s.Turns, s.ToolCalls, s.CostUSD)
	t.Logf("  totals (all time): turns=%d calls=%d cost=$%.2f",
		tl.Totals.Turns, tl.Totals.ToolCalls, tl.Totals.CostUSD)
	if tl.Totals.CostUSD < s.CostUSD {
		t.Errorf("unclipped total $%.2f is BELOW the windowed figure $%.2f", tl.Totals.CostUSD, s.CostUSD)
	}
	if tl.Warning != "" {
		t.Errorf("partial search: %s", tl.Warning)
	}
}
