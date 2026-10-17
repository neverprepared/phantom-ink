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

	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100)
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
	// Newest-first, as the real query returns; the timeline reverses it.
	c, _ := serveJSON(t, `{"hits":{"total":{"value":2},"hits":[
	  {"_source":{"log.attributes.event@name":"assistant_response","log.attributes.event@sequence":2,
	    "log.attributes.response":"the real answer","log.attributes.response_length":"15"}},
	  {"_source":{"log.attributes.event@name":"user_prompt","log.attributes.event@sequence":1,
	    "log.attributes.prompt":"<REDACTED>","log.attributes.prompt_length":"24"}}
	]}}`)

	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100)
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
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100)
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
	// Hits arrive newest-first, as the real query asks for; the timeline must
	// present them oldest-first.
	c, _ := serveJSON(t, `{"hits":{"total":{"value":3},"hits":[
	  {"_source":{"log.attributes.event@name":"api_request","log.attributes.event@sequence":3}},
	  {"_source":{"log.attributes.event@name":"tool_result","log.attributes.event@sequence":2,
	    "log.attributes.success":"false"}},
	  {"_source":{"log.attributes.event@name":"tool_result","log.attributes.event@sequence":1,
	    "log.attributes.success":"true"}}
	]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if tl.Events[0].Sequence != 1 || tl.Events[2].Sequence != 3 {
		t.Fatalf("events not presented oldest-first: %d,%d,%d",
			tl.Events[0].Sequence, tl.Events[1].Sequence, tl.Events[2].Sequence)
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
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 1)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if !tl.Truncated {
		t.Error("a session with more events than returned must report Truncated")
	}
}

func TestGetSessionTimelineRequiresSessionID(t *testing.T) {
	c, req := serveJSON(t, `{"hits":{"total":{"value":0},"hits":[]}}`)
	if _, err := c.GetSessionTimeline(context.Background(), "", "", 100); err == nil {
		t.Fatal("an empty session id must error, not query every session")
	}
	if *req != "" {
		t.Error("an empty session id must not reach OpenSearch")
	}
}

func TestGetSessionTimelineOrdersBySequence(t *testing.T) {
	c, req := serveJSON(t, `{"hits":{"total":{"value":0},"hits":[]}}`)
	if _, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100); err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	// Timestamps collide within a millisecond; event@sequence is the only
	// stable order.
	var q map[string]any
	if err := json.Unmarshal([]byte(*req), &q); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	spec, ok := q["sort"].([]any)[0].(map[string]any)["log.attributes.event@sequence"].(map[string]any)
	if !ok {
		t.Fatal("timeline must sort by event@sequence")
	}
	// DESC so that capping at `limit` keeps the most RECENT events — an
	// ascending query on a running session returns its first hour forever.
	// The reversal back to chronological order is asserted elsewhere.
	if spec["order"] != "desc" {
		t.Errorf("sort order = %v, want desc so truncation drops the oldest", spec["order"])
	}
	// Without unmapped_type the sort fails on indices lacking the field, and
	// OpenSearch reports that as HTTP 200 with failed shards.
	if spec["unmapped_type"] != "long" {
		t.Errorf("unmapped_type = %v, want long", spec["unmapped_type"])
	}
}

// A search that failed on some shards returns 200 with fewer hits. Presenting
// that as a complete session history is the exact failure this guards.
func TestGetSessionTimelineSurfacesShardFailures(t *testing.T) {
	c, _ := serveJSON(t, `{"_shards":{"total":65,"successful":63,"failed":2,
	  "failures":[{"shard":0,"index":"logs-otel-2026.08.04",
	    "reason":{"type":"query_shard_exception","reason":"No mapping found for [log.attributes.event@sequence] in order to sort on"}}]},
	  "hits":{"total":{"value":1},"hits":[
	   {"_source":{"log.attributes.event@name":"tool_result","log.attributes.event@sequence":1}}]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if tl.Warning == "" {
		t.Fatal("a partial search must be reported, not presented as complete")
	}
	for _, want := range []string{"2 of 65", "logs-otel-2026.08.04"} {
		if !strings.Contains(tl.Warning, want) {
			t.Errorf("warning %q omits %q", tl.Warning, want)
		}
	}
}

func TestGetSessionTimelineNoWarningWhenAllShardsSucceed(t *testing.T) {
	c, _ := serveJSON(t, `{"_shards":{"total":65,"successful":65,"failed":0},
	  "hits":{"total":{"value":1},"hits":[
	   {"_source":{"log.attributes.event@name":"tool_result","log.attributes.event@sequence":1}}]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if tl.Warning != "" {
		t.Errorf("healthy search must not warn, got %q", tl.Warning)
	}
}

// subagent_completed is the only record that names a child agent. Excluding it
// is what made the session tree look unavailable.
func TestGetSessionTimelineAdmitsAndReadsSubagentCompleted(t *testing.T) {
	c, req := serveJSON(t, `{"hits":{"total":{"value":1},"hits":[
	  {"_source":{"log.attributes.event@name":"subagent_completed",
	    "log.attributes.event@sequence":2867,
	    "log.attributes.prompt@id":"turn-9",
	    "log.attributes.agent_type":"Explore",
	    "log.attributes.agent@source":"built-in",
	    "log.attributes.duration_ms":217534,
	    "log.attributes.total_tool_uses":42,
	    "log.attributes.total_tokens":103628}}]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if !strings.Contains(*req, "subagent_completed") {
		t.Error("the timeline query must admit subagent_completed")
	}
	e := tl.Events[0]
	if e.AgentType != "Explore" || e.AgentSource != "built-in" {
		t.Errorf("child identity = %q/%q", e.AgentType, e.AgentSource)
	}
	if e.ToolUses != 42 || e.TotalTokens != 103628 || e.DurationMs != 217534 {
		t.Errorf("child rollups = tools:%d tokens:%d dur:%d", e.ToolUses, e.TotalTokens, e.DurationMs)
	}
	if e.TurnID != "turn-9" {
		t.Errorf("child must attach to its parent turn, got %q", e.TurnID)
	}
}

// query_source distinguishes the session's own thread from subagent work and
// from harness overhead that still costs money.
func TestGetSessionTimelineReadsAgentAttribution(t *testing.T) {
	c, _ := serveJSON(t, `{"hits":{"total":{"value":2},"hits":[
	  {"_source":{"log.attributes.event@name":"api_request","log.attributes.event@sequence":2,
	    "log.attributes.query_source":"agent:builtin:Explore"}},
	  {"_source":{"log.attributes.event@name":"api_request","log.attributes.event@sequence":1,
	    "log.attributes.query_source":"repl_main_thread"}}]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if tl.Events[0].Agent != "repl_main_thread" {
		t.Errorf("main-thread attribution = %q", tl.Events[0].Agent)
	}
	if tl.Events[1].Agent != "agent:builtin:Explore" {
		t.Errorf("subagent attribution = %q", tl.Events[1].Agent)
	}
}

func TestGetSessionTimelineWorkspaceFilterIsConditional(t *testing.T) {
	c, req := serveJSON(t, `{"hits":{"total":{"value":0},"hits":[]}}`)
	if _, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100); err != nil {
		t.Fatalf("unscoped: %v", err)
	}
	if strings.Contains(*req, "resource.attributes.workspace.keyword") {
		t.Error("an empty workspace must not constrain the timeline")
	}
	if _, err := c.GetSessionTimeline(context.Background(), "sess-a", "lakeview", 100); err != nil {
		t.Fatalf("scoped: %v", err)
	}
	if !strings.Contains(*req, `"lakeview"`) {
		t.Error("a named workspace must constrain the timeline, so an id alone cannot cross profiles")
	}
}

func TestGetSessionTimelinePropagatesSearchError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, `{"error":"no such index"}`)
	}))
	t.Cleanup(srv.Close)
	if _, err := NewClient(srv.URL).GetSessionTimeline(context.Background(), "sess-a", "", 10); err == nil {
		t.Fatal("a 400 from OpenSearch must surface as an error, not an empty timeline")
	}
}

// A nil slice marshals to JSON `null`, which the frontend consumes as
// string[] and calls .join() on. A prompt-only session has no tool_result
// docs and so no tool buckets at all.
func TestListAgentSessionsNeverReturnsNilSlices(t *testing.T) {
	c, _ := serveJSON(t, `{"aggregations":{"sessions":{"buckets":[
	  {"key":"prompt-only","doc_count":2,
	   "last":{"value":1,"value_as_string":"2026-10-17T06:00:00.000Z"},
	   "first":{"value":0,"value_as_string":"2026-10-17T06:00:00.000Z"},
	   "turns":{"value":1},"ws":{"buckets":[]},"models":{"buckets":[]},
	   "cost":{"value":0},"tokens_in":{"value":0},"tokens_out":{"value":0},
	   "cache_in":{"value":0},"cache_new":{"value":0},
	   "calls":{"doc_count":0,"tools":{"buckets":[]},"latest":{"hits":{"hits":[]}}},
	   "fails":{"doc_count":0}}
	]}}}`)
	got, err := c.ListAgentSessions(context.Background(), "", "", 10)
	if err != nil {
		t.Fatalf("ListAgentSessions: %v", err)
	}
	if got[0].TopTools == nil {
		t.Error("TopTools is nil; it marshals to JSON null and breaks .join() in the UI")
	}
	if got[0].Models == nil {
		t.Error("Models is nil; same hazard")
	}
	// And the whole row must survive having no tool calls at all.
	if got[0].SessionID != "prompt-only" || got[0].LastTool != "" {
		t.Errorf("unexpected row: %+v", got[0])
	}
	b, err := json.Marshal(got[0])
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if strings.Contains(string(b), "null") {
		t.Errorf("row marshals a null: %s", b)
	}
}

// tool_input and tool_parameters are governed by OTEL_LOG_TOOL_DETAILS, a
// SEPARATE flag from the prompt ones that carries no prompt or completion text.
// No document in the index has these yet, so the reader must not depend on a
// guessed nesting.
func TestGetSessionTimelineReadsToolDetailsByPrefix(t *testing.T) {
	c, req := serveJSON(t, `{"hits":{"total":{"value":1},"hits":[
	  {"_source":{"log.attributes.event@name":"tool_result","log.attributes.event@sequence":1,
	    "log.attributes.tool_name":"Bash",
	    "log.attributes.tool_input":"{\"command\":\"ls -la\"}",
	    "log.attributes.tool_parameters.bash_command":"ls",
	    "log.attributes.tool_parameters.full_command":"ls -la /etc",
	    "log.attributes.tool_parameters.description":""}}]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	// The query must ask for them, or enabling the flag changes nothing here.
	if !strings.Contains(*req, "log.attributes.tool_parameters*") {
		t.Error("the query must request tool_parameters (wildcard, shape unverified)")
	}
	if !strings.Contains(*req, "log.attributes.tool_input") {
		t.Error("the query must request tool_input")
	}
	e := tl.Events[0]
	if e.ToolInput == "" {
		t.Error("tool_input not read")
	}
	if e.ToolParams["full_command"] != "ls -la /etc" {
		t.Errorf("full_command = %q — this is the field that answers 'what did it touch'", e.ToolParams["full_command"])
	}
	if _, present := e.ToolParams["description"]; present {
		t.Error("an empty parameter must be omitted, not rendered as a blank row")
	}
	if !tl.ToolDetailsAvailable {
		t.Error("ToolDetailsAvailable must be set when any event carried tool detail")
	}
	// The prompt flags are independent and are NOT implied by tool details.
	if tl.ContentAvailable {
		t.Error("tool detail must not be reported as prompt/completion content")
	}
}

func TestGetSessionTimelineToolDetailsUnavailableWhenFlagOff(t *testing.T) {
	c, _ := serveJSON(t, `{"hits":{"total":{"value":1},"hits":[
	  {"_source":{"log.attributes.event@name":"tool_result","log.attributes.event@sequence":1,
	    "log.attributes.tool_name":"Bash","log.attributes.tool_input_size_bytes":"120"}}]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 100)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	if tl.ToolDetailsAvailable {
		t.Error("a size-only tool_result must not claim tool details are available")
	}
	if tl.Events[0].ToolParams != nil {
		t.Error("no params must mean a nil map, not an empty one that renders a header")
	}
}

// The list's rollups are clipped to the selected range — the same session
// measures $144 at 6h and $273 at 30d. The drill-down must report the
// session's real totals, computed over the whole matched set rather than the
// size-capped page.
func TestGetSessionTimelineReturnsUnclippedTotals(t *testing.T) {
	c, req := serveJSON(t, `{"_shards":{"total":1,"successful":1,"failed":0},
	 "aggregations":{"turns":{"value":57},"cost":{"value":272.96},
	   "tokens_out":{"value":396639},
	   "first":{"value":0,"value_as_string":"2026-10-16T14:37:29.212Z"},
	   "last":{"value":1,"value_as_string":"2026-10-17T07:00:34.182Z"},
	   "calls":{"doc_count":737},"fails":{"doc_count":3}},
	 "hits":{"total":{"value":3541},"hits":[
	  {"_source":{"log.attributes.event@name":"tool_result","log.attributes.event@sequence":1}}]}}`)
	tl, err := c.GetSessionTimeline(context.Background(), "sess-a", "", 1)
	if err != nil {
		t.Fatalf("GetSessionTimeline: %v", err)
	}
	// No range clause may appear, or the totals inherit the clipping.
	if strings.Contains(*req, `"range"`) {
		t.Error("the timeline query must not filter by time; totals would be clipped")
	}
	if tl.Totals.Turns != 57 || tl.Totals.ToolCalls != 737 || tl.Totals.Failures != 3 {
		t.Errorf("totals = %+v", tl.Totals)
	}
	if tl.Totals.CostUSD != 272.96 {
		t.Errorf("CostUSD = %v, want the whole-life 272.96", tl.Totals.CostUSD)
	}
	if tl.Totals.FirstSeen == "" || tl.Totals.LastSeen == "" {
		t.Errorf("session window = %q..%q", tl.Totals.FirstSeen, tl.Totals.LastSeen)
	}
	// Totals must survive truncation: only 1 of 3541 events was returned.
	if !tl.Truncated {
		t.Error("expected a truncated timeline in this fixture")
	}
}
