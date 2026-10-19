package main

import (
	"database/sql"
	"testing"

	_ "modernc.org/sqlite"
)

// openMigrationTestDB returns an empty in-memory DB with only the version table,
// matching what migrate() sets up before it applies anything.
func openMigrationTestDB(t *testing.T) *DB {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	if _, err := conn.Exec(`CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)`); err != nil {
		t.Fatalf("create schema_version: %v", err)
	}
	return &DB{conn: conn}
}

// Every migration version must be unique. Two steps sharing a number means one
// of them is skipped forever on any DB that recorded the other.
func TestMigrationVersionsAreUnique(t *testing.T) {
	seen := make(map[int]int)
	for i, m := range migrations {
		if prev, dup := seen[m.version]; dup {
			t.Errorf("migrations[%d] and migrations[%d] share version %d", prev, i, m.version)
		}
		seen[m.version] = i
	}
}

// Each migration must carry exactly one of fn or sql, or the applier silently
// records a version having done nothing.
func TestMigrationsHaveExactlyOneBody(t *testing.T) {
	for _, m := range migrations {
		hasFn, hasSQL := m.fn != nil, m.sql != ""
		if hasFn == hasSQL {
			t.Errorf("migration v%d: want exactly one of fn/sql, got fn=%v sql=%v", m.version, hasFn, hasSQL)
		}
	}
}

// The applier must gate on the SET of applied versions, not on MAX(version).
// A migration added later with a number below the current max was previously
// unreachable: this is the regression that left DBs missing a table while
// schema_version claimed otherwise.
func TestMigrateAppliesVersionBelowHighWatermark(t *testing.T) {
	db := openMigrationTestDB(t)

	// A DB that has already recorded a HIGHER version than the step we add.
	if _, err := db.conn.Exec("INSERT INTO schema_version (version) VALUES (50)"); err != nil {
		t.Fatalf("seed: %v", err)
	}

	back := migration{version: 7, sql: `CREATE TABLE late_addition (id INTEGER)`}
	if err := db.applyMigration(back); err != nil {
		t.Fatalf("applyMigration: %v", err)
	}

	applied, err := db.appliedVersions()
	if err != nil {
		t.Fatalf("appliedVersions: %v", err)
	}
	if !applied[7] {
		t.Error("v7 not recorded as applied")
	}
	if !applied[50] {
		t.Error("pre-existing v50 lost")
	}
	// And the table it created is really there.
	if _, err := db.conn.Exec("INSERT INTO late_addition (id) VALUES (1)"); err != nil {
		t.Errorf("back-numbered migration recorded but not applied: %v", err)
	}
}

// A step that fails partway must leave NOTHING behind -- neither the half-applied
// schema change nor a recorded version.
func TestApplyMigrationRollsBackOnFailure(t *testing.T) {
	db := openMigrationTestDB(t)

	bad := migration{version: 3, fn: func(conn migrationConn) error {
		if _, err := conn.Exec(`CREATE TABLE first_half (id INTEGER)`); err != nil {
			return err
		}
		// Second statement fails: the first must not survive.
		_, err := conn.Exec(`CREATE TABLE "" (broken`)
		return err
	}}

	if err := db.applyMigration(bad); err == nil {
		t.Fatal("expected the failing migration to return an error")
	}

	applied, err := db.appliedVersions()
	if err != nil {
		t.Fatalf("appliedVersions: %v", err)
	}
	if applied[3] {
		t.Error("failed migration recorded itself as applied")
	}
	var n int
	if err := db.conn.QueryRow(
		`SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='first_half'`,
	).Scan(&n); err != nil {
		t.Fatalf("inspect: %v", err)
	}
	if n != 0 {
		t.Error("first statement of a failed migration survived; the step was not atomic")
	}
}

// A version already recorded must not be re-applied.
func TestAppliedVersionsIsASet(t *testing.T) {
	db := openMigrationTestDB(t)
	for _, v := range []int{1, 1, 4, 9} {
		if _, err := db.conn.Exec("INSERT INTO schema_version (version) VALUES (?)", v); err != nil {
			t.Fatalf("seed: %v", err)
		}
	}
	applied, err := db.appliedVersions()
	if err != nil {
		t.Fatalf("appliedVersions: %v", err)
	}
	for _, v := range []int{1, 4, 9} {
		if !applied[v] {
			t.Errorf("v%d missing from applied set", v)
		}
	}
	if applied[2] {
		t.Error("v2 reported applied but was never recorded")
	}
}
