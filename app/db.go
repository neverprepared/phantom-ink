package main

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"
)

var dbPath = filepath.Join(os.Getenv("HOME"), ".config", "phantom-ink", "phantom-ink.db")

// DB wraps the SQLite connection.
type DB struct {
	conn *sql.DB
}

// OpenDB opens (or creates) the phantom-ink database and runs migrations.
func OpenDB() (*DB, error) {
	if err := os.MkdirAll(filepath.Dir(dbPath), 0700); err != nil {
		return nil, fmt.Errorf("create config dir: %w", err)
	}

	conn, err := sql.Open("sqlite", dbPath+"?_pragma=journal_mode(wal)&_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, fmt.Errorf("open database: %w", err)
	}

	db := &DB{conn: conn}
	if err := db.migrate(); err != nil {
		conn.Close()
		return nil, fmt.Errorf("migrate database: %w", err)
	}
	return db, nil
}

// Close closes the database connection.
func (db *DB) Close() error {
	return db.conn.Close()
}

func (db *DB) migrate() error {
	_, err := db.conn.Exec(`
		CREATE TABLE IF NOT EXISTS settings (
			key   TEXT PRIMARY KEY,
			value TEXT NOT NULL DEFAULT ''
		);

		CREATE TABLE IF NOT EXISTS integrations (
			name       TEXT PRIMARY KEY,
			enabled    INTEGER NOT NULL DEFAULT 0,
			remote     INTEGER NOT NULL DEFAULT 0,
			local_url  TEXT NOT NULL DEFAULT '',
			remote_url TEXT NOT NULL DEFAULT ''
		);

		CREATE TABLE IF NOT EXISTS repos (
			name              TEXT PRIMARY KEY,
			url               TEXT NOT NULL,
			profile           TEXT NOT NULL DEFAULT '',
			merge_queue       INTEGER NOT NULL DEFAULT 0,
			pr_shepherd       INTEGER NOT NULL DEFAULT 0,
			target_branch     TEXT NOT NULL DEFAULT 'main',
			is_fork           INTEGER NOT NULL DEFAULT 0,
			upstream_url      TEXT NOT NULL DEFAULT '',
			workspace_home    TEXT NOT NULL DEFAULT ''
		);
	`)
	return err
}

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
}

// GetIntegration reads integration config by name.
func (db *DB) GetIntegration(name string) (IntegrationRow, bool) {
	var r IntegrationRow
	var enabled, remote int
	err := db.conn.QueryRow(
		"SELECT name, enabled, remote, local_url, remote_url FROM integrations WHERE name = ?",
		name).Scan(&r.Name, &enabled, &remote, &r.LocalURL, &r.RemoteURL)
	if err != nil {
		return r, false
	}
	r.Enabled = enabled != 0
	r.Remote = remote != 0
	return r, true
}

// UpsertIntegration inserts or updates an integration.
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
	rows, err := db.conn.Query("SELECT name, enabled, remote, local_url, remote_url FROM integrations")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []IntegrationRow
	for rows.Next() {
		var r IntegrationRow
		var enabled, remote int
		if err := rows.Scan(&r.Name, &enabled, &remote, &r.LocalURL, &r.RemoteURL); err != nil {
			continue
		}
		r.Enabled = enabled != 0
		r.Remote = remote != 0
		result = append(result, r)
	}
	return result, nil
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
