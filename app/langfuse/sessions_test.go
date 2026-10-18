package langfuse

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// capturedReqs records the query string per path, because ListAgentSessions
// issues two requests to different routes. A single captured value would hold
// whichever landed last, so an assertion about the observations query would
// really be inspecting the metrics query.
type capturedReqs struct {
	mu     sync.Mutex
	byPath map[string]string
	auth   string
}

func (c *capturedReqs) record(path, raw, auth string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.byPath[path] = raw
	c.auth = auth
}

func (c *capturedReqs) get(path string) string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.byPath[path]
}

const obsPath = "/api/public/v2/observations"
const metricsPath = "/api/public/v2/metrics"

// serve stands up a fake Langfuse that returns obsBody for the observations
// route and metricsBody for the metrics route.
func serve(t *testing.T, obsBody, metricsBody string) (*Client, *capturedReqs) {
	t.Helper()
	caps := &capturedReqs{byPath: map[string]string{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		caps.record(r.URL.Path, r.URL.RawQuery, r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case metricsPath:
			if metricsBody == "" {
				w.WriteHeader(http.StatusBadRequest)
				_, _ = io.WriteString(w, `{"message":"nope"}`)
				return
			}
			_, _ = io.WriteString(w, metricsBody)
		default:
			_, _ = io.WriteString(w, obsBody)
		}
	}))
	t.Cleanup(srv.Close)
	return NewClient(srv.URL, "pk-test", "sk-test"), caps
}

func TestNewClientRequiresEveryCredential(t *testing.T) {
	for _, tc := range []struct{ base, pub, sec string }{
		{"", "pk", "sk"},
		{"https://x", "", "sk"},
		{"https://x", "pk", ""},
		{"   ", "pk", "sk"},
	} {
		if c := NewClient(tc.base, tc.pub, tc.sec); c != nil {
			t.Errorf("NewClient(%q,%q,%q) = non-nil; incomplete config must read as "+
				"'not configured', not as a client that 401s on every call",
				tc.base, tc.pub, tc.sec)
		}
	}
	if NewClient("https://x/", "pk", "sk") == nil {
		t.Error("a complete config must build a client")
	}
}

func TestSendsBasicAuth(t *testing.T) {
	c, caps := serve(t, `{"data":[]}`, `{"data":[]}`)
	if _, err := c.ListAgentSessions(context.Background(), "", "now-24h", 10); err != nil {
		t.Fatalf("ListAgentSessions: %v", err)
	}
	want := "Basic " + base64.StdEncoding.EncodeToString([]byte("pk-test:sk-test"))
	if caps.auth != want {
		t.Errorf("Authorization = %q, want %q", caps.auth, want)
	}
}

const twoSessions = `{"data":[
 {"id":"o5","name":"Claude Generation 2","type":"GENERATION","startTime":"2026-10-17T12:00:50Z",
  "sessionId":"sess-new","environment":"personal","model":"claude-opus-5"},
 {"id":"o4","name":"Tool: Bash","type":"TOOL","startTime":"2026-10-17T12:00:40Z",
  "sessionId":"sess-new","environment":"personal"},
 {"id":"o3","name":"router-span","type":"SPAN","startTime":"2026-10-17T12:00:30Z",
  "sessionId":null,"environment":"personal"},
 {"id":"o2","name":"Claude Generation 1","type":"GENERATION","startTime":"2026-10-17T11:00:00Z",
  "sessionId":"sess-old","environment":"personal","model":"claude-sonnet-5"},
 {"id":"o1","name":"Tool: Read","type":"TOOL","startTime":"2026-10-17T10:00:00Z",
  "sessionId":"sess-old","environment":"personal"}
]}`

const twoAggregates = `{"data":[
 {"sessionId":"sess-new","count_count":900,"sum_totalCost":12.5,
  "sum_totalTokens":1000000,"sum_latency":45000},
 {"sessionId":"sess-old","count_count":12,"sum_totalCost":0.25,
  "sum_totalTokens":5000,"sum_latency":900}
]}`

