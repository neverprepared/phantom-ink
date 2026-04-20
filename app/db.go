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

// migration describes a single schema version step. Either sql or fn must be
// set; fn takes precedence when both are set. Use fn for DDL that SQLite's
// modernc driver does not support (e.g. ALTER TABLE … ADD COLUMN IF NOT EXISTS).
type migration struct {
	version int
	sql     string
	fn      func(conn *sql.DB) error
}

// addColumnIfMissing adds a column to table only if it does not already exist.
// modernc.org/sqlite does not support "ALTER TABLE … ADD COLUMN IF NOT EXISTS",
// so we check PRAGMA table_info instead.
func addColumnIfMissing(conn *sql.DB, table, column, definition string) error {
	rows, err := conn.Query("PRAGMA table_info(" + table + ")")
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var cid int
		var name string
		var rest [4]interface{}
		if err := rows.Scan(&cid, &name, &rest[0], &rest[1], &rest[2], &rest[3]); err != nil {
			continue
		}
		if name == column {
			return nil // already present
		}
	}
	_, err = conn.Exec("ALTER TABLE " + table + " ADD COLUMN " + column + " " + definition)
	return err
}

var migrations = []migration{
	{version: 1, sql: `
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
			name          TEXT PRIMARY KEY,
			url           TEXT NOT NULL,
			profile       TEXT NOT NULL DEFAULT '',
			merge_queue   INTEGER NOT NULL DEFAULT 0,
			pr_shepherd   INTEGER NOT NULL DEFAULT 0,
			target_branch TEXT NOT NULL DEFAULT 'main',
			is_fork       INTEGER NOT NULL DEFAULT 0,
			upstream_url  TEXT NOT NULL DEFAULT ''
		);
	`},
	// v2: workspace_home added to repos. Uses fn because modernc.org/sqlite
	// does not support ALTER TABLE … ADD COLUMN IF NOT EXISTS.
	{version: 2, fn: func(conn *sql.DB) error {
		return addColumnIfMissing(conn, "repos", "workspace_home", "TEXT NOT NULL DEFAULT ''")
	}},
	// v3: disk usage cache for profile sizes (avoids slow directory walks on every load)
	{version: 3, sql: `
		CREATE TABLE IF NOT EXISTS disk_cache (
			profile_name TEXT PRIMARY KEY,
			bytes        INTEGER NOT NULL DEFAULT 0,
			scanned_at   TEXT NOT NULL DEFAULT ''
		);
	`},
}

func (db *DB) migrate() error {
	// Ensure the version-tracking table exists before anything else.
	if _, err := db.conn.Exec(`
		CREATE TABLE IF NOT EXISTS schema_version (
			version INTEGER NOT NULL
		)
	`); err != nil {
		return fmt.Errorf("create schema_version table: %w", err)
	}

	var current int
	if err := db.conn.QueryRow("SELECT COALESCE(MAX(version), 0) FROM schema_version").Scan(&current); err != nil {
		return fmt.Errorf("read schema version: %w", err)
	}

	for _, m := range migrations {
		if current >= m.version {
			continue
		}
		if m.fn != nil {
			if err := m.fn(db.conn); err != nil {
				return fmt.Errorf("migration v%d: %w", m.version, err)
			}
		} else {
			if _, err := db.conn.Exec(m.sql); err != nil {
				return fmt.Errorf("migration v%d: %w", m.version, err)
			}
		}
		if _, err := db.conn.Exec("INSERT INTO schema_version (version) VALUES (?)", m.version); err != nil {
			return fmt.Errorf("record schema version v%d: %w", m.version, err)
		}
	}
	return nil
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
