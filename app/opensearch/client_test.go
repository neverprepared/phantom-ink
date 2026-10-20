package opensearch

import (
	"context"
	"regexp"
	"strings"
	"testing"
)

// Two rows as the store actually holds them since ADR-007 landed: one
// claude-code document, which carries model and session but an EMPTY
// severityText on every one of ~18k documents a day, and one phantom-inbox
// document, which carries severity, event name, outcome and a run id but
// neither model nor session.
//
// Keys are the FLATTENED dotted form Data Prepper writes. Reading
// log.attributes as a nested object is the mistake ADR-007 records under "what
// this corrects".
const twoServiceLogs = `{"hits":{"hits":[
 {"_source":{
   "time":"2026-10-19T22:31:13.790Z",
   "body":"claude_code.api_request",
   "severityText":"",
   "serviceName":"claude-code",
   "log.attributes.session@id":"189d5026-b29e",
   "log.attributes.model":"claude-opus-5",
   "log.attributes.duration_ms":2469,
   "resource.attributes.workspace":"personal"
 }},
 {"_source":{
   "time":"2026-10-19T22:31:13.000Z",
   "body":"drain: skipped 1",
   "severityText":"INFO",
   "serviceName":"phantom-inbox",
   "log.attributes.event@name":"inbox.drain.run",
   "log.attributes.event@outcome":"ok",
   "log.attributes.run@id":"01M5B4CDY0H33GBGG2ZM75TE57",
   "log.attributes.duration_ms":2005,
   "resource.attributes.workspace":"personal"
 }}
]}}`

