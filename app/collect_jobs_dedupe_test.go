package main

import (
	"database/sql"
	"testing"

	_ "modernc.org/sqlite"
)

// The schema here used to be a hand-written copy of the collect_jobs DDL, which
// silently drifted from db_migrations.go every time a column was added (it broke
// on imported_schedule_id in v30). Run the real migrations instead, so these
// tests exercise the schema the app actually ships.

func newTestDB(t *testing.T) *DB {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	conn.SetMaxOpenConns(1)
	t.Cleanup(func() { conn.Close() })
	db := &DB{conn: conn}
	if err := db.migrate(); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	return db
}

func insertJob(t *testing.T, db *DB, id, profile, name, owner string, createdAt int64) {
	t.Helper()
	_, err := db.conn.Exec(
		`INSERT INTO collect_jobs (id, profile, name, command, created_at, source, owner_widget_id)
		 VALUES (?, ?, ?, 'cmd', ?, 'widget', ?)`,
		id, profile, name, createdAt, owner)
	if err != nil {
		t.Fatalf("insert %s: %v", id, err)
	}
}

func jobIDs(t *testing.T, db *DB, profile string) []string {
	t.Helper()
	jobs, err := db.ListCollectJobs(profile)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	ids := make([]string, len(jobs))
	for i, j := range jobs {
		ids[i] = j.ID
	}
	return ids
}

func TestDedupeWidgetJobs_KeepsOldestPerOwner(t *testing.T) {
	db := newTestDB(t)
	// Same widget (owner W) raced and created three rows; oldest is j1.
	insertJob(t, db, "j1", "gsa", "JIRA", "W", 100)
	insertJob(t, db, "j2", "gsa", "JIRA", "W", 200)
	insertJob(t, db, "j3", "gsa", "JIRA", "W", 300)
	// A different widget's job must survive untouched.
	insertJob(t, db, "k1", "gsa", "OTHER", "V", 150)

	n, err := db.DedupeWidgetJobs()
	if err != nil {
		t.Fatal(err)
	}
	if n != 2 {
		t.Fatalf("removed %d, want 2", n)
	}
	if _, ok := db.GetCollectJob("j1"); !ok {
		t.Error("oldest (j1) should survive")
	}
	if _, ok := db.GetCollectJob("k1"); !ok {
		t.Error("other widget's job (k1) should survive")
	}
	for _, gone := range []string{"j2", "j3"} {
		if _, ok := db.GetCollectJob(gone); ok {
			t.Errorf("%s should have been removed", gone)
		}
	}
}

func TestPruneOrphanWidgetJobs_DropsJobsNotInLayout(t *testing.T) {
	db := newTestDB(t)
	// Saved layout for gsa references only widget "live".
	if _, err := db.conn.Exec(
		`INSERT INTO settings (key, value) VALUES ('dashboard_layout:gsa', ?)`,
		`{"version":1,"widgets":[{"id":"live","kind":"script-metric"}]}`); err != nil {
		t.Fatal(err)
	}
	insertJob(t, db, "keep", "gsa", "JIRA", "live", 100)    // owner in layout
	insertJob(t, db, "orph1", "gsa", "JIRA", "ghost1", 200) // orphan
	insertJob(t, db, "orph2", "gsa", "JIRA", "ghost2", 300) // orphan
	// A profile with NO saved layout must be left untouched.
	insertJob(t, db, "safe", "lakeview", "JIRA", "whatever", 100)

	n, err := db.PruneOrphanWidgetJobs()
	if err != nil {
		t.Fatal(err)
	}
	if n != 2 {
		t.Fatalf("removed %d, want 2", n)
	}
	if _, ok := db.GetCollectJob("keep"); !ok {
		t.Error("job whose owner is in the layout should survive")
	}
	if _, ok := db.GetCollectJob("safe"); !ok {
		t.Error("job for a profile with no saved layout must be left alone")
	}
	for _, gone := range []string{"orph1", "orph2"} {
		if _, ok := db.GetCollectJob(gone); ok {
			t.Errorf("orphan %s should have been removed", gone)
		}
	}
}

func TestSaveCollectJob_IdempotentPerOwner(t *testing.T) {
	db := newTestDB(t)
	a := &App{db: db}

	base := CollectJob{
		Profile: "gsa", Name: "JIRA", Command: "cmd",
		Source: "widget", OwnerWidgetID: "W", Enabled: true,
	}
	first, err := a.SaveCollectJob(base)
	if err != nil {
		t.Fatal(err)
	}
	// A second create-shaped call (empty id) for the SAME owner must reuse
	// the first job's id, not spawn a duplicate.
	second, err := a.SaveCollectJob(base)
	if err != nil {
		t.Fatal(err)
	}
	if first.ID != second.ID {
		t.Errorf("second registration got a new id %s (want reuse of %s)", second.ID, first.ID)
	}
	if ids := jobIDs(t, db, "gsa"); len(ids) != 1 {
		t.Errorf("expected exactly 1 job for the widget, got %d: %v", len(ids), ids)
	}
}

func TestDeleteCollectJobsByOwner(t *testing.T) {
	db := newTestDB(t)
	a := &App{db: db}
	insertJob(t, db, "a", "gsa", "JIRA", "W", 100)
	insertJob(t, db, "b", "gsa", "JIRA", "W", 200)
	insertJob(t, db, "c", "gsa", "OTHER", "V", 100)

	n, err := a.DeleteCollectJobsByOwner("gsa", "W")
	if err != nil {
		t.Fatal(err)
	}
	if n != 2 {
		t.Fatalf("deleted %d, want 2", n)
	}
	if _, ok := db.GetCollectJob("c"); !ok {
		t.Error("job for other owner must survive")
	}
}
