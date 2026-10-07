package main

import (
	"os"
	"path/filepath"
	"strings"
)

// ---------------------------------------------------------------------------
// Settings (key-value)
// ---------------------------------------------------------------------------

// GetSetting reads a setting value by key, returning fallback if not found.
func (db *DB) GetSetting(key, fallback string) string {
	var val string
	err := db.conn.QueryRow("SELECT value FROM settings WHERE key = ?", key).Scan(&val)
	if err != nil {
		return fallback
	}
	return val
}

// GetWorkspacesRoot returns the workspaces_root setting or "" if unset.
func (db *DB) GetWorkspacesRoot() string {
	var val string
	if err := db.conn.QueryRow("SELECT value FROM settings WHERE key = 'workspaces_root'").Scan(&val); err != nil || val == "" {
		return ""
	}
	home := os.Getenv("HOME")
	if strings.HasPrefix(val, "~/") {
		val = filepath.Join(home, val[2:])
	}
	return val
}

// SetSetting writes a setting value.
func (db *DB) SetSetting(key, value string) error {
	_, err := db.conn.Exec(
		"INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
		key, value)
	return err
}

// ---------------------------------------------------------------------------
// Integrations
// ---------------------------------------------------------------------------

// IntegrationRow represents a row from the integrations table.
type IntegrationRow struct {
	Name      string `json:"name"`
	Enabled   bool   `json:"enabled"`
	Remote    bool   `json:"remote"`
	LocalURL  string `json:"local_url"`
	RemoteURL string `json:"remote_url"`
	// DesiredRunning is run intent, distinct from Enabled (config intent): set
	// by StartService, cleared by StopService, and converged at launch by
	// ReconcileIntegrations. Not written by UpsertIntegration.
	DesiredRunning bool `json:"desired_running"`
}

// GetIntegration reads integration config by name.
func (db *DB) GetIntegration(name string) (IntegrationRow, bool) {
	var r IntegrationRow
	var enabled, remote, desired int
	err := db.conn.QueryRow(
		"SELECT name, enabled, remote, local_url, remote_url, desired_running FROM integrations WHERE name = ?",
		name).Scan(&r.Name, &enabled, &remote, &r.LocalURL, &r.RemoteURL, &desired)
	if err != nil {
		return r, false
	}
	r.Enabled = enabled != 0
	r.Remote = remote != 0
	r.DesiredRunning = desired != 0
	return r, true
}

// UpsertIntegration inserts or updates an integration's configuration. It
// deliberately does NOT write desired_running — that is run intent, owned by
// StartService/StopService via SetDesiredRunning. Editing a URL or flipping the
// Enabled toggle must not disable self-healing.
func (db *DB) UpsertIntegration(r IntegrationRow) error {
	_, err := db.conn.Exec(`
		INSERT INTO integrations (name, enabled, remote, local_url, remote_url)
		VALUES (?, ?, ?, ?, ?)
		ON CONFLICT(name) DO UPDATE SET
			enabled = excluded.enabled,
			remote = excluded.remote,
			local_url = excluded.local_url,
			remote_url = excluded.remote_url`,
		r.Name, boolToInt(r.Enabled), boolToInt(r.Remote), r.LocalURL, r.RemoteURL)
	return err
}

// AllIntegrations returns all integration rows.
func (db *DB) AllIntegrations() ([]IntegrationRow, error) {
	rows, err := db.conn.Query("SELECT name, enabled, remote, local_url, remote_url, desired_running FROM integrations")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []IntegrationRow
	for rows.Next() {
		var r IntegrationRow
		var enabled, remote, desired int
		if err := rows.Scan(&r.Name, &enabled, &remote, &r.LocalURL, &r.RemoteURL, &desired); err != nil {
			continue
		}
		r.Enabled = enabled != 0
		r.Remote = remote != 0
		r.DesiredRunning = desired != 0
		result = append(result, r)
	}
	return result, nil
}

// SetDesiredRunning records whether the user wants this service's container up.
// Upsert-shaped so a Start on a not-yet-seeded service still records intent,
// and it touches only desired_running so config columns are preserved.
func (db *DB) SetDesiredRunning(name string, desired bool) error {
	_, err := db.conn.Exec(`
		INSERT INTO integrations (name, desired_running)
		VALUES (?, ?)
		ON CONFLICT(name) DO UPDATE SET desired_running = excluded.desired_running`,
		name, boolToInt(desired))
	return err
}

// GetSettingsWithPrefix returns all settings whose key starts with the given
// prefix. Keys in the returned map have the prefix stripped.
func (db *DB) GetSettingsWithPrefix(prefix string) (map[string]string, error) {
	rows, err := db.conn.Query("SELECT key, value FROM settings WHERE key LIKE ?", prefix+"%")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make(map[string]string)
	for rows.Next() {
		var k, v string
		if err := rows.Scan(&k, &v); err != nil {
			continue
		}
		result[k[len(prefix):]] = v
	}
	return result, nil
}
