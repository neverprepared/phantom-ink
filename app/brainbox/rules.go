package brainbox

import (
	"fmt"
	"net/url"
)

// Rule mirrors brainbox event_rules.EventRule. Pattern and Actions are
// untyped JSON passed through verbatim — the frontend owns the action
// shapes and the EventBridge-style pattern DSL. map[string]interface{}
// (not json.RawMessage) so Wails generates Record<string, any> bindings.
type Rule struct {
	ID              string                   `json:"id"`
	Name            string                   `json:"name"`
	Profile         string                   `json:"profile"` // "" or "global" = all workspaces
	Enabled         bool                     `json:"enabled"`
	Description     string                   `json:"description"` // server null → ""
	Pattern         map[string]interface{}   `json:"pattern"`
	Actions         []map[string]interface{} `json:"actions"`
	CreatedAt       int64                    `json:"created_at"` // epoch ms
	UpdatedAt       int64                    `json:"updated_at"`
	LastTriggeredAt *int64                   `json:"last_triggered_at"`
	TriggerCount    int64                    `json:"trigger_count"`
}

type RuleEnabledState struct {
	ID      string `json:"id"`
	Enabled bool   `json:"enabled"`
}

type RuleTestMatch struct {
	Seq    int64  `json:"seq"`
	ID     string `json:"id"`
	Type   string `json:"type"`
	Status string `json:"status"`
	TS     int64  `json:"ts"`
}

type RuleTestResult struct {
	Valid   bool            `json:"valid"`
	Errors  []string        `json:"errors"`
	Matched *bool           `json:"matched,omitempty"` // event mode only
	Matches []RuleTestMatch `json:"matches,omitempty"` // sample mode only
	Scanned int             `json:"scanned,omitempty"`
}

type RuleExecution struct {
	ID          int64                  `json:"id"`
	RuleID      string                 `json:"rule_id"`
	EventSeq    int64                  `json:"event_seq"`
	EventID     string                 `json:"event_id"`
	ActionIndex int                    `json:"action_index"`
	ActionType  string                 `json:"action_type"`
	Status      string                 `json:"status"` // queued|running|ok|failed|throttled|dead
	Attempts    int                    `json:"attempts"`
	Result      map[string]interface{} `json:"result"`
	Error       string                 `json:"error"` // server null → ""
	CreatedAt   int64                  `json:"created_at"`
	UpdatedAt   int64                  `json:"updated_at"`
}

// ruleBody is the create/update payload — only the operator-authored fields;
// stats (trigger_count, last_triggered_at) are server-managed.
type ruleBody struct {
	Name        string                   `json:"name"`
	Profile     string                   `json:"profile"`
	Enabled     bool                     `json:"enabled"`
	Description string                   `json:"description"`
	Pattern     map[string]interface{}   `json:"pattern"`
	Actions     []map[string]interface{} `json:"actions"`
}

func ruleToBody(rule Rule) ruleBody {
	return ruleBody{
		Name:        rule.Name,
		Profile:     rule.Profile,
		Enabled:     rule.Enabled,
		Description: rule.Description,
		Pattern:     rule.Pattern,
		Actions:     rule.Actions,
	}
}

// ListRules returns server-side event rules. A non-empty profile returns
// that profile's rules plus global ones; empty returns all rules (the
// param is omitted entirely — the server treats absent as "no filter").
func (c *Client) ListRules(profile string) ([]Rule, error) {
	path := "/api/rules"
	if profile != "" {
		path += "?profile=" + url.QueryEscape(profile)
	}
	var resp struct {
		Rules []Rule `json:"rules"`
	}
	if err := c.get(path, &resp); err != nil {
		return nil, err
	}
	return resp.Rules, nil
}

func (c *Client) GetRule(id string) (Rule, error) {
	var rule Rule
	err := c.get("/api/rules/"+url.PathEscape(id), &rule)
	return rule, err
}

func (c *Client) CreateRule(rule Rule) (Rule, error) {
	var created Rule
	err := c.post("/api/rules", ruleToBody(rule), &created)
	return created, err
}

func (c *Client) UpdateRule(rule Rule) (Rule, error) {
	var updated Rule
	err := c.put("/api/rules/"+url.PathEscape(rule.ID), ruleToBody(rule), &updated)
	return updated, err
}

