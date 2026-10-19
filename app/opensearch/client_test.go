package opensearch

import (
	"context"
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
