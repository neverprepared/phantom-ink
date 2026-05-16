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
	// v4: discovered coding-agent CLIs (claude, codex, aider, gemini, …)
	{version: 4, sql: `
		CREATE TABLE IF NOT EXISTS agents (
			id          TEXT PRIMARY KEY,
			binary      TEXT NOT NULL,
			label       TEXT NOT NULL,
			path        TEXT NOT NULL DEFAULT '',
			version     TEXT NOT NULL DEFAULT '',
			enabled     INTEGER NOT NULL DEFAULT 0,
			detected_at TEXT NOT NULL DEFAULT ''
		);
	`},
	// v5: agent chains — ordered sequences of CLI agents wired so each step
	// receives the previous step's output as input. steps_json is the full
	// []ChainStep payload; chain_runs.log_json is the per-step event log.
	{version: 5, sql: `
		CREATE TABLE IF NOT EXISTS chains (
			id          TEXT PRIMARY KEY,
			name        TEXT NOT NULL,
			description TEXT NOT NULL DEFAULT '',
			steps_json  TEXT NOT NULL DEFAULT '[]',
			cwd         TEXT NOT NULL DEFAULT '',
			created_at  TEXT NOT NULL DEFAULT '',
			updated_at  TEXT NOT NULL DEFAULT ''
		);

		CREATE TABLE IF NOT EXISTS chain_runs (
			id           TEXT PRIMARY KEY,
			chain_id     TEXT NOT NULL,
			started_at   TEXT NOT NULL DEFAULT '',
			finished_at  TEXT NOT NULL DEFAULT '',
			status       TEXT NOT NULL DEFAULT 'running',
			log_json     TEXT NOT NULL DEFAULT '[]'
		);
		CREATE INDEX IF NOT EXISTS idx_chain_runs_chain_id ON chain_runs(chain_id);
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

// ---------------------------------------------------------------------------
// Chains
// ---------------------------------------------------------------------------

// ChainRow is the persisted form of a chain definition. The runtime Chain type
// (with structured Steps) lives in chains.go and serializes Steps to/from
// StepsJSON via encoding/json.
type ChainRow struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description"`
	StepsJSON   string `json:"steps_json"`
	Cwd         string `json:"cwd"`
	CreatedAt   string `json:"created_at"`
	UpdatedAt   string `json:"updated_at"`
}

func (db *DB) UpsertChain(c ChainRow) error {
	_, err := db.conn.Exec(`
		INSERT INTO chains (id, name, description, steps_json, cwd, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			name        = excluded.name,
			description = excluded.description,
			steps_json  = excluded.steps_json,
			cwd         = excluded.cwd,
			updated_at  = excluded.updated_at`,
		c.ID, c.Name, c.Description, c.StepsJSON, c.Cwd, c.CreatedAt, c.UpdatedAt)
	return err
}

func (db *DB) GetChain(id string) (ChainRow, bool) {
	var r ChainRow
	err := db.conn.QueryRow(`
		SELECT id, name, description, steps_json, cwd, created_at, updated_at
		FROM chains WHERE id = ?`, id).Scan(
		&r.ID, &r.Name, &r.Description, &r.StepsJSON, &r.Cwd, &r.CreatedAt, &r.UpdatedAt)
	if err != nil {
		return r, false
	}
	return r, true
}

func (db *DB) ListChains() ([]ChainRow, error) {
	rows, err := db.conn.Query(`
		SELECT id, name, description, steps_json, cwd, created_at, updated_at
		FROM chains ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []ChainRow
	for rows.Next() {
		var r ChainRow
		if err := rows.Scan(&r.ID, &r.Name, &r.Description, &r.StepsJSON, &r.Cwd, &r.CreatedAt, &r.UpdatedAt); err != nil {
			continue
		}
		out = append(out, r)
	}
	return out, nil
}

func (db *DB) DeleteChain(id string) error {
	_, err := db.conn.Exec("DELETE FROM chains WHERE id = ?", id)
	return err
}

// ChainRunRow is the persisted form of a single chain execution.
type ChainRunRow struct {
	ID         string `json:"id"`
	ChainID    string `json:"chain_id"`
	StartedAt  string `json:"started_at"`
	FinishedAt string `json:"finished_at"`
	Status     string `json:"status"`
	LogJSON    string `json:"log_json"`
}

func (db *DB) InsertChainRun(r ChainRunRow) error {
	_, err := db.conn.Exec(`
		INSERT INTO chain_runs (id, chain_id, started_at, finished_at, status, log_json)
		VALUES (?, ?, ?, ?, ?, ?)`,
		r.ID, r.ChainID, r.StartedAt, r.FinishedAt, r.Status, r.LogJSON)
	return err
}

func (db *DB) UpdateChainRun(id, finishedAt, status, logJSON string) error {
	_, err := db.conn.Exec(`
		UPDATE chain_runs SET finished_at = ?, status = ?, log_json = ? WHERE id = ?`,
		finishedAt, status, logJSON, id)
	return err
}

func (db *DB) ListChainRuns(chainID string, limit int) ([]ChainRunRow, error) {
	if limit <= 0 {
		limit = 25
	}
	rows, err := db.conn.Query(`
		SELECT id, chain_id, started_at, finished_at, status, log_json
		FROM chain_runs WHERE chain_id = ?
		ORDER BY started_at DESC LIMIT ?`, chainID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []ChainRunRow
	for rows.Next() {
		var r ChainRunRow
		if err := rows.Scan(&r.ID, &r.ChainID, &r.StartedAt, &r.FinishedAt, &r.Status, &r.LogJSON); err != nil {
			continue
		}
		out = append(out, r)
	}
	return out, nil
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