func TestTailLogsIdentifiesTheServiceThatEmitted(t *testing.T) {
	// Without this the two rows are indistinguishable in the panel: an inbox row
	// has no model and no session, so it renders as bare time + body, exactly
	// like a claude-code row missing those tags.
	c, _ := serveJSON(t, twoServiceLogs)

	got, err := c.TailLogs(context.Background(), "personal", 10)
	if err != nil {
		t.Fatalf("TailLogs: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("want 2 entries, got %d", len(got))
	}
	if got[0].Service != "claude-code" {
		t.Errorf("entry 0 service = %q, want claude-code", got[0].Service)
	}
	if got[1].Service != "phantom-inbox" {
		t.Errorf("entry 1 service = %q, want phantom-inbox", got[1].Service)
	}
}

func TestTailLogsCarriesSeverityAndLeavesClaudeCodesBlank(t *testing.T) {
	// severityText is empty on every claude-code document. Defaulting that to
	// INFO would invent data and make a severity filter lie about the majority
	// of rows, so an unset level stays unset and the reader renders it as such.
	c, _ := serveJSON(t, twoServiceLogs)

	got, err := c.TailLogs(context.Background(), "personal", 10)
	if err != nil {
		t.Fatalf("TailLogs: %v", err)
	}
	if got[0].Severity != "" {
		t.Errorf("claude-code severity = %q, want empty (not defaulted)", got[0].Severity)
	}
	if got[1].Severity != "INFO" {
		t.Errorf("inbox severity = %q, want INFO", got[1].Severity)
	}
}

func TestTailLogsSeparatesEventNameFromBody(t *testing.T) {
	// LogEntry.Body was documented as "event name (e.g.
	// claude_code.api_request)", which is true for claude-code and false for
	// ADR-007 records, where body is a human sentence and event.name is its own
	// attribute. Conflating them loses the log group on our own records.
	c, _ := serveJSON(t, twoServiceLogs)

	got, err := c.TailLogs(context.Background(), "personal", 10)
	if err != nil {
		t.Fatalf("TailLogs: %v", err)
	}
	if got[1].Body != "drain: skipped 1" {
		t.Errorf("inbox body = %q, want the human sentence", got[1].Body)
	}
	if got[1].EventName != "inbox.drain.run" {
		t.Errorf("inbox event name = %q, want inbox.drain.run", got[1].EventName)
	}
	if got[1].Outcome != "ok" {
		t.Errorf("inbox outcome = %q, want ok", got[1].Outcome)
	}
	if got[1].RunID != "01M5B4CDY0H33GBGG2ZM75TE57" {
		t.Errorf("inbox run id = %q, want the ULID", got[1].RunID)
	}
}

func TestTailLogsRequestsTheNewFields(t *testing.T) {
	// A projected field absent from _source comes back empty with no error, so
	// the query itself has to be asserted: this is the failure mode that makes a
	// reader look broken when the data is fine.
	c, caps := serveJSON(t, twoServiceLogs)

	if _, err := c.TailLogs(context.Background(), "personal", 10); err != nil {
		t.Fatalf("TailLogs: %v", err)
	}
	body := caps.get("logs-otel-*")
	for _, field := range []string{
		"severityText",
		"serviceName",
		"log.attributes.event@name",
		"log.attributes.event@outcome",
		"log.attributes.run@id",
	} {
		if !strings.Contains(body, field) {
			t.Errorf("query does not request %q; projected fields come back empty silently", field)
		}
	}
}

func TestGetOverviewScopesTodayToTheLocalDay(t *testing.T) {
	// "now/d" is rounded by OpenSearch in UTC unless a time_zone is supplied, so
	// the cost and token cards reset at UTC midnight — 17:00 the previous
	// afternoon in US Pacific — and read 0 for the rest of the working day.
	// Measured against the live store at 02:08 UTC: now/d gave 0.0 while the
	// same query with a time_zone gave $325.70.
	c, caps := serveJSON(t, `{"aggregations":{"total":{"value":1.0}},"hits":{"total":{"value":0},"hits":[]}}`)

	if _, err := c.GetOverview(context.Background(), "personal"); err != nil {
		t.Fatalf("GetOverview: %v", err)
	}

	body := caps.get("metrics-otel-*")
	if !strings.Contains(body, "now/d") {
		t.Fatalf("expected a day-rounded window, got: %s", body)
	}
	if !strings.Contains(body, "time_zone") {
		t.Errorf("day-rounded window has no time_zone, so it rounds in UTC: %s", body)
	}
}

func TestLocalDayOffsetIsAnOpenSearchTimeZone(t *testing.T) {
	// OpenSearch accepts either an IANA name or a ±HH:MM offset. The offset is
	// computed per query rather than cached, so it follows DST without needing
	// the zone database.
	got := localDayOffset()

	if !regexp.MustCompile(`^[+-]\d{2}:\d{2}$`).MatchString(got) {
		t.Errorf("localDayOffset() = %q, want a ±HH:MM offset", got)
	}
}

func TestTailLogsReadsDurationsThatArriveAsStrings(t *testing.T) {
	// §P8's mapping mess: the same attribute arrives as a JSON number on some
	// events and a quoted string on others. Measured on the live store, an
	// api_request carries duration_ms as "3562" — a string. A number-only
	// reader returns 0, and 0 renders as ABSENT rather than as wrong, which is
	// why the logs tab showed rows with no duration at all.
	//
	// sessions.go already solved this with numOf; this reader had its own
	// number-only intOf.
	c, _ := serveJSON(t, `{"hits":{"hits":[
	 {"_source":{"time":"2026-10-20T02:17:14.836Z","body":"claude_code.api_request",
	   "log.attributes.duration_ms":"3562"}}
	]}}`)

	got, err := c.TailLogs(context.Background(), "personal", 10)
	if err != nil {
		t.Fatalf("TailLogs: %v", err)
	}
	if got[0].DurationMs != 3562 {
		t.Errorf("DurationMs = %d, want 3562 (string-valued attribute)", got[0].DurationMs)
	}
}

// Measured attribute sets per event type on the live store, 2026-10-20. The
// tail was projecting six fields and discarding the rest, which is why an
// api_request row showed a model and a duration but not what it COST.
const richLogs = `{"hits":{"hits":[
 {"_source":{
   "time":"2026-10-20T02:17:14.836Z","body":"claude_code.api_request","serviceName":"claude-code",
   "log.attributes.event@name":"api_request","log.attributes.model":"claude-opus-5",
   "log.attributes.cost_usd":"0.29892700000000005",
   "log.attributes.input_tokens":"94","log.attributes.output_tokens":"85",
   "log.attributes.cache_read_tokens":"578304","log.attributes.cache_creation_tokens":"718",
   "log.attributes.query_source":"away_summary","log.attributes.duration_ms":"3562"
 }},
 {"_source":{
   "time":"2026-10-20T02:13:59.098Z","body":"claude_code.tool_result","serviceName":"claude-code",
   "log.attributes.event@name":"tool_result","log.attributes.tool_name":"Bash",
   "log.attributes.success":"true","log.attributes.duration_ms":"20083",
   "log.attributes.tool_input":"{\"command\":\"rm -rf /\"}"
 }},
 {"_source":{
   "time":"2026-10-20T02:14:11.214Z","body":"claude_code.hook_execution_complete",
   "serviceName":"claude-code","log.attributes.event@name":"hook_execution_complete",
   "log.attributes.hook_name":"Stop","log.attributes.num_hooks":"2",
   "log.attributes.num_success":"2","log.attributes.total_duration_ms":"428"
 }}
]}}`

func TestTailLogsCarriesCostAndTokens(t *testing.T) {
	// Cost is the single most useful number on an api_request row and it was
	// being discarded. It arrives as a string.
	c, _ := serveJSON(t, richLogs)

	got, err := c.TailLogs(context.Background(), "personal", 10)
	if err != nil {
		t.Fatalf("TailLogs: %v", err)
	}
	if got[0].CostUSD < 0.2989 || got[0].CostUSD > 0.2990 {
		t.Errorf("CostUSD = %v, want ~0.29893", got[0].CostUSD)
	}
	if got[0].InputTokens != 94 || got[0].OutputTokens != 85 {
		t.Errorf("tokens = %d/%d, want 94/85", got[0].InputTokens, got[0].OutputTokens)
	}
	if got[0].CacheReadTokens != 578304 {
		t.Errorf("CacheReadTokens = %d, want 578304 — cache reads dominate and a row without them misleads", got[0].CacheReadTokens)
	}
}

func TestTailLogsNamesTheToolAndTheHook(t *testing.T) {
	// A tool_result row that does not say WHICH tool, and a hook row that does
	// not say which hook, are the two least useful rows in the tail.
	c, _ := serveJSON(t, richLogs)

	got, _ := c.TailLogs(context.Background(), "personal", 10)

	if got[1].ToolName != "Bash" {
		t.Errorf("ToolName = %q, want Bash", got[1].ToolName)
	}
	if got[2].HookName != "Stop" {
		t.Errorf("HookName = %q, want Stop", got[2].HookName)
	}
}

func TestTailLogsReportsQuerySource(t *testing.T) {
	// main vs subagent vs auxiliary. The spec measured main $11,168 / subagent
	// $940 / auxiliary $916, so a cost row without its source cannot be
	// attributed to the work that caused it.
	c, _ := serveJSON(t, richLogs)

	got, _ := c.TailLogs(context.Background(), "personal", 10)

	if got[0].QuerySource != "away_summary" {
		t.Errorf("QuerySource = %q, want away_summary", got[0].QuerySource)
	}
}

func TestTailLogsNeverProjectsToolContent(t *testing.T) {
	// ADR-007 rule 4. tool_input holds the actual command; the tail is not the
	// place for it, and a reader that surfaces it undoes the writers' care.
	c, caps := serveJSON(t, richLogs)

	if _, err := c.TailLogs(context.Background(), "personal", 10); err != nil {
		t.Fatalf("TailLogs: %v", err)
	}

	body := caps.get("logs-otel-*")
	for _, banned := range []string{"tool_input", "tool_parameters", "prompt", "response"} {
		if strings.Contains(body, banned) {
			t.Errorf("tail requests %q — content must not be projected into a log tail", banned)
		}
	}
}

func TestTailLogsFallsBackToTotalDurationForHookEvents(t *testing.T) {
	// hook_execution_complete carries total_duration_ms and no duration_ms, so a
	// duration_ms-only reader shows the two highest-volume events in the index
	// (44% of daily documents) with no timing at all.
	c, _ := serveJSON(t, richLogs)

	got, err := c.TailLogs(context.Background(), "personal", 10)
	if err != nil {
		t.Fatalf("TailLogs: %v", err)
	}
	if got[2].DurationMs != 428 {
		t.Errorf("hook DurationMs = %d, want 428 from total_duration_ms", got[2].DurationMs)
	}
}
