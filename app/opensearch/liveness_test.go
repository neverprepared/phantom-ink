package opensearch

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestClassifyLivenessBoundaries(t *testing.T) {
	now := time.Date(2026, 10, 17, 12, 0, 0, 0, time.UTC)
	at := func(d time.Duration) string { return now.Add(-d).Format(time.RFC3339Nano) }

	cases := []struct {
		name  string
		ts    string
		want  string
		wantM int64
	}{
		// The boundaries are asserted on both sides. A test that only probed
		// the middle of each band would pass against a classifier using >
		// instead of >=, or seconds instead of milliseconds.
		{"just now", at(0), LivenessActive, 0},
		{"one second", at(time.Second), LivenessActive, 1000},
		{"80s observed worst-case log gap", at(80 * time.Second), LivenessActive, 80000},
		{"just inside active", at(activeWithin - time.Millisecond), LivenessActive, 119999},
		{"exactly at active threshold is idle", at(activeWithin), LivenessIdle, 120000},
		{"just inside idle", at(idleWithin - time.Millisecond), LivenessIdle, 899999},
		{"exactly at idle threshold is dormant", at(idleWithin), LivenessDormant, 900000},
		{"hours", at(4 * time.Hour), LivenessDormant, 14400000},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, ms := classifyLiveness(tc.ts, now)
			if got != tc.want {
				t.Errorf("state = %q, want %q", got, tc.want)
			}
			if ms != tc.wantM {
				t.Errorf("age = %dms, want %dms", ms, tc.wantM)
			}
		})
	}
}

// An absent timestamp is not old data. Collapsing the two is what lets a
// liveness indicator report a confident state it has no evidence for.
func TestClassifyLivenessDistinguishesAbsentFromOld(t *testing.T) {
	now := time.Date(2026, 10, 17, 12, 0, 0, 0, time.UTC)
	for _, ts := range []string{"", "not-a-timestamp", "2026-13-45T99:99:99Z"} {
		got, ms := classifyLiveness(ts, now)
		if got != LivenessUnknown {
			t.Errorf("classifyLiveness(%q) = %q, want %q", ts, got, LivenessUnknown)
		}
		if ms != 0 {
			t.Errorf("classifyLiveness(%q) age = %d, want 0", ts, ms)
		}
	}
}

// The app's clock and the ingest path's clock are independent. A timestamp a
// few seconds in the future is skew; reporting a negative age would render as
// a nonsense "active in -3s".
func TestAgeMillisClampsFutureTimestamps(t *testing.T) {
	now := time.Date(2026, 10, 17, 12, 0, 0, 0, time.UTC)
	ms, ok := ageMillis(now.Add(90*time.Second).Format(time.RFC3339Nano), now)
	if !ok {
		t.Fatal("a future timestamp is still a parseable timestamp")
	}
	if ms != 0 {
		t.Errorf("age = %d, want 0 (clamped)", ms)
	}
}

func TestAgeMillisParsesOpenSearchStrictDateTime(t *testing.T) {
	now := time.Date(2026, 10, 17, 12, 0, 30, 0, time.UTC)
	// The exact shape metrics-otel-*/logs-otel-* return in value_as_string.
	ms, ok := ageMillis("2026-10-17T12:00:00.000Z", now)
	if !ok {
		t.Fatal("failed to parse the format OpenSearch actually returns")
	}
	if ms != 30000 {
		t.Errorf("age = %d, want 30000", ms)
	}
}

