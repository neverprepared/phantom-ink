package brainbox

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// capture records the last request the stub server saw.
type capture struct {
	method string
	path   string // includes raw query
	apiKey string
	body   []byte
}

func stubServer(t *testing.T, status int, respBody string) (*Client, *capture) {
	t.Helper()
	cap := &capture{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cap.method = r.Method
		cap.path = r.URL.RequestURI()
		cap.apiKey = r.Header.Get("X-API-Key")
		cap.body, _ = io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(respBody))
	}))
	t.Cleanup(srv.Close)
	return NewClient(srv.URL, "test-key"), cap
}

func TestListRules(t *testing.T) {
	client, cap := stubServer(t, 200, `{"rules": [{"id": "r1", "name": "n", "profile": "personal",
		"enabled": true, "pattern": {"type": ["task.failed"]},
		"actions": [{"type": "submit_task"}], "trigger_count": 3}], "count": 1}`)

	rules, err := client.ListRules("personal")
	if err != nil {
		t.Fatal(err)
	}
	if cap.method != "GET" || cap.path != "/api/rules?profile=personal" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
	if cap.apiKey != "test-key" {
		t.Fatalf("missing api key header")
	}
	if len(rules) != 1 || rules[0].ID != "r1" || rules[0].TriggerCount != 3 {
		t.Fatalf("bad decode: %+v", rules)
	}
	if rules[0].Pattern["type"] == nil {
		t.Fatalf("pattern not decoded: %+v", rules[0].Pattern)
	}
}

func TestListRulesOmitsEmptyProfile(t *testing.T) {
	client, cap := stubServer(t, 200, `{"rules": [], "count": 0}`)
	if _, err := client.ListRules(""); err != nil {
		t.Fatal(err)
	}
	if cap.path != "/api/rules" {
		t.Fatalf("empty profile must omit the param, got %s", cap.path)
	}
}

func TestCreateRulePostsTrimmedBody(t *testing.T) {
	client, cap := stubServer(t, 201, `{"id": "new1", "name": "n", "enabled": true,
		"pattern": {}, "actions": []}`)

	rule := Rule{
		Name:         "n",
		Profile:      "personal",
		Enabled:      true,
		Pattern:      map[string]interface{}{"type": []interface{}{"task.failed"}},
		Actions:      []map[string]interface{}{{"type": "submit_task", "agent_name": "a", "description": "d"}},
		TriggerCount: 99, // server-managed — must not be sent
	}
	created, err := client.CreateRule(rule)
	if err != nil {
		t.Fatal(err)
	}
	if cap.method != "POST" || cap.path != "/api/rules" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
	var sent map[string]interface{}
	if err := json.Unmarshal(cap.body, &sent); err != nil {
		t.Fatal(err)
	}
	if _, ok := sent["trigger_count"]; ok {
		t.Fatalf("trigger_count leaked into create body: %s", cap.body)
	}
	if sent["name"] != "n" || sent["profile"] != "personal" {
		t.Fatalf("bad body: %s", cap.body)
	}
	if created.ID != "new1" {
		t.Fatalf("bad decode: %+v", created)
	}
}

func TestUpdateRulePutsToId(t *testing.T) {
	client, cap := stubServer(t, 200, `{"id": "r1", "name": "renamed"}`)
	_, err := client.UpdateRule(Rule{ID: "r1", Name: "renamed", Pattern: map[string]interface{}{}})
	if err != nil {
		t.Fatal(err)
	}
	if cap.method != "PUT" || cap.path != "/api/rules/r1" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
}

func TestDeleteRule(t *testing.T) {
	client, cap := stubServer(t, 204, ``)
	if err := client.DeleteRule("r1"); err != nil {
		t.Fatal(err)
	}
	if cap.method != "DELETE" || cap.path != "/api/rules/r1" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
}

func TestSetRuleEnabled(t *testing.T) {
	client, cap := stubServer(t, 200, `{"id": "r1", "enabled": true}`)
	state, err := client.SetRuleEnabled("r1", true)
	if err != nil {
		t.Fatal(err)
	}
	if cap.path != "/api/rules/r1/enable" || !state.Enabled {
		t.Fatalf("enable: %s %+v", cap.path, state)
	}

	client2, cap2 := stubServer(t, 200, `{"id": "r1", "enabled": false}`)
	if _, err := client2.SetRuleEnabled("r1", false); err != nil {
		t.Fatal(err)
	}
	if cap2.path != "/api/rules/r1/disable" {
		t.Fatalf("disable path: %s", cap2.path)
	}
}

