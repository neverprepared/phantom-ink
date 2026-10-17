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
	tl, err := c.GetSessionTimeline(context.Background(), ss[0].SessionID, 2000)
	if err != nil {
		t.Fatalf("timeline: %v", err)
	}
	t.Logf("timeline %s: %d events, truncated=%v contentAvailable=%v ws=%q",
		tl.SessionID, len(tl.Events), tl.Truncated, tl.ContentAvailable, tl.Workspace)
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
