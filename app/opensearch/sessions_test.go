package opensearch

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// serveJSON stands up a fake OpenSearch that returns body for any _search and
// records the request body it was sent, so tests can assert on the query we
// build as well as on the parsing of the response.
func serveJSON(t *testing.T, body string) (*Client, *string) {
	t.Helper()
	var captured string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		captured = string(b)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, body)
	}))
	t.Cleanup(srv.Close)
	return NewClient(srv.URL), &captured
}

func TestListAgentSessionsParsesAggregation(t *testing.T) {
	c, _ := serveJSON(t, `{"aggregations":{"sessions":{"buckets":[
	  {"key":"sess-a","doc_count":40,
	   "last":{"value":1,"value_as_string":"2026-10-17T06:55:31.000Z"},
	   "first":{"value":0,"value_as_string":"2026-10-17T05:00:00.000Z"},
	   "turns":{"value":7},
	   "ws":{"buckets":[{"key":"personal"}]},
	   "models":{"buckets":[{"key":"claude-opus-5"},{"key":"claude-haiku-4-5"}]},
	   "cost":{"value":1.25},"tokens_in":{"value":1000},"tokens_out":{"value":250},
	   "cache_in":{"value":118898},"cache_new":{"value":1169},
	   "calls":{"doc_count":22,
	     "tools":{"buckets":[{"key":"Bash"},{"key":"Read"}]},
	     "latest":{"hits":{"hits":[{"_source":{"log.attributes.tool_name":"Bash"}}]}}},
	   "fails":{"doc_count":3}}
	]}}}`)

	got, err := c.ListAgentSessions(context.Background(), "personal", "now-24h", 10)
	if err != nil {
		t.Fatalf("ListAgentSessions: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("want 1 session, got %d", len(got))
	}
	s := got[0]
	// ToolCalls must come from the tool_result-only filter bucket (22), not the
	// bucket's total doc_count (40, which includes prompts and api_requests).
	if s.ToolCalls != 22 {
		t.Errorf("ToolCalls = %d, want 22 (the tool_result filter bucket, not doc_count)", s.ToolCalls)
	}
	if s.SessionID != "sess-a" || s.Workspace != "personal" {
		t.Errorf("identity = %q/%q, want sess-a/personal", s.SessionID, s.Workspace)
	}
	if s.Turns != 7 || s.Failures != 3 {
		t.Errorf("Turns/Failures = %d/%d, want 7/3", s.Turns, s.Failures)
	}
	if s.LastSeen != "2026-10-17T06:55:31.000Z" || s.FirstSeen != "2026-10-17T05:00:00.000Z" {
		t.Errorf("window = %q..%q", s.FirstSeen, s.LastSeen)
	}
	if s.LastTool != "Bash" {
		t.Errorf("LastTool = %q, want Bash", s.LastTool)
	}
	if s.CostUSD != 1.25 || s.TokensIn != 1000 || s.TokensOut != 250 {
		t.Errorf("cost/tokens = %v/%d/%d", s.CostUSD, s.TokensIn, s.TokensOut)
	}
	// Cache counters must be reported separately: input_tokens alone is a tiny
	// fraction of what a long session actually sent.
	if s.TokensCacheIn != 118898 || s.TokensCacheNew != 1169 {
		t.Errorf("cache tokens = %d/%d, want 118898/1169", s.TokensCacheIn, s.TokensCacheNew)
	}
	if len(s.TopTools) != 2 || s.TopTools[0] != "Bash" {
		t.Errorf("TopTools = %v", s.TopTools)
	}
	if len(s.Models) != 2 {
		t.Errorf("Models = %v", s.Models)
	}
}

// Claude Code emits a tool_decision AND a tool_result for every tool call, both
// carrying tool_name. If the query ever admits tool_decision, every per-session
// tool count silently doubles.
func TestListAgentSessionsNeverAdmitsToolDecision(t *testing.T) {
	c, req := serveJSON(t, `{"aggregations":{"sessions":{"buckets":[]}}}`)
	if _, err := c.ListAgentSessions(context.Background(), "", "now-7d", 10); err != nil {
		t.Fatalf("ListAgentSessions: %v", err)
	}
	if strings.Contains(*req, "tool_decision") {
		t.Error("query admits tool_decision; every tool count would double")
	}
	for _, want := range []string{"tool_result", "user_prompt", "assistant_response", "api_request"} {
		if !strings.Contains(*req, want) {
			t.Errorf("query omits activity event %q", want)
		}
	}
	// hook_execution_* is the highest-volume pair in the index and is local
	// plumbing, not agent work.
	if strings.Contains(*req, "hook_execution") {
		t.Error("query admits hook_execution events")
	}
}

func TestListAgentSessionsWorkspaceFilterIsConditional(t *testing.T) {
	// The field name alone is not the signal: the `ws` sub-aggregation
	// references it unconditionally. What must be conditional is a `term`
	// filter in the query's `must` clause.
	mustHasWorkspaceTerm := func(t *testing.T, raw string) bool {
		t.Helper()
		var q map[string]any
		if err := json.Unmarshal([]byte(raw), &q); err != nil {
			t.Fatalf("unmarshal query: %v", err)
		}
		for _, clause := range q["query"].(map[string]any)["bool"].(map[string]any)["must"].([]any) {
			term, ok := clause.(map[string]any)["term"].(map[string]any)
			if !ok {
				continue
			}
			if _, ok := term["resource.attributes.workspace.keyword"]; ok {
				return true
			}
		}
		return false
	}

	c, req := serveJSON(t, `{"aggregations":{"sessions":{"buckets":[]}}}`)
	if _, err := c.ListAgentSessions(context.Background(), "", "", 10); err != nil {
		t.Fatalf("unfiltered: %v", err)
	}
	if mustHasWorkspaceTerm(t, *req) {
		t.Error("empty workspace must not add a workspace term (strict equality would exclude workspace-less rows)")
	}
	if _, err := c.ListAgentSessions(context.Background(), "lakeview", "", 10); err != nil {
		t.Fatalf("filtered: %v", err)
	}
	if !mustHasWorkspaceTerm(t, *req) {
		t.Error("a named workspace must constrain the query with a term filter")
	}
	if !strings.Contains(*req, `"lakeview"`) {
		t.Error("the workspace term must carry the requested value")
	}
}

func TestListAgentSessionsClampsLimit(t *testing.T) {
	for _, tc := range []struct{ in, want int }{{0, 50}, {-5, 50}, {1000, 50}, {25, 25}} {
		c, req := serveJSON(t, `{"aggregations":{"sessions":{"buckets":[]}}}`)
		if _, err := c.ListAgentSessions(context.Background(), "", "", tc.in); err != nil {
			t.Fatalf("limit %d: %v", tc.in, err)
		}
		var q map[string]any
		if err := json.Unmarshal([]byte(*req), &q); err != nil {
			t.Fatalf("unmarshal query: %v", err)
		}
		aggs := q["aggs"].(map[string]any)["sessions"].(map[string]any)
		size := aggs["terms"].(map[string]any)["size"].(float64)
		if int(size) != tc.want {
			t.Errorf("limit %d -> agg size %d, want %d", tc.in, int(size), tc.want)
		}
	}
}

// Sizes and prompt/response lengths are mapped `text` in this index while
// duration_ms and the token counts are mapped `long`. A reader that only
// accepts JSON numbers returns 0 for every size the UI displays.
func TestGetSessionTimelineReadsTextMappedNumbers(t *testing.T) {
	c, _ := serveJSON(t, `{"hits":{"total":{"value":1},"hits":[
	  {"_source":{
	    "time":"2026-10-17T06:52:31.158Z",
	    "log.attributes.event@name":"tool_result",
	    "log.attributes.event@sequence":7811,
	    "log.attributes.prompt@id":"turn-1",
	    "resource.attributes.workspace":"personal",
	    "log.attributes.tool_name":"Bash",
	    "log.attributes.tool_use_id":"toolu_1",
	    "log.attributes.success":"true",
	    "log.attributes.duration_ms":3003,
	    "log.attributes.tool_input_size_bytes":"1359",
	    "log.attributes.tool_result_size_bytes":"194"
	  }}]}}`)

	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if len(tl.Events) != 1 {
		t.Fatalf("want 1 event, got %d", len(tl.Events))
	}
	e := tl.Events[0]
	if e.InputSize != 1359 {
		t.Errorf("InputSize = %d, want 1359 (field is mapped text)", e.InputSize)
	}
	if e.ResultSize != 194 {
		t.Errorf("ResultSize = %d, want 194 (field is mapped text)", e.ResultSize)
	}
	if e.DurationMs != 3003 {
		t.Errorf("DurationMs = %d, want 3003 (field is mapped long)", e.DurationMs)
	}
	if e.Sequence != 7811 {
		t.Errorf("Sequence = %d, want 7811", e.Sequence)
	}
	if e.Success == nil || !*e.Success {
		t.Errorf("Success = %v, want true (arrives as the string \"true\")", e.Success)
	}
	if tl.Workspace != "personal" {
		t.Errorf("Workspace = %q", tl.Workspace)
	}
	if tl.Truncated {
		t.Error("Truncated set when total equals returned count")
	}
}

// A suppressed prompt is present-but-sentinel, not absent. Showing
// "<REDACTED>" as a message body would read as captured content.
func TestGetSessionTimelineDistinguishesRedactedFromCaptured(t *testing.T) {
	c, _ := serveJSON(t, `{"hits":{"total":{"value":2},"hits":[
	  {"_source":{"log.attributes.event@name":"user_prompt","log.attributes.event@sequence":1,
	    "log.attributes.prompt":"<REDACTED>","log.attributes.prompt_length":"24"}},
	  {"_source":{"log.attributes.event@name":"assistant_response","log.attributes.event@sequence":2,
	    "log.attributes.response":"the real answer","log.attributes.response_length":"15"}}
	]}}`)

	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	red, cap := tl.Events[0], tl.Events[1]
	if !red.Redacted {
		t.Error("sentinel prompt must set Redacted")
	}
	if red.Content != "" {
		t.Errorf("sentinel leaked into Content as %q", red.Content)
	}
	// The length is emitted even when the text is not, so the UI can show how
	// big a message it cannot show.
	if red.ContentLength != 24 {
		t.Errorf("redacted ContentLength = %d, want 24", red.ContentLength)
	}
	if cap.Content != "the real answer" || cap.Redacted {
		t.Errorf("captured event = %q redacted=%v", cap.Content, cap.Redacted)
	}
	if !tl.ContentAvailable {
		t.Error("ContentAvailable must be true when any event carried real text")
	}
}

func TestGetSessionTimelineContentUnavailableWhenAllRedacted(t *testing.T) {
	c, _ := serveJSON(t, `{"hits":{"total":{"value":1},"hits":[
	  {"_source":{"log.attributes.event@name":"user_prompt","log.attributes.event@sequence":1,
	    "log.attributes.prompt":"<REDACTED>","log.attributes.prompt_length":"24"}}
	]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	// Events present but no content: capture is off. That is an actionable
	// state, and distinct from an empty session.
	if tl.ContentAvailable {
		t.Error("ContentAvailable must be false when every event is redacted")
	}
	if len(tl.Events) != 1 {
		t.Fatalf("want the event retained, got %d", len(tl.Events))
	}
}

// success is absent on events that have no outcome. A zero-value bool would
// report those as failures.
func TestGetSessionTimelineSuccessIsTriState(t *testing.T) {
	c, _ := serveJSON(t, `{"hits":{"total":{"value":3},"hits":[
	  {"_source":{"log.attributes.event@name":"tool_result","log.attributes.event@sequence":1,
	    "log.attributes.success":"true"}},
	  {"_source":{"log.attributes.event@name":"tool_result","log.attributes.event@sequence":2,
	    "log.attributes.success":"false"}},
	  {"_source":{"log.attributes.event@name":"api_request","log.attributes.event@sequence":3}}
	]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if tl.Events[0].Success == nil || !*tl.Events[0].Success {
		t.Error(`"true" must decode to a set, true Success`)
	}
	if tl.Events[1].Success == nil || *tl.Events[1].Success {
		t.Error(`"false" must decode to a set, false Success`)
	}
	if tl.Events[2].Success != nil {
		t.Error("an absent success must stay nil, not read as a failure")
	}
}

func TestGetSessionTimelineReportsTruncation(t *testing.T) {
	c, _ := serveJSON(t, `{"hits":{"total":{"value":900},"hits":[
	  {"_source":{"log.attributes.event@name":"tool_result","log.attributes.event@sequence":1}}
	]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", 1)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if !tl.Truncated {
		t.Error("a session with more events than returned must report Truncated")
	}
}

func TestGetSessionTimelineRequiresSessionID(t *testing.T) {
	c, req := serveJSON(t, `{"hits":{"total":{"value":0},"hits":[]}}`)
	if _, err := c.GetSessionTimeline(context.Background(), "", 100); err == nil {
		t.Fatal("an empty session id must error, not query every session")
	}
	if *req != "" {
		t.Error("an empty session id must not reach OpenSearch")
	}
}

func TestGetSessionTimelineOrdersBySequence(t *testing.T) {
	c, req := serveJSON(t, `{"hits":{"total":{"value":0},"hits":[]}}`)
	if _, err := c.GetSessionTimeline(context.Background(), "sess-a", 100); err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	// Timestamps collide within a millisecond; event@sequence is the only
	// stable order.
	if !strings.Contains(*req, "log.attributes.event@sequence") {
		t.Error("timeline must sort by event@sequence")
	}
	var q map[string]any
	if err := json.Unmarshal([]byte(*req), &q); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	sortSpec := q["sort"].([]any)[0].(map[string]any)
	ord := sortSpec["log.attributes.event@sequence"].(map[string]any)["order"]
	if ord != "asc" {
		t.Errorf("sort order = %v, want asc (read a session forwards)", ord)
	}
}

func TestGetSessionTimelinePropagatesSearchError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, `{"error":"no such index"}`)
	}))
	t.Cleanup(srv.Close)
	if _, err := NewClient(srv.URL).GetSessionTimeline(context.Background(), "sess-a", 10); err == nil {
		t.Fatal("a 400 from OpenSearch must surface as an error, not an empty timeline")
	}
}