func TestTestRulePatternSampleMode(t *testing.T) {
	client, cap := stubServer(t, 200, `{"valid": true, "errors": [],
		"matches": [{"seq": 4, "id": "e1", "type": "task.failed", "status": "failed", "ts": 1}],
		"scanned": 20}`)

	res, err := client.TestRulePattern(map[string]interface{}{"type": []interface{}{"task.failed"}}, 50)
	if err != nil {
		t.Fatal(err)
	}
	if cap.method != "POST" || cap.path != "/api/rules/test" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
	var sent map[string]interface{}
	_ = json.Unmarshal(cap.body, &sent)
	sample, _ := sent["sample"].(map[string]interface{})
	if sample == nil || sample["limit"] != float64(50) {
		t.Fatalf("bad sample body: %s", cap.body)
	}
	if !res.Valid || len(res.Matches) != 1 || res.Matches[0].Seq != 4 || res.Scanned != 20 {
		t.Fatalf("bad decode: %+v", res)
	}
}

func TestTestRulePatternInvalidIs200(t *testing.T) {
	client, _ := stubServer(t, 200, `{"valid": false, "errors": ["type: match list must not be empty"]}`)
	res, err := client.TestRulePattern(map[string]interface{}{"type": []interface{}{}}, 50)
	if err != nil {
		t.Fatal(err) // invalid patterns are NOT transport errors
	}
	if res.Valid || len(res.Errors) != 1 {
		t.Fatalf("bad decode: %+v", res)
	}
}

func TestTestRuleEventMode(t *testing.T) {
	client, cap := stubServer(t, 200, `{"valid": true, "errors": [], "matched": true}`)
	res, err := client.TestRuleEvent(
		map[string]interface{}{"type": []interface{}{"x"}},
		map[string]interface{}{"type": "x"},
	)
	if err != nil {
		t.Fatal(err)
	}
	var sent map[string]interface{}
	_ = json.Unmarshal(cap.body, &sent)
	if _, ok := sent["event"]; !ok {
		t.Fatalf("event missing from body: %s", cap.body)
	}
	if res.Matched == nil || !*res.Matched {
		t.Fatalf("bad decode: %+v", res)
	}
}

func TestListRuleExecutionsQuery(t *testing.T) {
	client, cap := stubServer(t, 200, `{"executions": [{"id": 9, "rule_id": "r1",
		"event_seq": 4, "action_type": "webhook", "status": "dead", "attempts": 3,
		"error": "boom"}], "count": 1}`)

	execs, err := client.ListRuleExecutions("r1", "dead", 50, 10)
	if err != nil {
		t.Fatal(err)
	}
	if cap.path != "/api/rules/r1/executions?limit=50&offset=10&status=dead" {
		t.Fatalf("unexpected query: %s", cap.path)
	}
	if len(execs) != 1 || execs[0].ID != 9 || execs[0].Error != "boom" {
		t.Fatalf("bad decode: %+v", execs)
	}
}

func TestListAllRuleExecutions(t *testing.T) {
	client, cap := stubServer(t, 200, `{"executions": [], "count": 0}`)
	if _, err := client.ListAllRuleExecutions("dead", 2000, 0); err != nil {
		t.Fatal(err)
	}
	if cap.path != "/api/rules/executions?limit=2000&status=dead" {
		t.Fatalf("unexpected query: %s", cap.path)
	}
}

func TestRetryRuleExecution(t *testing.T) {
	client, cap := stubServer(t, 200, `{"id": 9, "rule_id": "r1", "status": "queued", "attempts": 0}`)
	ex, err := client.RetryRuleExecution(9)
	if err != nil {
		t.Fatal(err)
	}
	if cap.method != "POST" || cap.path != "/api/rules/executions/9/retry" {
		t.Fatalf("unexpected request: %s %s", cap.method, cap.path)
	}
	if ex.Status != "queued" {
		t.Fatalf("bad decode: %+v", ex)
	}
}

func TestRetryConflictSurfacesHTTP409(t *testing.T) {
	client, _ := stubServer(t, 409, `{"detail": "Execution is 'ok' — not retryable"}`)
	_, err := client.RetryRuleExecution(9)
	if err == nil || !strings.Contains(err.Error(), "HTTP 409") {
		t.Fatalf("expected HTTP 409 error, got %v", err)
	}
}

func TestPatternErrorsSurfaceInError(t *testing.T) {
	client, _ := stubServer(t, 400, `{"detail": {"pattern_errors": ["type: match list must not be empty"]}}`)
	_, err := client.CreateRule(Rule{Name: "n", Pattern: map[string]interface{}{}, Actions: []map[string]interface{}{{"type": "submit_task"}}})
	if err == nil || !strings.Contains(err.Error(), "pattern_errors") {
		t.Fatalf("expected pattern_errors in error string, got %v", err)
	}
}