func TestListPreservesRecencyOrder(t *testing.T) {
	c, _ := serve(t, twoSessions, twoAggregates)
	got, err := c.ListAgentSessions(context.Background(), "", "now-24h", 10)
	if err != nil {
		t.Fatalf("ListAgentSessions: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d sessions, want 2", len(got))
	}
	// The API returns newest-first; first sighting must set the order. The
	// metrics call cannot provide this -- it rejects ordering by startTime.
	if got[0].SessionID != "sess-new" || got[1].SessionID != "sess-old" {
		t.Errorf("order = %q,%q; want sess-new first (most recent)",
			got[0].SessionID, got[1].SessionID)
	}
}

func TestListMergesAggregatesAndSpans(t *testing.T) {
	c, _ := serve(t, twoSessions, twoAggregates)
	got, _ := c.ListAgentSessions(context.Background(), "", "now-24h", 10)

	s := got[0]
	// The metrics count covers the whole window and must win over the
	// page-derived count (2 rows on this page vs 900 in the window).
	if s.Observations != 900 {
		t.Errorf("Observations = %d, want 900 (from metrics, not the page)", s.Observations)
	}
	if s.CostUSD != 12.5 || s.Tokens != 1000000 || s.LatencyMS != 45000 {
		t.Errorf("aggregates not merged: cost=%v tokens=%d latency=%d",
			s.CostUSD, s.Tokens, s.LatencyMS)
	}
	// Per-type counts come from the page and are still useful.
	if s.Generations != 1 || s.ToolCalls != 1 {
		t.Errorf("gens=%d tools=%d, want 1/1", s.Generations, s.ToolCalls)
	}
	if s.Workspace != "personal" {
		t.Errorf("Workspace = %q, want personal (Langfuse environment)", s.Workspace)
	}
	// Bounds come from the page's own timestamps.
	if s.FirstSeen != "2026-10-17T12:00:40Z" || s.LastSeen != "2026-10-17T12:00:50Z" {
		t.Errorf("bounds = %q..%q", s.FirstSeen, s.LastSeen)
	}
	if len(s.Models) != 1 || s.Models[0] != "claude-opus-5" {
		t.Errorf("Models = %v", s.Models)
	}
}

// The router's own spans have no session. Grouping them under one synthetic row
// would invent a session that does not exist.
func TestListSkipsObservationsWithNoSession(t *testing.T) {
	c, _ := serve(t, twoSessions, twoAggregates)
	got, _ := c.ListAgentSessions(context.Background(), "", "now-24h", 10)
	for _, s := range got {
		if s.SessionID == "" {
			t.Error("a session with an empty id was emitted")
		}
	}
	if len(got) != 2 {
		t.Errorf("got %d sessions; the null-session observation must not create one", len(got))
	}
}

// The aggregates are an overlay on a different route. A list that loads without
// cost beats one that does not load.
func TestListSurvivesMetricsFailure(t *testing.T) {
	c, _ := serve(t, twoSessions, "") // "" makes the metrics route 400
	got, err := c.ListAgentSessions(context.Background(), "", "now-24h", 10)
	if err != nil {
		t.Fatalf("a failing metrics query must not fail the list: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d sessions, want 2", len(got))
	}
	if got[0].CostUSD != 0 {
		t.Errorf("CostUSD = %v, want 0 when the aggregate call failed", got[0].CostUSD)
	}
	// Falls back to the page-derived count rather than reporting nothing.
	if got[0].Observations != 2 {
		t.Errorf("Observations = %d, want 2 from the page", got[0].Observations)
	}
}

func TestListScopesByEnvironment(t *testing.T) {
	c, caps := serve(t, twoSessions, twoAggregates)
	_, _ = c.ListAgentSessions(context.Background(), "personal", "now-24h", 10)
	if q := caps.get(obsPath); !strings.Contains(q, "environment=personal") {
		t.Errorf("observations query missing the environment filter: %s", q)
	}
	if q := caps.get(metricsPath); !strings.Contains(q, "environment") {
		t.Errorf("metrics query missing the environment filter: %s", q)
	}

	// Unfiltered must not send an empty filter, which would match nothing.
	c2, caps2 := serve(t, twoSessions, twoAggregates)
	_, _ = c2.ListAgentSessions(context.Background(), "", "now-24h", 10)
	if q := caps2.get(obsPath); strings.Contains(q, "environment=") {
		t.Errorf("unfiltered query sent an environment filter: %s", q)
	}
}