func (c *Client) DeleteRule(id string) error {
	return c.delete("/api/rules/"+url.PathEscape(id), nil)
}

func (c *Client) SetRuleEnabled(id string, enabled bool) (RuleEnabledState, error) {
	verb := "disable"
	if enabled {
		verb = "enable"
	}
	var state RuleEnabledState
	err := c.post("/api/rules/"+url.PathEscape(id)+"/"+verb, nil, &state)
	return state, err
}

// TestRulePattern dry-runs a pattern against the most recent agent_events
// rows. Invalid patterns come back as 200 {valid:false, errors} — no error
// is returned for them.
func (c *Client) TestRulePattern(pattern map[string]interface{}, sampleLimit int) (RuleTestResult, error) {
	body := map[string]interface{}{
		"pattern": pattern,
		"sample":  map[string]interface{}{"limit": sampleLimit},
	}
	var result RuleTestResult
	err := c.post("/api/rules/test", body, &result)
	return result, err
}

// TestRuleEvent dry-matches a pattern against one supplied event document.
func (c *Client) TestRuleEvent(pattern, event map[string]interface{}) (RuleTestResult, error) {
	body := map[string]interface{}{"pattern": pattern, "event": event}
	var result RuleTestResult
	err := c.post("/api/rules/test", body, &result)
	return result, err
}

func execQuery(status string, limit, offset int) string {
	q := url.Values{}
	if status != "" {
		q.Set("status", status)
	}
	if limit > 0 {
		q.Set("limit", fmt.Sprintf("%d", limit))
	}
	if offset > 0 {
		q.Set("offset", fmt.Sprintf("%d", offset))
	}
	if enc := q.Encode(); enc != "" {
		return "?" + enc
	}
	return ""
}

func (c *Client) ListRuleExecutions(ruleID, status string, limit, offset int) ([]RuleExecution, error) {
	path := "/api/rules/" + url.PathEscape(ruleID) + "/executions" + execQuery(status, limit, offset)
	var resp struct {
		Executions []RuleExecution `json:"executions"`
	}
	if err := c.get(path, &resp); err != nil {
		return nil, err
	}
	return resp.Executions, nil
}

// ListAllRuleExecutions is the cross-rule view; status="dead" is the DLQ.
func (c *Client) ListAllRuleExecutions(status string, limit, offset int) ([]RuleExecution, error) {
	path := "/api/rules/executions" + execQuery(status, limit, offset)
	var resp struct {
		Executions []RuleExecution `json:"executions"`
	}
	if err := c.get(path, &resp); err != nil {
		return nil, err
	}
	return resp.Executions, nil
}

// RetryRuleExecution requeues a dead/failed/throttled execution. The server
// answers 409 for queued/running/ok rows — surfaced as an HTTP 409 error.
func (c *Client) RetryRuleExecution(executionID int64) (RuleExecution, error) {
	var ex RuleExecution
	err := c.post(fmt.Sprintf("/api/rules/executions/%d/retry", executionID), nil, &ex)
	return ex, err
}

// RulesStatusCounts breaks executions down by status. Ok24h is windowed —
// an all-time ok counter grows unboundedly and means nothing on a strip.
type RulesStatusCounts struct {
	Queued    int64 `json:"queued"`
	Running   int64 `json:"running"`
	Throttled int64 `json:"throttled"`
	Dead      int64 `json:"dead"`
	Ok24h     int64 `json:"ok_24h"`
}

// RulesSinkStatus reports the OpenSearch event sink's cursor health.
type RulesSinkStatus struct {
	Enabled   bool   `json:"enabled"`
	Cursor    int64  `json:"cursor"`
	Lag       int64  `json:"lag"`
	LastError string `json:"last_error"` // server null → ""
}

// RulesStatus is the queue-health snapshot behind the Rules tab status
// strip: execution counts, the consumer's cursor/lag against the event-log
// head, and the sink block.
type RulesStatus struct {
	Counts  RulesStatusCounts `json:"counts"`
	Cursor  int64             `json:"cursor"`
	HeadSeq int64             `json:"head_seq"`
	Lag     int64             `json:"lag"`
	Sink    RulesSinkStatus   `json:"sink"`
}

func (c *Client) GetRulesStatus() (RulesStatus, error) {
	var status RulesStatus
	err := c.get("/api/rules/status", &status)
	return status, err
}
