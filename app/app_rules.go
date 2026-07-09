package main

import "phantom-ink/brainbox"

// Wails-bound surface for the brainbox server-side event rules API
// (EventBridge-style rules over the agent event bus). All methods delegate
// to brainbox.Client; errors pass through so the Rules tab can parse
// "HTTP 400: {detail: {pattern_errors: [...]}}" bodies for inline display.

func (a *App) ListRules(profile string) ([]brainbox.Rule, error) {
	return a.client.ListRules(profile)
}

func (a *App) GetRule(id string) (brainbox.Rule, error) {
	return a.client.GetRule(id)
}

// SaveRule creates (empty ID) or updates (ID set). Updates preserve
// server-managed stats (trigger_count, last_triggered_at).
func (a *App) SaveRule(rule brainbox.Rule) (brainbox.Rule, error) {
	if rule.ID == "" {
		return a.client.CreateRule(rule)
	}
	return a.client.UpdateRule(rule)
}

func (a *App) DeleteRule(id string) error {
	return a.client.DeleteRule(id)
}

func (a *App) SetRuleEnabled(id string, enabled bool) (brainbox.RuleEnabledState, error) {
	return a.client.SetRuleEnabled(id, enabled)
}

// TestRulePattern dry-runs a pattern against recent agent_events rows.
func (a *App) TestRulePattern(pattern map[string]interface{}, sampleLimit int) (brainbox.RuleTestResult, error) {
	return a.client.TestRulePattern(pattern, sampleLimit)
}

// TestRuleEvent dry-matches a pattern against one supplied event document.
func (a *App) TestRuleEvent(pattern, event map[string]interface{}) (brainbox.RuleTestResult, error) {
	return a.client.TestRuleEvent(pattern, event)
}

func (a *App) ListRuleExecutions(ruleID, status string, limit, offset int) ([]brainbox.RuleExecution, error) {
	return a.client.ListRuleExecutions(ruleID, status, limit, offset)
}

// ListAllRuleExecutions is the cross-rule view; status="dead" = DLQ.
func (a *App) ListAllRuleExecutions(status string, limit, offset int) ([]brainbox.RuleExecution, error) {
	return a.client.ListAllRuleExecutions(status, limit, offset)
}

func (a *App) RetryRuleExecution(executionID int64) (brainbox.RuleExecution, error) {
	return a.client.RetryRuleExecution(executionID)
}