// sessionId is high-cardinality, so the API REJECTS the query without both of
// these. Asserted on the wire because the failure is a 400, not a bad number.
func TestMetricsQueryCarriesRowLimitAndMeasureOrdering(t *testing.T) {
	c, caps := serve(t, twoSessions, twoAggregates)
	_, _ = c.ListAgentSessions(context.Background(), "", "now-24h", 10)
	q := caps.get(metricsPath)
	for _, want := range []string{"row_limit", "orderBy", "count_count", "desc"} {
		if !strings.Contains(q, want) {
			t.Errorf("metrics query missing %q: %s", want, q)
		}
	}
}

func TestListClampsLimit(t *testing.T) {
	for _, in := range []int{0, -5, 9999} {
		c, _ := serve(t, twoSessions, twoAggregates)
		if _, err := c.ListAgentSessions(context.Background(), "", "now-24h", in); err != nil {
			t.Fatalf("limit %d: %v", in, err)
		}
	}
	// A limit smaller than the session count must truncate.
	c, _ := serve(t, twoSessions, twoAggregates)
	got, _ := c.ListAgentSessions(context.Background(), "", "now-24h", 1)
	if len(got) != 1 {
		t.Errorf("limit 1 returned %d sessions", len(got))
	}
}

func TestSinceTimeParsesDateMath(t *testing.T) {
	now := time.Date(2026, 10, 17, 12, 0, 0, 0, time.UTC)
	cases := map[string]time.Duration{
		"now-24h": 24 * time.Hour,
		"now-7d":  7 * 24 * time.Hour,
		"now-30m": 30 * time.Minute,
		"now-45s": 45 * time.Second,
		// Unparseable input falls back to 24h: a list covering the wrong
		// window is recoverable, one that refuses to load is not.
		"":           24 * time.Hour,
		"garbage":    24 * time.Hour,
		"now-0h":     24 * time.Hour,
		"2026-01-01": 24 * time.Hour,
	}
	for in, want := range cases {
		if got := now.Sub(sinceTime(in, now)); got != want {
			t.Errorf("sinceTime(%q) looked back %v, want %v", in, got, want)
		}
	}
}

// --- timeline ---------------------------------------------------------------

const treeBody = `{"data":[
 {"id":"c2","name":"Tool: Bash","type":"TOOL","startTime":"2026-10-17T12:00:30Z",
  "parentObservationId":"g1","environment":"personal","latency":120.5},
 {"id":"g1","name":"Claude Generation 1","type":"GENERATION","startTime":"2026-10-17T12:00:20Z",
  "parentObservationId":"r0","environment":"personal","model":"claude-opus-5","totalPrice":0.42,
  "input":{"role":"user","content":"hello"},"output":"hi there"},
 {"id":"r0","name":"claude_code.interaction","type":"SPAN","startTime":"2026-10-17T12:00:10Z",
  "parentObservationId":null,"environment":"personal"},
 {"id":"orphan","name":"Tool: Read","type":"TOOL","startTime":"2026-10-17T12:00:40Z",
  "parentObservationId":"missing-from-this-page","environment":"personal"}
]}`

