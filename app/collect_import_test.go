package main

import (
	"database/sql"
	"fmt"
	"testing"

	"phantom-ink/brainbox"

	_ "modernc.org/sqlite"
)

// Importing existing local jobs to the server is what stops the cutover from
// silently breaking people. The moment the local scheduler goes widget-only,
// every source="" job in a user's database goes inert — so they have to be
// copied over, idempotently, without deleting the originals.

func importTestDB(t *testing.T) *DB {
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

type fakeCreator struct {
	created []brainbox.Schedule
	err     error
}

func (f *fakeCreator) create(s brainbox.Schedule) (brainbox.Schedule, error) {
	if f.err != nil {
		return brainbox.Schedule{}, f.err
	}
	s.ID = fmt.Sprintf("srv-%d", len(f.created)+1)
	f.created = append(f.created, s)
	return s, nil
}

// insertCollectJobRow writes a collect_jobs row with explicit cadence/target columns.
func insertCollectJobRow(t *testing.T, db *DB, j CollectJob) {
	t.Helper()
	if j.DefaultActions == "" {
		j.DefaultActions = "[]"
	}
	if j.TargetType == "" {
		j.TargetType = "shell"
	}
	_, err := db.conn.Exec(
		`INSERT INTO collect_jobs (id, profile, name, command, interval_s, enabled,
		   default_actions, last_error, created_at, target_type, target_id,
		   target_prompt, run_at, days, source, owner_widget_id, run_once_at_ms)
		 VALUES (?,?,?,?,?,?,?,'',0,?,?,?,?,?,?,?,?)`,
		j.ID, j.Profile, j.Name, j.Command, j.IntervalS, boolToInt(j.Enabled),
		j.DefaultActions, j.TargetType, j.TargetID, j.TargetPrompt, j.RunAt,
		j.Days, j.Source, j.OwnerWidgetID, j.RunOnceAtMs)
	if err != nil {
		t.Fatalf("insert %s: %v", j.ID, err)
	}
}

func TestImportConvertsIntervalCadence(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "j1", Profile: "personal", Name: "deps", Command: "check-deps.sh",
		IntervalS: 300, Enabled: true,
	})
	f := &fakeCreator{}

	res := importLocalSchedules(db, "", f.create)

	if len(res.Imported) != 1 || len(f.created) != 1 {
		t.Fatalf("expected one import, got %+v", res)
	}
	got := f.created[0]
	if got.Schedule["kind"] != "interval" {
		t.Fatalf("kind = %v, want interval", got.Schedule["kind"])
	}
	if got.Schedule["every_s"] != 300 {
		t.Fatalf("every_s = %v, want 300", got.Schedule["every_s"])
	}
	if got.Name != "deps" || got.Profile != "personal" || !got.Enabled {
		t.Fatalf("schedule identity not carried over: %+v", got)
	}
}

func TestImportConvertsTimeOfDayCadence(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "j1", Profile: "personal", Name: "morning", Command: "x.sh",
		RunAt: "08:30", Days: "weekdays", Enabled: true,
	})
	f := &fakeCreator{}

	importLocalSchedules(db, "", f.create)

	got := f.created[0].Schedule
	if got["kind"] != "daily" || got["at"] != "08:30" || got["days"] != "weekdays" {
		t.Fatalf("daily cadence wrong: %+v", got)
	}
}

func TestImportConvertsOneShotCadence(t *testing.T) {
	db := importTestDB(t)
	at := int64(1730000000000)
	insertCollectJobRow(t, db, CollectJob{
		ID: "j1", Profile: "personal", Name: "once", Command: "x.sh",
		RunOnceAtMs: &at, Enabled: true,
	})
	f := &fakeCreator{}

	importLocalSchedules(db, "", f.create)

	got := f.created[0].Schedule
	if got["kind"] != "at" || got["fire_at_ms"] != at {
		t.Fatalf("one-shot cadence wrong: %+v", got)
	}
}

// Cadence precedence has to match collectJobIsDue, or an imported schedule
// fires on a different clock than the job did locally.
func TestImportCadencePrecedenceMatchesLocalScheduler(t *testing.T) {
	db := importTestDB(t)
	at := int64(1730000000000)
	insertCollectJobRow(t, db, CollectJob{
		ID: "j1", Profile: "personal", Name: "all-three", Command: "x.sh",
		IntervalS: 300, RunAt: "08:30", RunOnceAtMs: &at, Enabled: true,
	})
	f := &fakeCreator{}

	importLocalSchedules(db, "", f.create)

	// One-shot wins over time-of-day, which wins over interval.
	if f.created[0].Schedule["kind"] != "at" {
		t.Fatalf("precedence wrong: %+v", f.created[0].Schedule)
	}
}

func TestImportBuildsACollectorAction(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "j1", Profile: "personal", Name: "deps", Command: "check-deps.sh",
		IntervalS: 300, Enabled: true,
	})
	f := &fakeCreator{}

	importLocalSchedules(db, "", f.create)

	actions := f.created[0].Actions
	if len(actions) != 1 {
		t.Fatalf("expected one action, got %d", len(actions))
	}
	a := actions[0]
	if a["type"] != "run_profile_command" {
		t.Fatalf("action type = %v", a["type"])
	}
	if a["command"] != "check-deps.sh" {
		t.Fatalf("command not carried over: %v", a["command"])
	}
	if a["workspace_profile"] != "personal" {
		t.Fatalf("profile not pinned on the action: %v", a["workspace_profile"])
	}
	// It was a collector: its stdout entries have to keep flowing somewhere.
	if a["emit_entries"] != true {
		t.Fatalf("emit_entries should be on for an imported collector: %v", a["emit_entries"])
	}
}