// livenessFixture serves one session whose log recency and metric heartbeat are
// set independently, so the two can be made to disagree.
func livenessFixture(t *testing.T, lastSeen, lastBeat string) *Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.HasPrefix(r.URL.Path, "/metrics-otel-") {
			if lastBeat == "" {
				_, _ = io.WriteString(w, `{"aggregations":{"s":{"buckets":[]}}}`)
				return
			}
			_, _ = io.WriteString(w, `{"aggregations":{"s":{"buckets":[
			  {"key":"s1","last":{"value":1,"value_as_string":"`+lastBeat+`"}}]}}}`)
			return
		}
		_, _ = io.WriteString(w, `{"aggregations":{"sessions":{"buckets":[
		  {"key":"s1","doc_count":2,"last":{"value":1,"value_as_string":"`+lastSeen+`"},
		   "first":{"value":0,"value_as_string":"2026-10-17T11:00:00.000Z"},"turns":{"value":1},
		   "ws":{"buckets":[{"key":"personal"}]},"models":{"buckets":[]},
		   "cost":{"value":0},"tokens_in":{"value":0},"tokens_out":{"value":0},
		   "cache_in":{"value":0},"cache_new":{"value":0},
		   "calls":{"doc_count":0,"tools":{"buckets":[]},"latest":{"hits":{"hits":[]}}},
		   "fails":{"doc_count":0}}]}}}`)
	}))
	t.Cleanup(srv.Close)
	c := NewClient(srv.URL)
	c.now = func() time.Time { return time.Date(2026, 10, 17, 12, 0, 0, 0, time.UTC) }
	return c
}

// THE REGRESSION TEST for the shipped bug. The previous indicator thresholded
// the metric heartbeat at 150s. That heartbeat has p90 180s gaps and carries no
// query_source on any of 26,566 docs, so it stalls during subagent fan-out
// while the session works. Measured in exactly that state: log recency 40s,
// heartbeat 264s. The old code called this session dead; it is active.
func TestFanOutSessionIsActiveDespiteStaleHeartbeat(t *testing.T) {
	c := livenessFixture(t,
		"2026-10-17T11:59:20.000Z", // activity 40s ago
		"2026-10-17T11:55:36.000Z", // heartbeat 264s ago
	)
	got, err := c.ListAgentSessions(context.Background(), "", "now-24h", 10)
	if err != nil {
		t.Fatalf("ListAgentSessions: %v", err)
	}
	if got[0].Liveness != LivenessActive {
		t.Errorf("liveness = %q, want %q — a fan-out session is working, not dead",
			got[0].Liveness, LivenessActive)
	}
	if got[0].LastActivityMS != 40000 {
		t.Errorf("last_activity_ms = %d, want 40000", got[0].LastActivityMS)
	}
	// The disagreement must remain legible rather than being fused away: it is
	// the one thing this data can say about subagent activity.
	if got[0].LastHeartbeatMS == nil {
		t.Fatal("heartbeat age not reported")
	}
	if *got[0].LastHeartbeatMS != 264000 {
		t.Errorf("last_heartbeat_ms = %d, want 264000", *got[0].LastHeartbeatMS)
	}
}

// Liveness must not depend on the metrics index being readable, which is the
// structural reason it moved onto log recency.
func TestLivenessSurvivesMissingHeartbeat(t *testing.T) {
	c := livenessFixture(t, "2026-10-17T11:59:30.000Z", "")
	got, err := c.ListAgentSessions(context.Background(), "", "now-24h", 10)
	if err != nil {
		t.Fatalf("ListAgentSessions: %v", err)
	}
	if got[0].Liveness != LivenessActive {
		t.Errorf("liveness = %q, want %q with no heartbeat at all",
			got[0].Liveness, LivenessActive)
	}
	if got[0].LastHeartbeatMS != nil {
		t.Errorf("last_heartbeat_ms = %v, want absent", *got[0].LastHeartbeatMS)
	}
}

func TestLivenessStatesEndToEnd(t *testing.T) {
	for _, tc := range []struct {
		name, lastSeen, want string
	}{
		{"recent", "2026-10-17T11:59:00.000Z", LivenessActive},
		{"ten minutes", "2026-10-17T11:50:00.000Z", LivenessIdle},
		{"an hour", "2026-10-17T11:00:00.000Z", LivenessDormant},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := livenessFixture(t, tc.lastSeen, "")
			got, err := c.ListAgentSessions(context.Background(), "", "now-24h", 10)
			if err != nil {
				t.Fatalf("ListAgentSessions: %v", err)
			}
			if got[0].Liveness != tc.want {
				t.Errorf("liveness = %q, want %q", got[0].Liveness, tc.want)
			}
		})
	}
}
