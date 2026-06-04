package main

import (
	"fmt"
	"time"
)

// ── Automation rules — Wails bindings ─────────────────────────────────────

// ListAutomationRules returns all automation rules, optionally filtered by profile.
func (a *App) ListAutomationRules(profile string) ([]AutomationRule, error) {
	if a.db == nil {
		return []AutomationRule{}, nil
	}
	rules, err := a.db.ListAutomationRules(profile)
	if rules == nil {
		rules = []AutomationRule{}
	}
	return rules, err
}

// SaveAutomationRule creates or updates an automation rule.
func (a *App) SaveAutomationRule(rule AutomationRule) error {
	if a.db == nil {
		return fmt.Errorf("database not initialized")
	}
	if rule.Name == "" {
		return fmt.Errorf("rule name is required")
	}
	if rule.TriggerType == "" {
		return fmt.Errorf("trigger_type is required")
	}
	if rule.ActionType == "" {
		return fmt.Errorf("action_type is required")
	}
	if rule.ID == "" {
		rule.ID = newTaskID() // reuse existing ID generator
	}
	if rule.CreatedAt == 0 {
		rule.CreatedAt = time.Now().UnixMilli()
	}
	return a.db.UpsertAutomationRule(rule)
}

// DeleteAutomationRule removes an automation rule by ID.
func (a *App) DeleteAutomationRule(id string) error {
	if a.db == nil {
		return fmt.Errorf("database not initialized")
	}
	return a.db.DeleteAutomationRule(id)
}

// GetMatchingRules returns automation rules that would match the given
// collected entry — used by the timeline to show contextual action buttons.
func (a *App) GetMatchingRules(jobID, entryID string) ([]AutomationRule, error) {
	if a.db == nil {
		return []AutomationRule{}, nil
	}
	entry, ok := a.db.GetLatestCollectedEntry(jobID, entryID)
	if !ok {
		return []AutomationRule{}, nil
	}
	rules, err := a.db.ListEnabledAutomationRules(entry.Profile)
	if err != nil {
		return nil, err
	}
	evt := AutomationEvent{Type: "entry_created", Profile: entry.Profile, Entry: &entry}
	var matched []AutomationRule
	for _, r := range rules {
		if matchesTrigger(r, evt) {
			matched = append(matched, r)
		}
	}
	if matched == nil {
		matched = []AutomationRule{}
	}
	return matched, nil
}

// TriggerRule manually fires a rule against a specific collected entry.
func (a *App) TriggerRule(ruleID, jobID, entryID string) error {
	if a.db == nil {
		return fmt.Errorf("database not initialized")
	}
	rule, ok := a.db.GetAutomationRule(ruleID)
	if !ok {
		return fmt.Errorf("rule %q not found", ruleID)
	}
	entry, ok := a.db.GetLatestCollectedEntry(jobID, entryID)
	if !ok {
		return fmt.Errorf("entry %s/%s not found", jobID, entryID)
	}
	evt := AutomationEvent{Type: "entry_created", Profile: entry.Profile, Entry: &entry}
	go a.automations.fireAction(rule, evt)
	_ = a.db.RecordAutomationTrigger(ruleID)
	return nil
}
