package main

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

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
	// v6: task queue — durable work items that the in-app worker drains. A
	// task targets a chain and, on dispatch, becomes a chain_runs row. status
	// is one of: pending | running | succeeded | failed | cancelled. trigger
	// is one of: manual | schedule | webhook | followup.
	{version: 6, sql: `
		CREATE TABLE IF NOT EXISTS tasks (
			id              TEXT PRIMARY KEY,
			chain_id        TEXT NOT NULL,
			status          TEXT NOT NULL DEFAULT 'pending',
			priority        INTEGER NOT NULL DEFAULT 0,
			input           TEXT NOT NULL DEFAULT '',
			cwd             TEXT NOT NULL DEFAULT '',
			trigger         TEXT NOT NULL DEFAULT 'manual',
			parent_task_id  TEXT NOT NULL DEFAULT '',
			enqueued_at     TEXT NOT NULL DEFAULT '',
			scheduled_for   TEXT NOT NULL DEFAULT '',
			started_at      TEXT NOT NULL DEFAULT '',
			finished_at     TEXT NOT NULL DEFAULT '',
			attempts        INTEGER NOT NULL DEFAULT 0,
			max_attempts    INTEGER NOT NULL DEFAULT 1,
			last_error      TEXT NOT NULL DEFAULT '',
			result_run_id   TEXT NOT NULL DEFAULT ''
		);
		CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority DESC, enqueued_at);
		CREATE INDEX IF NOT EXISTS idx_tasks_chain_id ON tasks(chain_id);
	`},
	// v7: cron schedules — fire chain runs on a recurring expression. The
	// scheduler goroutine reads the table on tick and enqueues a task per
	// due schedule, recording last_fired_at to suppress double-firing.
	{version: 7, sql: `
		CREATE TABLE IF NOT EXISTS schedules (
			id             TEXT PRIMARY KEY,
			chain_id       TEXT NOT NULL,
			cron_expr      TEXT NOT NULL,
			input          TEXT NOT NULL DEFAULT '',
			cwd            TEXT NOT NULL DEFAULT '',
			enabled        INTEGER NOT NULL DEFAULT 1,
			created_at     TEXT NOT NULL DEFAULT '',
			updated_at     TEXT NOT NULL DEFAULT '',
			last_fired_at  TEXT NOT NULL DEFAULT ''
		);
		CREATE INDEX IF NOT EXISTS idx_schedules_chain_id ON schedules(chain_id);
		CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON schedules(enabled);
	`},
	// v8: declarative on_success follow-ups for chains. Stored as a JSON
	// array of ChainFollowup on the chains row so we don't need a join
	// table; followups are read+written wholesale with the chain.
	{version: 8, fn: func(conn *sql.DB) error {
		return addColumnIfMissing(conn, "chains", "on_success_json", "TEXT NOT NULL DEFAULT '[]'")
	}},
	// v9: profile snapshotting on tasks and schedules. Every queued task and
	// every saved schedule remembers the active profile at create time, so
	// runs and cron firings always execute in the right workspace context
	// regardless of who's at the keyboard later. Profiles are foundational.
	{version: 9, fn: func(conn *sql.DB) error {
		if err := addColumnIfMissing(conn, "tasks", "workspace_profile", "TEXT NOT NULL DEFAULT ''"); err != nil {
			return err
		}
		return addColumnIfMissing(conn, "schedules", "workspace_profile", "TEXT NOT NULL DEFAULT ''")
	}},
	// v10: file attachments on chains. Paths are stored relative to the
	// profile's workspace_home so the same chain works across profiles —
	// "code/api/main.go" means whatever's at that location in the active
	// profile at run time. {{files}} expands to absolute paths in prompts.
	{version: 10, fn: func(conn *sql.DB) error {
		return addColumnIfMissing(conn, "chains", "files_json", "TEXT NOT NULL DEFAULT '[]'")
	}},
	// v17: profile-scoped chains. workspace_profile="" means global (visible
	// in all profiles). Non-empty means the chain belongs to that profile only.
	{version: 17, fn: func(conn *sql.DB) error {
		return addColumnIfMissing(conn, "chains", "workspace_profile", "TEXT NOT NULL DEFAULT ''")
	}},
	// v11: data collection scheduler — periodic commands whose output is
	// stored as timeline entries (metrics, events). collected_entries are
	// upserted by (job_id, entry_id) so reruns update existing rows.
	{version: 11, sql: `
		CREATE TABLE IF NOT EXISTS collect_jobs (
			id              TEXT PRIMARY KEY,
			profile         TEXT NOT NULL DEFAULT '',
			name            TEXT NOT NULL,
			command         TEXT NOT NULL,
			interval_s      INTEGER NOT NULL DEFAULT 300,
			enabled         INTEGER NOT NULL DEFAULT 1,
			default_actions TEXT NOT NULL DEFAULT '[]',
			last_run_at     INTEGER,
			last_error      TEXT NOT NULL DEFAULT '',
			created_at      INTEGER NOT NULL
		);
		CREATE INDEX IF NOT EXISTS idx_collect_jobs_profile ON collect_jobs(profile);

		CREATE TABLE IF NOT EXISTS collected_entries (
			job_id       TEXT NOT NULL,
			entry_id     TEXT NOT NULL,
			profile      TEXT NOT NULL DEFAULT '',
			kind         TEXT NOT NULL DEFAULT 'metric',
			title        TEXT NOT NULL DEFAULT '',
			description  TEXT NOT NULL DEFAULT '',
			value        TEXT NOT NULL DEFAULT '',
			url          TEXT NOT NULL DEFAULT '',
			start_at     INTEGER,
			end_at       INTEGER,
			status       TEXT NOT NULL DEFAULT 'active',
			tags         TEXT NOT NULL DEFAULT '[]',
			metadata     TEXT NOT NULL DEFAULT '{}',
			actions      TEXT NOT NULL DEFAULT '[]',
			collected_at INTEGER NOT NULL,
			PRIMARY KEY (job_id, entry_id)
		);
		CREATE INDEX IF NOT EXISTS idx_collected_entries_profile   ON collected_entries(profile);
		CREATE INDEX IF NOT EXISTS idx_collected_entries_kind      ON collected_entries(profile, kind);
		CREATE INDEX IF NOT EXISTS idx_collected_entries_start_at  ON collected_entries(start_at);
		CREATE INDEX IF NOT EXISTS idx_collected_entries_collected ON collected_entries(collected_at DESC);
	`},
	{version: 12, sql: `
		CREATE TABLE IF NOT EXISTS profile_images (
			profile         TEXT PRIMARY KEY,
			registry_url    TEXT NOT NULL DEFAULT '',
			last_pushed_at  TEXT NOT NULL DEFAULT '',
			last_digest     TEXT NOT NULL DEFAULT ''
		);
	`},
	{version: 13, sql: `
		ALTER TABLE profile_images ADD COLUMN env_key TEXT NOT NULL DEFAULT '';
	`},
	// v14: composable job targets and time-of-day scheduling.
	{version: 14, fn: func(conn *sql.DB) error {
		for _, col := range []struct{ name, def string }{
			{"target_type", "TEXT NOT NULL DEFAULT 'shell'"},
			{"target_id", "TEXT NOT NULL DEFAULT ''"},
			{"target_prompt", "TEXT NOT NULL DEFAULT ''"},
			{"run_at", "TEXT NOT NULL DEFAULT ''"},
			{"days", "TEXT NOT NULL DEFAULT ''"},
		} {
			if err := addColumnIfMissing(conn, "collect_jobs", col.name, col.def); err != nil {
				return err
			}
		}
		return nil
	}},
	// v15: automation rules — event-driven trigger → action pairs.
	{version: 15, sql: `
		CREATE TABLE IF NOT EXISTS automation_rules (
			id                TEXT PRIMARY KEY,
			profile           TEXT NOT NULL DEFAULT '',
			name              TEXT NOT NULL DEFAULT '',
			description       TEXT NOT NULL DEFAULT '',
			enabled           INTEGER NOT NULL DEFAULT 1,
			trigger_type      TEXT NOT NULL DEFAULT '',
			trigger_config    TEXT NOT NULL DEFAULT '{}',
			action_type       TEXT NOT NULL DEFAULT '',
			action_config     TEXT NOT NULL DEFAULT '{}',
			created_at        INTEGER NOT NULL DEFAULT 0,
			last_triggered_at INTEGER,
			trigger_count     INTEGER NOT NULL DEFAULT 0
		);
		CREATE INDEX IF NOT EXISTS idx_automation_rules_profile ON automation_rules(profile, enabled);
	`},
	// v16: persisted dismissals for the Stream panel's attention queue.
	// `id` is the stable AttentionItem ID built from "<source>:<source_id>".
	{version: 16, sql: `
		CREATE TABLE IF NOT EXISTS dismissed_attention (
			id           TEXT PRIMARY KEY,
			dismissed_at INTEGER NOT NULL DEFAULT 0
		);
	`},
	// v18: producer-driven attention items. Agents and the task queue insert
	// directly into this table at the moment of failure; the aggregator unions
	// it with the two legacy scraped sources. resolved_at=NULL means active.
	{version: 18, sql: `
		CREATE TABLE IF NOT EXISTS attention_items (
			id           TEXT PRIMARY KEY,
			source       TEXT NOT NULL,
			source_id    TEXT NOT NULL,
			workspace    TEXT NOT NULL DEFAULT '',
			title        TEXT NOT NULL DEFAULT '',
			subtitle     TEXT NOT NULL DEFAULT '',
			reason       TEXT NOT NULL DEFAULT '',
			url          TEXT NOT NULL DEFAULT '',
			actions_json TEXT NOT NULL DEFAULT '[]',
			context_json TEXT NOT NULL DEFAULT '{}',
			user_reply   TEXT NOT NULL DEFAULT '',
			created_at   INTEGER NOT NULL DEFAULT 0,
			resolved_at  INTEGER
		);
		CREATE INDEX IF NOT EXISTS idx_attention_active
			ON attention_items(resolved_at, workspace, created_at DESC);
	`},
	// v19: re-assert dismissed_attention for DBs that recorded v16 in
	// schema_version without actually creating the table (happened in
	// development when migration numbering was reordered).
	{version: 19, sql: `
		CREATE TABLE IF NOT EXISTS dismissed_attention (
			id           TEXT PRIMARY KEY,
			dismissed_at INTEGER NOT NULL DEFAULT 0
		);
	`},
	// v20: collect_jobs.source — identifies where a job came from
	// ("widget" for dashboard-widget-bound, "" for manually created).
	// Used by the Jobs panel to badge widget jobs and by the dashboard
	// to safely dedupe widget-owned collect jobs.
	{version: 20, fn: func(conn *sql.DB) error {
		return addColumnIfMissing(conn, "collect_jobs", "source", "TEXT NOT NULL DEFAULT ''")
	}},
	// v21: collect_jobs.owner_widget_id — direct id-based link from a
	// dashboard widget to its bound job. Replaces the fragile name+command
	// fingerprint when looking up the owning widget.
	{version: 21, fn: func(conn *sql.DB) error {
		return addColumnIfMissing(conn, "collect_jobs", "owner_widget_id", "TEXT NOT NULL DEFAULT ''")
	}},
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

	// Sort by version to guard against slice misordering.
	sorted := append([]migration(nil), migrations...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].version < sorted[j].version })

	for _, m := range sorted {
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
// StepsJSON via encoding/json. OnSuccessJSON is the same idea for the
// declarative followups list — read+written wholesale with the chain.
type ChainRow struct {
	ID               string `json:"id"`
	Name             string `json:"name"`
	Description      string `json:"description"`
	StepsJSON        string `json:"steps_json"`
	Cwd              string `json:"cwd"`
	OnSuccessJSON    string `json:"on_success_json"`
	FilesJSON        string `json:"files_json"`
	WorkspaceProfile string `json:"workspace_profile"`
	CreatedAt        string `json:"created_at"`
	UpdatedAt        string `json:"updated_at"`
}

func (db *DB) UpsertChain(c ChainRow) error {
	if c.OnSuccessJSON == "" {
		c.OnSuccessJSON = "[]"
	}
	if c.FilesJSON == "" {
		c.FilesJSON = "[]"
	}
	_, err := db.conn.Exec(`
		INSERT INTO chains (id, name, description, steps_json, cwd, on_success_json, files_json, workspace_profile, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			name              = excluded.name,
			description       = excluded.description,
			steps_json        = excluded.steps_json,
			cwd               = excluded.cwd,
			on_success_json   = excluded.on_success_json,
			files_json        = excluded.files_json,
			workspace_profile = excluded.workspace_profile,
			updated_at        = excluded.updated_at`,
		c.ID, c.Name, c.Description, c.StepsJSON, c.Cwd, c.OnSuccessJSON, c.FilesJSON, c.WorkspaceProfile, c.CreatedAt, c.UpdatedAt)
	return err
}

func (db *DB) GetChain(id string) (ChainRow, bool) {
	var r ChainRow
	err := db.conn.QueryRow(`
		SELECT id, name, description, steps_json, cwd, on_success_json, files_json, workspace_profile, created_at, updated_at
		FROM chains WHERE id = ?`, id).Scan(
		&r.ID, &r.Name, &r.Description, &r.StepsJSON, &r.Cwd, &r.OnSuccessJSON, &r.FilesJSON, &r.WorkspaceProfile, &r.CreatedAt, &r.UpdatedAt)
	if err != nil {
		return r, false
	}
	return r, true
}

// ListChains returns chains visible for the given profile: profile-owned chains
// plus global chains (workspace_profile=""). Pass "" to return all chains.
func (db *DB) ListChains(profile string) ([]ChainRow, error) {
	var (
		rows *sql.Rows
		err  error
	)
	if profile == "" {
		rows, err = db.conn.Query(`
			SELECT id, name, description, steps_json, cwd, on_success_json, files_json, workspace_profile, created_at, updated_at
			FROM chains ORDER BY name`)
	} else {
		rows, err = db.conn.Query(`
			SELECT id, name, description, steps_json, cwd, on_success_json, files_json, workspace_profile, created_at, updated_at
			FROM chains WHERE workspace_profile = '' OR workspace_profile = ?
			ORDER BY name`, profile)
	}
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []ChainRow
	for rows.Next() {
		var r ChainRow
		if err := rows.Scan(&r.ID, &r.Name, &r.Description, &r.StepsJSON, &r.Cwd, &r.OnSuccessJSON, &r.FilesJSON, &r.WorkspaceProfile, &r.CreatedAt, &r.UpdatedAt); err != nil {
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
// Tasks (queue)
// ---------------------------------------------------------------------------

// TaskRow mirrors the tasks table. The runtime Task type lives in queue.go.
// WorkspaceProfile is snapshotted at enqueue time so the task always runs
// under the right profile context — see feedback_profiles_foundational.md.
type TaskRow struct {
	ID               string `json:"id"`
	ChainID          string `json:"chain_id"`
	Status           string `json:"status"`
	Priority         int    `json:"priority"`
	Input            string `json:"input"`
	Cwd              string `json:"cwd"`
	Trigger          string `json:"trigger"`
	ParentTaskID     string `json:"parent_task_id"`
	WorkspaceProfile string `json:"workspace_profile"`
	EnqueuedAt       string `json:"enqueued_at"`
	ScheduledFor     string `json:"scheduled_for"`
	StartedAt        string `json:"started_at"`
	FinishedAt       string `json:"finished_at"`
	Attempts         int    `json:"attempts"`
	MaxAttempts      int    `json:"max_attempts"`
	LastError        string `json:"last_error"`
	ResultRunID      string `json:"result_run_id"`
}

func (db *DB) InsertTask(t TaskRow) error {
	_, err := db.conn.Exec(`
		INSERT INTO tasks (
			id, chain_id, status, priority, input, cwd, trigger, parent_task_id, workspace_profile,
			enqueued_at, scheduled_for, started_at, finished_at,
			attempts, max_attempts, last_error, result_run_id
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		t.ID, t.ChainID, t.Status, t.Priority, t.Input, t.Cwd, t.Trigger, t.ParentTaskID, t.WorkspaceProfile,
		t.EnqueuedAt, t.ScheduledFor, t.StartedAt, t.FinishedAt,
		t.Attempts, t.MaxAttempts, t.LastError, t.ResultRunID)
	return err
}

// ClaimNextTask atomically transitions the highest-priority eligible pending
// task to "running" and returns it. Returns (TaskRow{}, false) when nothing
// is ready. "Eligible" means status='pending' AND (scheduled_for='' OR
// scheduled_for <= nowRFC3339).
func (db *DB) ClaimNextTask(nowRFC3339 string) (TaskRow, bool) {
	tx, err := db.conn.Begin()
	if err != nil {
		return TaskRow{}, false
	}
	defer tx.Rollback()

	var t TaskRow
	err = tx.QueryRow(`
		SELECT id, chain_id, status, priority, input, cwd, trigger, parent_task_id, workspace_profile,
		       enqueued_at, scheduled_for, started_at, finished_at,
		       attempts, max_attempts, last_error, result_run_id
		FROM tasks
		WHERE status = 'pending'
		  AND (scheduled_for = '' OR scheduled_for <= ?)
		ORDER BY priority DESC, enqueued_at ASC
		LIMIT 1`, nowRFC3339).Scan(
		&t.ID, &t.ChainID, &t.Status, &t.Priority, &t.Input, &t.Cwd, &t.Trigger, &t.ParentTaskID, &t.WorkspaceProfile,
		&t.EnqueuedAt, &t.ScheduledFor, &t.StartedAt, &t.FinishedAt,
		&t.Attempts, &t.MaxAttempts, &t.LastError, &t.ResultRunID)
	if err != nil {
		return TaskRow{}, false
	}

	if _, err := tx.Exec(
		`UPDATE tasks SET status = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ?`,
		nowRFC3339, t.ID); err != nil {
		return TaskRow{}, false
	}
	if err := tx.Commit(); err != nil {
		return TaskRow{}, false
	}
	t.Status = "running"
	t.StartedAt = nowRFC3339
	t.Attempts++
	return t, true
}

func (db *DB) MarkTaskSucceeded(id, finishedAt, runID string) error {
	_, err := db.conn.Exec(
		`UPDATE tasks SET status = 'succeeded', finished_at = ?, result_run_id = ?, last_error = '' WHERE id = ?`,
		finishedAt, runID, id)
	return err
}

// MarkTaskFailed records a failure. If attempts < max_attempts the task is
// requeued (status reset to pending) with scheduled_for set for backoff.
// Returns true when the task is requeued.
func (db *DB) MarkTaskFailed(id, finishedAt, runID, errMsg, retryAt string) (requeued bool, err error) {
	tx, err := db.conn.Begin()
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	var attempts, maxAttempts int
	if err := tx.QueryRow(`SELECT attempts, max_attempts FROM tasks WHERE id = ?`, id).Scan(&attempts, &maxAttempts); err != nil {
		return false, err
	}
	if attempts < maxAttempts && retryAt != "" {
		if _, err := tx.Exec(
			`UPDATE tasks SET status = 'pending', scheduled_for = ?, last_error = ?, started_at = '' WHERE id = ?`,
			retryAt, errMsg, id); err != nil {
			return false, err
		}
		requeued = true
	} else {
		if _, err := tx.Exec(
			`UPDATE tasks SET status = 'failed', finished_at = ?, result_run_id = ?, last_error = ? WHERE id = ?`,
			finishedAt, runID, errMsg, id); err != nil {
			return false, err
		}
	}
	return requeued, tx.Commit()
}

func (db *DB) CancelTask(id string) error {
	res, err := db.conn.Exec(
		`UPDATE tasks SET status = 'cancelled', finished_at = ? WHERE id = ? AND status IN ('pending', 'running')`,
		time.Now().UTC().Format(time.RFC3339), id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return fmt.Errorf("task %q not pending or running", id)
	}
	return nil
}

func (db *DB) RetryTask(id string) error {
	_, err := db.conn.Exec(
		`UPDATE tasks SET status = 'pending', started_at = '', finished_at = '', last_error = '', scheduled_for = '', attempts = 0 WHERE id = ? AND status IN ('failed', 'cancelled')`,
		id)
	return err
}

func (db *DB) GetTask(id string) (TaskRow, bool) {
	var t TaskRow
	err := db.conn.QueryRow(`
		SELECT id, chain_id, status, priority, input, cwd, trigger, parent_task_id, workspace_profile,
		       enqueued_at, scheduled_for, started_at, finished_at,
		       attempts, max_attempts, last_error, result_run_id
		FROM tasks WHERE id = ?`, id).Scan(
		&t.ID, &t.ChainID, &t.Status, &t.Priority, &t.Input, &t.Cwd, &t.Trigger, &t.ParentTaskID, &t.WorkspaceProfile,
		&t.EnqueuedAt, &t.ScheduledFor, &t.StartedAt, &t.FinishedAt,
		&t.Attempts, &t.MaxAttempts, &t.LastError, &t.ResultRunID)
	if err != nil {
		return t, false
	}
	return t, true
}

// ListTasks returns tasks filtered by status (empty = all) and workspace
// (empty = all). Limit is capped at 200 to keep the UI snappy.
func (db *DB) ListTasks(status, workspace string, limit int) ([]TaskRow, error) {
	if limit <= 0 || limit > 200 {
		limit = 200
	}
	const selectCols = `id, chain_id, status, priority, input, cwd, trigger, parent_task_id, workspace_profile,
	       enqueued_at, scheduled_for, started_at, finished_at,
	       attempts, max_attempts, last_error, result_run_id`
	var (
		where  []string
		args   []any
	)
	if status != "" {
		where = append(where, "status = ?")
		args = append(args, status)
	}
	if workspace != "" {
		where = append(where, "workspace_profile = ?")
		args = append(args, workspace)
	}
	q := "SELECT " + selectCols + " FROM tasks"
	if len(where) > 0 {
		q += " WHERE " + strings.Join(where, " AND ")
	}
	q += " ORDER BY enqueued_at DESC LIMIT ?"
	args = append(args, limit)
	rows, err := db.conn.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []TaskRow
	for rows.Next() {
		var t TaskRow
		if err := rows.Scan(
			&t.ID, &t.ChainID, &t.Status, &t.Priority, &t.Input, &t.Cwd, &t.Trigger, &t.ParentTaskID, &t.WorkspaceProfile,
			&t.EnqueuedAt, &t.ScheduledFor, &t.StartedAt, &t.FinishedAt,
			&t.Attempts, &t.MaxAttempts, &t.LastError, &t.ResultRunID,
		); err != nil {
			continue
		}
		out = append(out, t)
	}
	return out, nil
}

// TaskStatsCounted returns task counts by status. Active tasks (pending/running)
// are always included; terminal tasks (succeeded/failed/cancelled) are filtered
// to those finished at or after `since` (RFC3339).
func (db *DB) TaskStatsCounted(since string) (pending, running, succeeded, failed, cancelled int, err error) {
	// Active tasks — not time-scoped
	rows, e := db.conn.Query(
		"SELECT status, COUNT(*) FROM tasks WHERE status IN ('pending','running') GROUP BY status")
	if e != nil {
		err = fmt.Errorf("count active tasks: %w", e)
		return
	}
	defer rows.Close()
	for rows.Next() {
		var status string
		var count int
		if rows.Scan(&status, &count) != nil {
			continue
		}
		switch status {
		case "pending":
			pending = count
		case "running":
			running = count
		}
	}
	rows.Close()

	// Terminal tasks within time window
	rows2, e := db.conn.Query(
		"SELECT status, COUNT(*) FROM tasks WHERE status IN ('succeeded','failed','cancelled') AND finished_at >= ? GROUP BY status",
		since)
	if e != nil {
		err = fmt.Errorf("count terminal tasks: %w", e)
		return
	}
	defer rows2.Close()
	for rows2.Next() {
		var status string
		var count int
		if rows2.Scan(&status, &count) != nil {
			continue
		}
		switch status {
		case "succeeded":
			succeeded = count
		case "failed":
			failed = count
		case "cancelled":
			cancelled = count
		}
	}
	return
}

// ---------------------------------------------------------------------------
// Schedules (cron)
// ---------------------------------------------------------------------------

type ScheduleRow struct {
	ID               string `json:"id"`
	ChainID          string `json:"chain_id"`
	CronExpr         string `json:"cron_expr"`
	Input            string `json:"input"`
	Cwd              string `json:"cwd"`
	Enabled          bool   `json:"enabled"`
	WorkspaceProfile string `json:"workspace_profile"`
	CreatedAt        string `json:"created_at"`
	UpdatedAt        string `json:"updated_at"`
	LastFiredAt      string `json:"last_fired_at"`
	// NextFireAt is a computed convenience populated by the bindings (not the
	// table). RFC3339 UTC. Empty when the cron expression is invalid or the
	// schedule is disabled.
	NextFireAt string `json:"next_fire_at"`
}

func (db *DB) UpsertSchedule(s ScheduleRow) error {
	_, err := db.conn.Exec(`
		INSERT INTO schedules (id, chain_id, cron_expr, input, cwd, enabled, workspace_profile, created_at, updated_at, last_fired_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			chain_id          = excluded.chain_id,
			cron_expr         = excluded.cron_expr,
			input             = excluded.input,
			cwd               = excluded.cwd,
			enabled           = excluded.enabled,
			workspace_profile = excluded.workspace_profile,
			updated_at        = excluded.updated_at`,
		s.ID, s.ChainID, s.CronExpr, s.Input, s.Cwd, boolToInt(s.Enabled), s.WorkspaceProfile,
		s.CreatedAt, s.UpdatedAt, s.LastFiredAt)
	return err
}

func (db *DB) GetSchedule(id string) (ScheduleRow, bool) {
	var r ScheduleRow
	var enabled int
	err := db.conn.QueryRow(`
		SELECT id, chain_id, cron_expr, input, cwd, enabled, workspace_profile, created_at, updated_at, last_fired_at
		FROM schedules WHERE id = ?`, id).Scan(
		&r.ID, &r.ChainID, &r.CronExpr, &r.Input, &r.Cwd, &enabled, &r.WorkspaceProfile,
		&r.CreatedAt, &r.UpdatedAt, &r.LastFiredAt)
	if err != nil {
		return r, false
	}
	r.Enabled = enabled != 0
	return r, true
}

func (db *DB) ListSchedules(chainID string) ([]ScheduleRow, error) {
	var rows *sql.Rows
	var err error
	if chainID == "" {
		rows, err = db.conn.Query(`
			SELECT id, chain_id, cron_expr, input, cwd, enabled, workspace_profile, created_at, updated_at, last_fired_at
			FROM schedules ORDER BY created_at`)
	} else {
		rows, err = db.conn.Query(`
			SELECT id, chain_id, cron_expr, input, cwd, enabled, workspace_profile, created_at, updated_at, last_fired_at
			FROM schedules WHERE chain_id = ? ORDER BY created_at`, chainID)
	}
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []ScheduleRow
	for rows.Next() {
		var r ScheduleRow
		var enabled int
		if err := rows.Scan(&r.ID, &r.ChainID, &r.CronExpr, &r.Input, &r.Cwd, &enabled, &r.WorkspaceProfile,
			&r.CreatedAt, &r.UpdatedAt, &r.LastFiredAt); err != nil {
			continue
		}
		r.Enabled = enabled != 0
		out = append(out, r)
	}
	return out, nil
}

func (db *DB) DeleteSchedule(id string) error {
	_, err := db.conn.Exec("DELETE FROM schedules WHERE id = ?", id)
	return err
}

func (db *DB) MarkScheduleFired(id, firedAt string) error {
	_, err := db.conn.Exec("UPDATE schedules SET last_fired_at = ? WHERE id = ?", firedAt, id)
	return err
}

// ---------------------------------------------------------------------------
// Profile images
// ---------------------------------------------------------------------------

// ProfileImageRow stores registry metadata for a built profile image.
type ProfileImageRow struct {
	Profile      string `json:"profile"`
	RegistryURL  string `json:"registry_url"`
	LastPushedAt string `json:"last_pushed_at"`
	LastDigest   string `json:"last_digest"`
	EnvKey       string `json:"env_key,omitempty"` // AES key for decrypting .env.enc at runtime
}

// UpsertProfileImage inserts or replaces a profile image record.
func (db *DB) UpsertProfileImage(r ProfileImageRow) error {
	_, err := db.conn.Exec(
		`INSERT INTO profile_images (profile, registry_url, last_pushed_at, last_digest, env_key)
		 VALUES (?, ?, ?, ?, ?)
		 ON CONFLICT(profile) DO UPDATE SET
		   registry_url   = excluded.registry_url,
		   last_pushed_at = excluded.last_pushed_at,
		   last_digest    = excluded.last_digest,
		   env_key        = excluded.env_key`,
		r.Profile, r.RegistryURL, r.LastPushedAt, r.LastDigest, r.EnvKey,
	)
	return err
}

// GetProfileImage returns the image record for a profile, if present.
func (db *DB) GetProfileImage(profile string) (ProfileImageRow, bool) {
	var r ProfileImageRow
	err := db.conn.QueryRow(
		`SELECT profile, registry_url, last_pushed_at, last_digest, env_key FROM profile_images WHERE profile = ?`,
		profile,
	).Scan(&r.Profile, &r.RegistryURL, &r.LastPushedAt, &r.LastDigest, &r.EnvKey)
	if err != nil {
		return ProfileImageRow{}, false
	}
	return r, true
}

// ListProfileImages returns all profile image records.
func (db *DB) ListProfileImages() []ProfileImageRow {
	rows, err := db.conn.Query(
		`SELECT profile, registry_url, last_pushed_at, last_digest, env_key FROM profile_images ORDER BY profile`,
	)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []ProfileImageRow
	for rows.Next() {
		var r ProfileImageRow
		if err := rows.Scan(&r.Profile, &r.RegistryURL, &r.LastPushedAt, &r.LastDigest, &r.EnvKey); err != nil {
			continue
		}
		out = append(out, r)
	}
	return out
}

// DeleteProfileImage removes a profile image record.
func (db *DB) DeleteProfileImage(profile string) error {
	_, err := db.conn.Exec("DELETE FROM profile_images WHERE profile = ?", profile)
	return err
}

// ---------------------------------------------------------------------------
// Automation rules
// ---------------------------------------------------------------------------

func (db *DB) ListEnabledAutomationRules(profile string) ([]AutomationRule, error) {
	// Returns rules matching the given profile OR global rules (profile='').
	q := `SELECT id, profile, name, description, enabled, trigger_type, trigger_config,
	             action_type, action_config, created_at, last_triggered_at, trigger_count
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
	q := `SELECT id, profile, name, description, enabled, trigger_type, trigger_config,
	             action_type, action_config, created_at, last_triggered_at, trigger_count
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
	var r AutomationRule
	var enabled int
	var lastTriggered sql.NullInt64
	err := db.conn.QueryRow(`
		SELECT id, profile, name, description, enabled, trigger_type, trigger_config,
		       action_type, action_config, created_at, last_triggered_at, trigger_count
		FROM automation_rules WHERE id = ?`, id).
		Scan(&r.ID, &r.Profile, &r.Name, &r.Description, &enabled,
			&r.TriggerType, &r.TriggerConfig, &r.ActionType, &r.ActionConfig,
			&r.CreatedAt, &lastTriggered, &r.TriggerCount)
	if err != nil {
		return AutomationRule{}, false
	}
	r.Enabled = enabled != 0
	if lastTriggered.Valid {
		r.LastTriggeredAt = &lastTriggered.Int64
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

func scanAutomationRules(rows *sql.Rows) ([]AutomationRule, error) {
	var out []AutomationRule
	for rows.Next() {
		var r AutomationRule
		var enabled int
		var lastTriggered sql.NullInt64
		if err := rows.Scan(&r.ID, &r.Profile, &r.Name, &r.Description, &enabled,
			&r.TriggerType, &r.TriggerConfig, &r.ActionType, &r.ActionConfig,
			&r.CreatedAt, &lastTriggered, &r.TriggerCount); err != nil {
			return nil, err
		}
		r.Enabled = enabled != 0
		if lastTriggered.Valid {
			r.LastTriggeredAt = &lastTriggered.Int64
		}
		out = append(out, r)
	}
	return out, rows.Err()
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