func TestTimelineBuildsTreeInForwardOrder(t *testing.T) {
	c, _ := serve(t, treeBody, `{"data":[]}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-1", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	// The API returns newest-first; a timeline reads forwards.
	var ids []string
	for _, o := range tl.Observations {
		ids = append(ids, o.ID)
	}
	want := []string{"r0", "g1", "c2", "orphan"}
	for i := range want {
		if ids[i] != want[i] {
			t.Fatalf("order = %v, want %v", ids, want)
		}
	}

	depth := map[string]int{}
	for _, o := range tl.Observations {
		depth[o.ID] = o.Depth
	}
	if depth["r0"] != 0 || depth["g1"] != 1 || depth["c2"] != 2 {
		t.Errorf("depths r0=%d g1=%d c2=%d, want 0/1/2",
			depth["r0"], depth["g1"], depth["c2"])
	}
	// A parent outside the page is treated as a root, so a truncated fetch
	// cannot produce runaway indentation.
	if depth["orphan"] != 0 {
		t.Errorf("orphan depth = %d, want 0 (parent not in this page)", depth["orphan"])
	}
}

func TestTimelineExposesContent(t *testing.T) {
	c, _ := serve(t, treeBody, `{"data":[]}`)
	tl, _ := c.GetSessionTimeline(context.Background(), "sess-1", 100)
	if !tl.ContentAvailable {
		t.Error("ContentAvailable = false, but an observation carried input/output")
	}
	var gen *Observation
	for i := range tl.Observations {
		if tl.Observations[i].ID == "g1" {
			gen = &tl.Observations[i]
		}
	}
	if gen == nil {
		t.Fatal("generation not found")
	}
	// A structured input is rendered as JSON; a string output stays a string.
	if !strings.Contains(gen.Input, "hello") {
		t.Errorf("Input = %q, want it to carry the prompt", gen.Input)
	}
	if gen.Output != "hi there" {
		t.Errorf("Output = %q, want the unquoted string", gen.Output)
	}
	if gen.Model != "claude-opus-5" || gen.CostUSD != 0.42 {
		t.Errorf("model/cost not mapped: %q %v", gen.Model, gen.CostUSD)
	}
}

// Native OTLP spans carry neither input nor output (verified against the live
// deployment), so a span-only session has structure but no conversation. The
// panel must be able to say so rather than render an empty transcript.
func TestTimelineReportsAbsentContent(t *testing.T) {
	const spansOnly = `{"data":[
	 {"id":"s1","name":"claude_code.llm_request","type":"GENERATION",
	  "startTime":"2026-10-17T12:00:00Z","input":null,"output":null,"environment":"personal"}]}`
	c, _ := serve(t, spansOnly, `{"data":[]}`)
	tl, _ := c.GetSessionTimeline(context.Background(), "sess-1", 100)
	if tl.ContentAvailable {
		t.Error("ContentAvailable = true with null input and output")
	}
	if len(tl.Observations) != 1 {
		t.Fatalf("got %d observations", len(tl.Observations))
	}
}

// `io` is a field GROUP. Passing "input,output" returns neither, which once led
// a reviewer to conclude content was unavailable in this deployment.
func TestTimelineRequestsTheIoFieldGroup(t *testing.T) {
	c, caps := serve(t, treeBody, `{"data":[]}`)
	_, _ = c.GetSessionTimeline(context.Background(), "sess-1", 100)
	q := caps.get(obsPath)
	if !strings.Contains(q, "fields=core%2Cbasic%2Cio") && !strings.Contains(q, "fields=core,basic,io") {
		t.Errorf("timeline did not request the io field group: %s", q)
	}
	if !strings.Contains(q, "sessionId=sess-1") {
		t.Errorf("timeline did not scope to the session: %s", q)
	}
}

func TestTimelineFlagsTruncation(t *testing.T) {
	c, _ := serve(t, treeBody, `{"data":[]}`)
	// 4 rows returned with limit 4 means there may be more.
	tl, _ := c.GetSessionTimeline(context.Background(), "sess-1", 4)
	if !tl.Truncated {
		t.Error("Truncated = false when the page filled the limit")
	}
	tl2, _ := c.GetSessionTimeline(context.Background(), "sess-1", 100)
	if tl2.Truncated {
		t.Error("Truncated = true when the page was short")
	}
}

func TestTimelineRequiresASessionID(t *testing.T) {
	c, _ := serve(t, treeBody, `{"data":[]}`)
	if _, err := c.GetSessionTimeline(context.Background(), "", 100); err == nil {
		t.Error("an empty session id must be refused, not fetched")
	}
}

func TestTimelineNeverReturnsNilSlice(t *testing.T) {
	c, _ := serve(t, `{"data":[]}`, `{"data":[]}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-1", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if tl.Observations == nil {
		t.Error("Observations is nil; the frontend iterates it")
	}
}