func TestImportSkipsWidgetJobs(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "w1", Profile: "personal", Name: "metric", Command: "x.sh",
		IntervalS: 60, Enabled: true, Source: "widget", OwnerWidgetID: "wid",
	})
	f := &fakeCreator{}

	res := importLocalSchedules(db, "", f.create)

	if len(f.created) != 0 {
		t.Fatalf("widget jobs must stay local, created %d", len(f.created))
	}
	if len(res.Imported) != 0 || len(res.Skipped) != 0 {
		t.Fatalf("a widget job is not a candidate at all: %+v", res)
	}
}

// Re-running the import must not duplicate anything — the operator will press
// the button twice.
func TestImportIsIdempotent(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "j1", Profile: "personal", Name: "deps", Command: "x.sh",
		IntervalS: 300, Enabled: true,
	})
	f := &fakeCreator{}

	importLocalSchedules(db, "", f.create)
	second := importLocalSchedules(db, "", f.create)

	if len(f.created) != 1 {
		t.Fatalf("second run created another schedule: %d total", len(f.created))
	}
	if len(second.AlreadyImported) != 1 {
		t.Fatalf("second run should report the row as already imported: %+v", second)
	}
}

// The local row is kept, marked, not deleted — the import has to be reversible.
func TestImportMarksTheLocalRowWithoutDeletingIt(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "j1", Profile: "personal", Name: "deps", Command: "x.sh",
		IntervalS: 300, Enabled: true,
	})
	f := &fakeCreator{}

	importLocalSchedules(db, "", f.create)

	job, ok := db.GetCollectJob("j1")
	if !ok {
		t.Fatal("local row was deleted; the import must be reversible")
	}
	if job.ImportedScheduleID != "srv-1" {
		t.Fatalf("imported_schedule_id = %q, want srv-1", job.ImportedScheduleID)
	}
}

// A runner-target job ran `claude -p <prompt>` locally. Its server equivalent
// is submit_task, which needs an agent identity the local row never stored —
// so it is reported for the operator to recreate rather than guessed at.
func TestImportSkipsRunnerJobsWithAReason(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "r1", Profile: "personal", Name: "todo", TargetType: "runner",
		TargetPrompt: "fix the thing", IntervalS: 3600, Enabled: true,
	})
	f := &fakeCreator{}

	res := importLocalSchedules(db, "", f.create)

	if len(f.created) != 0 {
		t.Fatalf("runner job should not be invented server-side: %+v", f.created)
	}
	if len(res.Skipped) != 1 || res.Skipped[0].JobID != "r1" {
		t.Fatalf("expected one reported skip, got %+v", res.Skipped)
	}
	if res.Skipped[0].Reason == "" {
		t.Fatal("a skip must carry a reason the operator can act on")
	}
}

func TestImportSkipsJobsWithNoCadence(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "j1", Profile: "personal", Name: "no-cadence", Command: "x.sh",
		Enabled: true, // interval 0, no run_at, no one-shot
	})
	f := &fakeCreator{}

	res := importLocalSchedules(db, "", f.create)

	if len(f.created) != 0 || len(res.Skipped) != 1 {
		t.Fatalf("a job with no cadence cannot be scheduled: %+v", res)
	}
}

// A server failure must leave the local row UNMARKED so a retry picks it up,
// and must not abort the rest of the batch.
func TestImportReportsFailuresAndLeavesRowsRetryable(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "j1", Profile: "personal", Name: "deps", Command: "x.sh",
		IntervalS: 300, Enabled: true,
	})
	f := &fakeCreator{err: fmt.Errorf("HTTP 503: bus down")}

	res := importLocalSchedules(db, "", f.create)

	if len(res.Failed) != 1 {
		t.Fatalf("expected one failure, got %+v", res)
	}
	job, _ := db.GetCollectJob("j1")
	if job.ImportedScheduleID != "" {
		t.Fatal("a failed import must not mark the row as done")
	}
}

func TestImportScopesToProfileWhenAsked(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "p1", Profile: "personal", Name: "a", Command: "x.sh", IntervalS: 60, Enabled: true,
	})
	insertCollectJobRow(t, db, CollectJob{
		ID: "g1", Profile: "gsa", Name: "b", Command: "y.sh", IntervalS: 60, Enabled: true,
	})
	f := &fakeCreator{}

	res := importLocalSchedules(db, "gsa", f.create)

	if len(res.Imported) != 1 || f.created[0].Profile != "gsa" {
		t.Fatalf("profile scoping ignored: %+v / %+v", res, f.created)
	}
}

// A disabled job imports as disabled rather than quietly starting to run.
func TestImportPreservesDisabledState(t *testing.T) {
	db := importTestDB(t)
	insertCollectJobRow(t, db, CollectJob{
		ID: "j1", Profile: "personal", Name: "off", Command: "x.sh",
		IntervalS: 300, Enabled: false,
	})
	f := &fakeCreator{}

	importLocalSchedules(db, "", f.create)

	if f.created[0].Enabled {
		t.Fatal("a disabled local job must not come back enabled on the server")
	}
}
