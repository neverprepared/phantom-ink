package main

import (
	"database/sql"
	"fmt"
	"sort"
)

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
	// (Historical table names "chains"/"chain_runs" preserved here so this
	// migration is a no-op on existing DBs. The Chain → Loop rename of the
	// SQL schema is applied by ALTER TABLE in v24 below; a later code-level
	// Loop → Sequence rename leaves the SQL names alone.)
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
	// (Historical column "chain_id" preserved; renamed by ALTER below.)
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
	// (Historical column "chain_id" preserved; renamed by ALTER below.)
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
	// v22: outbox_events — durable queue of agent-event-bus envelopes pending
	// delivery to brainbox. Survives brainbox restarts and network blips; the
	// outbox flush loop drains it with exponential backoff.
	{version: 22, sql: `
		CREATE TABLE IF NOT EXISTS outbox_events (
			rowid           INTEGER PRIMARY KEY AUTOINCREMENT,
			envelope_id     TEXT    NOT NULL,
			envelope_json   TEXT    NOT NULL,
			created_at      INTEGER NOT NULL,
			attempts        INTEGER NOT NULL DEFAULT 0,
			next_attempt_at INTEGER NOT NULL DEFAULT 0,
			last_error      TEXT    NOT NULL DEFAULT ''
		);
		CREATE INDEX IF NOT EXISTS idx_outbox_eligible
			ON outbox_events(next_attempt_at);
	`},
	// v23: attention_replies — overlay for envelope-id-keyed user replies.
	// Replaces the user_reply column on attention_items now that attention
	// reads from the bus (P5). Local table because replies are UI-state that
	// doesn't need to round-trip to brainbox.
	{version: 23, sql: `
		CREATE TABLE IF NOT EXISTS attention_replies (
			id         TEXT PRIMARY KEY,
			reply      TEXT NOT NULL,
			replied_at INTEGER NOT NULL
		);
	`},
	// v24: Chain → Loop rename. Renames the chains, chain_runs tables and
	// the chain_id columns on tasks and schedules so the storage layer matches
	// the renamed Go types. Pure rename — no data shape changes, no data loss.
	// Fresh installs hit v5–v17 first (creating chains/chain_runs/chain_id),
	// then this migration renames them; existing installs at v23 just run
	// this. ALTER TABLE RENAME and ALTER TABLE RENAME COLUMN are both
	// supported by modernc.org/sqlite per SQLite >= 3.25.
	//
	// Historical note: a later code rename (Loop → Sequence) keeps the Go
	// identifiers in sync with the user-facing label, but the SQL tables
	// stay as `loops` / `loop_runs` (and the foreign-key columns stay as
	// `loop_id`) to avoid a third migration on the same tables. The Go
	// SequenceRow / SequenceRunRow types deliberately reference these
	// historical SQL names.
	{version: 24, fn: func(conn *sql.DB) error {
		// Order matters: rename the chain_id column on chain_runs BEFORE
		// renaming the table, otherwise the column-rename statement runs
		// against a name that no longer exists.
		stmts := []string{
			"ALTER TABLE chain_runs RENAME COLUMN chain_id TO loop_id",
			"ALTER TABLE chains RENAME TO loops",
			"ALTER TABLE chain_runs RENAME TO loop_runs",
			"ALTER TABLE tasks RENAME COLUMN chain_id TO loop_id",
			"ALTER TABLE schedules RENAME COLUMN chain_id TO loop_id",
			"DROP INDEX IF EXISTS idx_chain_runs_chain_id",
			"CREATE INDEX IF NOT EXISTS idx_loop_runs_loop_id ON loop_runs(loop_id)",
			"DROP INDEX IF EXISTS idx_tasks_chain_id",
			"CREATE INDEX IF NOT EXISTS idx_tasks_loop_id ON tasks(loop_id)",
			"DROP INDEX IF EXISTS idx_schedules_chain_id",
			"CREATE INDEX IF NOT EXISTS idx_schedules_loop_id ON schedules(loop_id)",
		}
		for _, s := range stmts {
			if _, err := conn.Exec(s); err != nil {
				return fmt.Errorf("v24 %q: %w", s, err)
			}
		}
		return nil
	}},
	// v25: per-profile credential-bundle source selection. Catalog sources
	// store just the toggle; custom sources also carry their operator-authored
	// definition JSON ({globs, audience, env_map}).
	{version: 25, sql: `
		CREATE TABLE IF NOT EXISTS profile_bundle_sources (
			profile    TEXT NOT NULL,
			name       TEXT NOT NULL,
			kind       TEXT NOT NULL DEFAULT 'catalog',
			enabled    INTEGER NOT NULL DEFAULT 0,
			definition TEXT NOT NULL DEFAULT '{}',
			PRIMARY KEY (profile, name)
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
