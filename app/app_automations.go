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
	if err := a.requireDB(); err != nil {
		return err
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
	if err := a.requireDB(); err != nil {
		return err
	}
	return a.db.DeleteAutomationRule(id)
}
