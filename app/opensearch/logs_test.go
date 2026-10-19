package opensearch

import "testing"

// These fixtures are real _source shapes taken from logs-otel-2026.10.19, not
// invented. The important property is that Data Prepper FLATTENS OTEL log
// attributes into literal dotted keys ("log.attributes.hook_name"), so a
// nested read finds nothing — which is how these records were once mistaken
// for carrying no attributes at all.

func hookCompleteSource() map[string]any {
	return map[string]any{
		"time":                                  "2026-10-19T19:59:58.066Z",
		"body":                                  "claude_code.hook_execution_complete",
		"severityText":                          "",
		"resource.attributes.workspace":         "personal",
		"resource.attributes.service@name":      "claude-code",
		"log.attributes.event@name":             "hook_execution_complete",
		"log.attributes.session@id":             "0d924ba8-9590-4a00-a55f-91b275f1720f",
		"log.attributes.hook_name":              "PostToolUse:Bash",
		"log.attributes.hook_event":             "PostToolUse",
		"log.attributes.hook_source":            "merged",
		"log.attributes.num_hooks":              "3",
		"log.attributes.num_success":            "3",
		"log.attributes.num_blocking":           "0",
		"log.attributes.num_cancelled":          "0",
		"log.attributes.num_non_blocking_error": "0",
		"log.attributes.total_duration_ms":      "38",
		// Identical on every record from this machine; must not reach the UI.
		"log.attributes.user@email":      "someone@example.com",
		"log.attributes.organization@id": "0c5dad27-acf4-47c4-9e96-d9980f5d845d",
	}
}

func toolResultSource() map[string]any {
	return map[string]any{
		"time":                             "2026-10-19T02:48:51.589Z",
		"body":                             "claude_code.tool_result",
		"resource.attributes.workspace":    "personal",
		"resource.attributes.service@name": "claude-code",
		"log.attributes.event@name":        "tool_result",
		"log.attributes.tool_name":         "Bash",
		"log.attributes.success":           "false",
		"log.attributes.error_type":        "ShellError",
		"log.attributes.duration_ms":       "9403",
	}
}

// -- the envelope ---------------------------------------------------------------

func TestProjectsCommonEnvelope(t *testing.T) {
	e := projectLogEntry(hookCompleteSource())
	if e.Time == "" || e.Body == "" {
		t.Fatalf("time/body not projected: %+v", e)
	}
	if e.Workspace != "personal" {
		t.Errorf("workspace = %q, want personal", e.Workspace)
	}
	if e.Service != "claude-code" {
		t.Errorf("service = %q, want claude-code", e.Service)
	}
	if e.Event != "hook_execution_complete" {
		t.Errorf("event = %q", e.Event)
	}
	if e.Session == "" {
		t.Error("session not projected")
	}
}

// -- duration normalisation -----------------------------------------------------

func TestHookDurationComesFromTotalDurationMs(t *testing.T) {
	// The regression this guards: hook events spell it total_duration_ms, so
	// reading only duration_ms rendered every hook line with no duration.
	if got := projectLogEntry(hookCompleteSource()).DurationMs; got != 38 {
		t.Errorf("DurationMs = %d, want 38", got)
	}
}

func TestToolDurationComesFromDurationMs(t *testing.T) {
	if got := projectLogEntry(toolResultSource()).DurationMs; got != 9403 {
		t.Errorf("DurationMs = %d, want 9403", got)
	}
}

func TestDurationMsWinsWhenBothPresent(t *testing.T) {
	s := hookCompleteSource()
	s["log.attributes.duration_ms"] = "7"
	if got := projectLogEntry(s).DurationMs; got != 7 {
		t.Errorf("DurationMs = %d, want 7", got)
	}
}

func TestMissingDurationIsZero(t *testing.T) {
	s := hookCompleteSource()
	delete(s, "log.attributes.total_duration_ms")
	if got := projectLogEntry(s).DurationMs; got != 0 {
		t.Errorf("DurationMs = %d, want 0", got)
	}
}

// -- the attributes that carry the meaning --------------------------------------

