package main

import (
	"database/sql"
	"time"
)

// Automation rules
// ---------------------------------------------------------------------------

func (db *DB) ListEnabledAutomationRules(profile string) ([]AutomationRule, error) {
	// Returns rules matching the given profile OR global rules (profile='').
	q := `SELECT ` + automationRuleCols + `
	      FROM automation_rules
	      WHERE enabled = 1 AND (profile = '' OR profile = ?)
	      ORDER BY name ASC`
	rows, err := db.conn.Query(q, profile)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanAutomationRules(rows)
}

func (db *DB) ListAutomationRules(profile string) ([]AutomationRule, error) {
	q := `SELECT ` + automationRuleCols + `
	      FROM automation_rules`
	args := []any{}
	if profile != "" {
		q += " WHERE profile = ?"
		args = append(args, profile)
	}
	q += " ORDER BY name ASC"
	rows, err := db.conn.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanAutomationRules(rows)
}

func (db *DB) GetAutomationRule(id string) (AutomationRule, bool) {
	r, err := scanAutomationRule(db.conn.QueryRow(`SELECT `+automationRuleCols+` FROM automation_rules WHERE id = ?`, id))
	if err != nil {
		return AutomationRule{}, false
	}
	return r, true
}

func (db *DB) UpsertAutomationRule(r AutomationRule) error {
	if r.TriggerConfig == "" {
		r.TriggerConfig = "{}"
	}
	if r.ActionConfig == "" {
		r.ActionConfig = "{}"
	}
	_, err := db.conn.Exec(`
		INSERT INTO automation_rules
			(id, profile, name, description, enabled, trigger_type, trigger_config,
			 action_type, action_config, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			profile        = excluded.profile,
			name           = excluded.name,
			description    = excluded.description,
			enabled        = excluded.enabled,
			trigger_type   = excluded.trigger_type,
			trigger_config = excluded.trigger_config,
			action_type    = excluded.action_type,
			action_config  = excluded.action_config`,
		r.ID, r.Profile, r.Name, r.Description, boolToInt(r.Enabled),
		r.TriggerType, r.TriggerConfig, r.ActionType, r.ActionConfig, r.CreatedAt)
	return err
}

func (db *DB) DeleteAutomationRule(id string) error {
	_, err := db.conn.Exec("DELETE FROM automation_rules WHERE id = ?", id)
	return err
}

func (db *DB) RecordAutomationTrigger(id string) error {
	now := time.Now().UnixMilli()
	_, err := db.conn.Exec(`
		UPDATE automation_rules
		SET last_triggered_at = ?, trigger_count = trigger_count + 1
		WHERE id = ?`, now, id)
	return err
}

const automationRuleCols = `id, profile, name, description, enabled, trigger_type, trigger_config,
	action_type, action_config, created_at, last_triggered_at, trigger_count`

func scanAutomationRule(s rowScanner) (AutomationRule, error) {
	var r AutomationRule
	var enabled int
	var lastTriggered sql.NullInt64
	err := s.Scan(&r.ID, &r.Profile, &r.Name, &r.Description, &enabled,
		&r.TriggerType, &r.TriggerConfig, &r.ActionType, &r.ActionConfig,
		&r.CreatedAt, &lastTriggered, &r.TriggerCount)
	r.Enabled = enabled != 0
	if lastTriggered.Valid {
		r.LastTriggeredAt = &lastTriggered.Int64
	}
	return r, err
}

func scanAutomationRules(rows *sql.Rows) ([]AutomationRule, error) {
	var out []AutomationRule
	for rows.Next() {
		r, err := scanAutomationRule(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}
