package main

import (
	"database/sql"
	"testing"

	_ "modernc.org/sqlite"
)

// v30 retires four tables no code has ever read or written, and adds the column
// that makes importing local schedules to the server idempotent.
//
// The tables are RENAMED, not dropped: migrations here have no `down`, so a
// DROP is irreversible for every user who runs the build, and `tasks` could
// still hold rows from an old version.

func migratedDB(t *testing.T) *DB {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	db := &DB{conn: conn}
	if err := db.migrate(); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	return db
}

func tableExists(t *testing.T, db *DB, name string) bool {
	t.Helper()
	var got string
	err := db.conn.QueryRow(
		"SELECT name FROM sqlite_master WHERE type='table' AND name=?", name,
	).Scan(&got)
	return err == nil && got == name
}

func TestV30RetiresDeadTables(t *testing.T) {
	db := migratedDB(t)

	for _, dead := range []string{"tasks", "schedules", "loops", "loop_runs"} {
		if tableExists(t, db, dead) {
			t.Errorf("table %q should have been retired by v30", dead)
		}
		if !tableExists(t, db, dead+"_retired_v30") {
			t.Errorf("table %q should have been renamed, not dropped", dead)
		}
	}
}

// The live tables must be untouched — this migration retires dead schema, and
// a rename that caught a real table would be silent data loss.
func TestV30LeavesLiveTablesAlone(t *testing.T) {
	db := migratedDB(t)

	for _, live := range []string{"collect_jobs", "collected_entries", "settings"} {
		if !tableExists(t, db, live) {
			t.Errorf("live table %q went missing", live)
		}
		if tableExists(t, db, live+"_retired_v30") {
			t.Errorf("live table %q was wrongly retired", live)
		}
	}
}

func TestV30AddsImportedScheduleID(t *testing.T) {
	db := migratedDB(t)

	if _, err := db.conn.Exec(
		`INSERT INTO collect_jobs (id, profile, name, command, interval_s, enabled,
		   default_actions, last_error, created_at, imported_schedule_id)
		 VALUES ('j1','personal','n','echo',60,1,'[]','',0,'sched-123')`,
	); err != nil {
		t.Fatalf("insert with imported_schedule_id: %v", err)
	}

	var got string
	if err := db.conn.QueryRow(
		"SELECT imported_schedule_id FROM collect_jobs WHERE id='j1'",
	).Scan(&got); err != nil {
		t.Fatalf("read back: %v", err)
	}
	if got != "sched-123" {
		t.Fatalf("imported_schedule_id = %q, want %q", got, "sched-123")
	}
}

// Existing rows must default to "" (not imported) rather than NULL, so the
// import's "already carried over?" check is a plain string compare.
func TestV30ImportedScheduleIDDefaultsEmpty(t *testing.T) {
	db := migratedDB(t)

	if _, err := db.conn.Exec(
		`INSERT INTO collect_jobs (id, profile, name, command, interval_s, enabled,
		   default_actions, last_error, created_at)
		 VALUES ('j2','personal','n','echo',60,1,'[]','',0)`,
	); err != nil {
		t.Fatalf("insert: %v", err)
	}

	var got string
	if err := db.conn.QueryRow(
		"SELECT imported_schedule_id FROM collect_jobs WHERE id='j2'",
	).Scan(&got); err != nil {
		t.Fatalf("read back: %v", err)
	}
	if got != "" {
		t.Fatalf("default imported_schedule_id = %q, want empty", got)
	}
}

// Migrations are keyed by a version SET, not a max, so running them twice must
// be a no-op rather than a second rename attempt.
func TestV30IsIdempotent(t *testing.T) {
	db := migratedDB(t)
	if err := db.migrate(); err != nil {
		t.Fatalf("second migrate: %v", err)
	}
	if !tableExists(t, db, "tasks_retired_v30") {
		t.Fatal("retired table vanished on a second migrate")
	}
}