func TestHookAttributesSurvive(t *testing.T) {
	// "what did the hook do?" has to be answerable from the projection alone.
	e := projectLogEntry(hookCompleteSource())
	want := map[string]any{
		"hook_name":   "PostToolUse:Bash",
		"hook_event":  "PostToolUse",
		"num_hooks":   "3",
		"num_success": "3",
	}
	for k, v := range want {
		if e.Attrs[k] != v {
			t.Errorf("Attrs[%q] = %v, want %v", k, e.Attrs[k], v)
		}
	}
}

func TestFailedToolResultIsDistinguishable(t *testing.T) {
	// Under the old six-field allowlist a failed tool_result was byte-identical
	// to a successful one, because success and error_type were never fetched.
	e := projectLogEntry(toolResultSource())
	if e.Attrs["success"] != "false" {
		t.Errorf("success = %#v, want \"false\"", e.Attrs["success"])
	}
	if e.Attrs["error_type"] != "ShellError" {
		t.Errorf("error_type = %v", e.Attrs["error_type"])
	}
	if e.Attrs["tool_name"] != "Bash" {
		t.Errorf("tool_name = %v", e.Attrs["tool_name"])
	}
}

// -- hygiene --------------------------------------------------------------------

func TestPromotedAttributesAreNotDuplicatedInAttrs(t *testing.T) {
	e := projectLogEntry(toolResultSource())
	for _, k := range []string{"session@id", "event@name", "model", "duration_ms"} {
		if _, dup := e.Attrs[k]; dup {
			t.Errorf("Attrs still carries promoted key %q", k)
		}
	}
}

func TestIdentityAttributesAreNotProjected(t *testing.T) {
	// Excluded at the query too, but a tail must never render an email just to
	// draw a log line — belt and braces, since _source excludes are easy to
	// edit away by accident.
	e := projectLogEntry(hookCompleteSource())
	for k := range e.Attrs {
		if k == "user@email" || k == "organization@id" {
			t.Errorf("identity attribute %q leaked into Attrs", k)
		}
	}
}

func TestNilAttributeValuesAreDropped(t *testing.T) {
	s := toolResultSource()
	s["log.attributes.nothing"] = nil
	if _, ok := projectLogEntry(s).Attrs["nothing"]; ok {
		t.Error("nil attribute should not be projected")
	}
}

func TestEmptySourceDoesNotPanic(t *testing.T) {
	e := projectLogEntry(map[string]any{})
	if e.Time != "" || e.Attrs != nil {
		t.Errorf("unexpected projection from empty source: %+v", e)
	}
}

func TestSeverityIsCarried(t *testing.T) {
	s := toolResultSource()
	s["severityText"] = "ERROR"
	if got := projectLogEntry(s).Severity; got != "ERROR" {
		t.Errorf("Severity = %q, want ERROR", got)
	}
}

// -- numeric coercion -----------------------------------------------------------

// These indices are dynamically mapped, so the same field name arrives as a
// different JSON type depending on which document first typed it in a given
// daily index. client.go already documents this for duration_ms on the latency
// aggregation; the projection has to tolerate it too. Reading only the numeric
// forms yielded 0, which renders as "no duration" rather than as a visible bug.
func TestIntOfAcceptsEveryShapeOpenSearchReturns(t *testing.T) {
	cases := []struct {
		name string
		in   any
		want int64
	}{
		{"float64", float64(38), 38},
		{"int", 38, 38},
		{"int64", int64(38), 38},
		{"numeric string", "38", 38},
		{"float string", "38.7", 38},
		{"padded string", "  38  ", 38},
		{"empty string", "", 0},
		{"non-numeric string", "fast", 0},
		{"nil", nil, 0},
		{"bool", true, 0},
	}
	for _, c := range cases {
		if got := intOf(c.in); got != c.want {
			t.Errorf("%s: intOf(%#v) = %d, want %d", c.name, c.in, got, c.want)
		}
	}
}

func TestApiRequestNumericDurationStillWorks(t *testing.T) {
	// api_request really does carry a float64 here, so both spellings must work.
	e := projectLogEntry(map[string]any{
		"log.attributes.event@name":  "api_request",
		"log.attributes.duration_ms": float64(2926),
	})
	if e.DurationMs != 2926 {
		t.Errorf("DurationMs = %d, want 2926", e.DurationMs)
	}
}