// A parent cycle must not hang the depth walk.
func TestTimelineSurvivesAParentCycle(t *testing.T) {
	const cyclic = `{"data":[
	 {"id":"a","name":"A","type":"SPAN","startTime":"2026-10-17T12:00:00Z","parentObservationId":"b"},
	 {"id":"b","name":"B","type":"SPAN","startTime":"2026-10-17T12:00:01Z","parentObservationId":"a"}]}`
	c, _ := serve(t, cyclic, `{"data":[]}`)
	done := make(chan struct{})
	go func() {
		defer close(done)
		if _, err := c.GetSessionTimeline(context.Background(), "s", 100); err != nil {
			t.Errorf("GetSessionTimeline: %v", err)
		}
	}()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("depth walk did not terminate on a cyclic parent chain")
	}
}

// The events_only 404 must say what it means: the fix is a different route,
// not a retry.
func TestEventsOnlyModeGivesASpecificError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"message":"This endpoint is not available on deployments running in Langfuse v4 events_only mode."}`)
	}))
	t.Cleanup(srv.Close)
	_, err := NewClient(srv.URL, "pk", "sk").ListAgentSessions(context.Background(), "", "now-24h", 10)
	if err == nil {
		t.Fatal("expected an error")
	}
	if !strings.Contains(err.Error(), "events_only") {
		t.Errorf("error = %v, want it to name events_only mode", err)
	}
}

// `sessionId` lives in the `basic` field group. Requesting `core` alone returns
// rows with no sessionId key at all -- verified live, 50 of 50 -- so every row
// is unattributable and the list silently comes back EMPTY. That is exactly
// what happened on the first live run of this package.
func TestListRequestsTheBasicFieldGroup(t *testing.T) {
	c, caps := serve(t, twoSessions, twoAggregates)
	if _, err := c.ListAgentSessions(context.Background(), "", "now-24h", 10); err != nil {
		t.Fatalf("ListAgentSessions: %v", err)
	}
	q := caps.get(obsPath)
	if !strings.Contains(q, "fields=core%2Cbasic") && !strings.Contains(q, "fields=core,basic") {
		t.Errorf("list must request the basic field group or sessionId is absent: %s", q)
	}
}

// Every slice the frontend iterates or measures must be non-nil. Go marshals a
// nil slice as JSON `null`, and the panel reads `s.models.length`, so a nil
// here threw "null is not an object" DURING RENDER -- killing the component and
// pinning the panel on a loading spinner while this method returned rows
// correctly. The failure was in the UI; the cause was in this marshalling.
func TestListNeverReturnsNilSlices(t *testing.T) {
	// A session with no model on any observation: the exact shape that was nil.
	const noModels = `{"data":[
	 {"id":"o1","name":"claude_code.tool","type":"SPAN","startTime":"2026-10-17T12:00:00Z",
	  "sessionId":"s1","environment":"personal"}]}`
	c, _ := serve(t, noModels, `{"data":[]}`)
	got, err := c.ListAgentSessions(context.Background(), "", "now-24h", 10)
	if err != nil {
		t.Fatalf("ListAgentSessions: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d sessions, want 1", len(got))
	}
	if got[0].Models == nil {
		t.Error("Models is nil; it marshals as JSON null and the frontend does " +
			".length on it, which throws during render")
	}
	if len(got[0].Models) != 0 {
		t.Errorf("Models = %v, want empty", got[0].Models)
	}

	// And the JSON must actually say [] rather than null.
	blob, err := json.Marshal(got[0])
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(blob), `"models":null`) {
		t.Errorf("marshalled as models:null — %s", blob)
	}
	if !strings.Contains(string(blob), `"models":[]`) {
		t.Errorf("expected models:[] in %s", blob)
	}
}
