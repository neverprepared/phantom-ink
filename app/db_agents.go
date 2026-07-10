package main

import (
	"fmt"
)

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

// UpsertAgent inserts or updates an agent row. `enabled` is preserved across
// rescans by reading the current value first and writing it back unless the
// caller explicitly wants to overwrite it (use SetAgentEnabled for that).
func (db *DB) UpsertAgent(a DetectedAgent) error {
	_, err := db.conn.Exec(`
		INSERT INTO agents (id, binary, label, path, version, enabled, detected_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			binary      = excluded.binary,
			label       = excluded.label,
			path        = excluded.path,
			version     = excluded.version,
			detected_at = excluded.detected_at`,
		a.ID, a.Binary, a.Label, a.Path, a.Version, boolToInt(a.Enabled), a.DetectedAt)
	return err
}

// GetAgentEnabled returns the persisted enabled flag for an agent, defaulting
// to false when no row exists yet.
func (db *DB) GetAgentEnabled(id string) bool {
	var enabled int
	err := db.conn.QueryRow("SELECT enabled FROM agents WHERE id = ?", id).Scan(&enabled)
	if err != nil {
		return false
	}
	return enabled != 0
}

// ListAgents returns every agent row, newest detection first.
func (db *DB) ListAgents() ([]DetectedAgent, error) {
	rows, err := db.conn.Query(`
		SELECT id, binary, label, path, version, enabled, detected_at
		FROM agents
		ORDER BY label`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []DetectedAgent
	for rows.Next() {
		var r DetectedAgent
		var enabled int
		if err := rows.Scan(&r.ID, &r.Binary, &r.Label, &r.Path, &r.Version, &enabled, &r.DetectedAt); err != nil {
			continue
		}
		r.Enabled = enabled != 0
		r.Detected = r.Path != ""
		result = append(result, r)
	}
	return result, nil
}

// SetAgentEnabled toggles the enabled flag for a single agent.
func (db *DB) SetAgentEnabled(id string, enabled bool) error {
	res, err := db.conn.Exec("UPDATE agents SET enabled = ? WHERE id = ?", boolToInt(enabled), id)
	if err != nil {
		return err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return fmt.Errorf("agent %q not found", id)
	}
	return nil
}
