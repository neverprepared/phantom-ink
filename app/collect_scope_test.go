package main

import (
	"database/sql"
	"testing"
	"time"

	_ "modernc.org/sqlite"
)

// The local collect scheduler is now WIDGET-ONLY. User-created jobs (source="")
// moved to the server (phantom-events /api/schedules), where they fire with the
// app closed and cannot be double-fired by a second running instance.
//
// These tests exist because nothing previously asserted which jobs tick() picks
// up, in either direction — so the cutover could have silently kept running
// user jobs locally (double-firing against the server) or silently stopped
// running widget jobs, and the whole suite would still have been green.

func scopeTestDB(t *testing.T) *DB {
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

// seedDueJob inserts an enabled job that is due right now (interval cadence
// with no last_run_at fires immediately).
func seedDueJob(t *testing.T, db *DB, id, source, owner string) {
	t.Helper()
	_, err := db.conn.Exec(
		`INSERT INTO collect_jobs (id, profile, name, command, interval_s, enabled,
		   default_actions, last_error, created_at, source, owner_widget_id, target_type)
		 VALUES (?, 'personal', ?, 'echo hi', 60, 1, '[]', '', 0, ?, ?, 'shell')`,
		id, "job-"+id, source, owner)
	if err != nil {
		t.Fatalf("seed %s: %v", id, err)
	}
}

// dueJobIDs reports which jobs tick() would actually run, by applying the same
// filters tick() does without executing any subprocess.
func dueJobIDs(t *testing.T, db *DB) map[string]bool {
	t.Helper()
	jobs, err := db.ListCollectJobs("")
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	now := time.Now()
	out := map[string]bool{}
	for _, j := range jobs {
		if !j.Enabled || !collectSchedulerOwns(j) || !collectJobIsDue(j, now) {
			continue
		}
		out[j.ID] = true
	}
	return out
}

// A user-created job must NOT run locally any more. If it did, it would run
// twice — once here with local env, once on the server — and the two would
// race to write the same entries.
func TestLocalSchedulerSkipsUserCreatedJobs(t *testing.T) {
	db := scopeTestDB(t)
	seedDueJob(t, db, "user-job", "", "")

	if dueJobIDs(t, db)["user-job"] {
		t.Fatal("local scheduler picked up a source='' job; those belong to the server now")
	}
}

// Widget-owned jobs stay local: a dashboard widget polling a metric while the
// app is open is not an infrastructure concern, and nothing needs it to fire
// when the app is closed.
func TestLocalSchedulerStillRunsWidgetJobs(t *testing.T) {
	db := scopeTestDB(t)
	seedDueJob(t, db, "widget-job", "widget", "w1")

	if !dueJobIDs(t, db)["widget-job"] {
		t.Fatal("local scheduler dropped a widget-owned job; dashboard metrics would go stale")
	}
}

// Both at once — the real post-cutover state of a user's database, where the
// old user rows are still present but inert.
func TestLocalSchedulerPartitionsBySource(t *testing.T) {
	db := scopeTestDB(t)
	seedDueJob(t, db, "widget-a", "widget", "w1")
	seedDueJob(t, db, "widget-b", "widget", "w2")
	seedDueJob(t, db, "user-a", "", "")

	due := dueJobIDs(t, db)
	if len(due) != 2 || !due["widget-a"] || !due["widget-b"] {
		t.Fatalf("expected exactly the two widget jobs, got %v", due)
	}
}

// The ownership predicate is the single place the partition is decided, so it
// is worth pinning directly — including the legacy case.
func TestCollectSchedulerOwns(t *testing.T) {
	cases := []struct {
		name string
		job  CollectJob
		want bool
	}{
		{"widget-owned", CollectJob{Source: "widget"}, true},
		{"user-created", CollectJob{Source: ""}, false},
		{"unknown source is not ours", CollectJob{Source: "something-else"}, false},
	}
	for _, tc := range cases {
		if got := collectSchedulerOwns(tc.job); got != tc.want {
			t.Errorf("%s: collectSchedulerOwns = %v, want %v", tc.name, got, tc.want)
		}
	}
}

// RunCollectJobNow is a Wails binding, so it is a trust boundary: the panel
// must route a server schedule to RunScheduleNow, but the binding cannot rely
// on the panel getting that right.
func TestRunCollectJobNowRefusesServerSchedules(t *testing.T) {
	db := scopeTestDB(t)
	a := &App{db: db}
	seedDueJob(t, db, "user-job", "", "")

	err := a.RunCollectJobNow("user-job")
	if err == nil {
		t.Fatal("expected RunCollectJobNow to refuse a source='' job")
	}
}
