package main

import (
	"database/sql"
	"testing"
	"time"

	"phantom-ink/brainbox"
)

// newMigratedTestDB returns an in-memory DB with the full schema (every table),
// unlike newTestDB which only creates collect_jobs. Needed for tests that touch
// schedules/sequences alongside collect jobs.
func newMigratedTestDB(t *testing.T) *DB {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	conn.SetMaxOpenConns(1)
	db := &DB{conn: conn}
	if err := db.migrate(); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	return db
}

// expandSchedule enumerates cron fires strictly within the window, as read-only
// (non-draggable) cron occurrences.
func TestExpandSchedule_WithinWindow(t *testing.T) {
	now := time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC) // Monday 00:00
	until := now.Add(2 * time.Hour).UnixMilli()
	s := ScheduleRow{ID: "s1", SequenceID: "loopA", CronExpr: "*/30 * * * *", Enabled: true, WorkspaceProfile: "p", Input: "run A"}

	got := expandSchedule(s, now.UnixMilli(), until, now, map[string]string{"loopA": "Loop A"})

	if len(got) != 4 { // 00:30, 01:00, 01:30, 02:00
		t.Fatalf("expected 4 fires in a 2h window for */30, got %d: %+v", len(got), got)
	}
	var prev int64
	for _, it := range got {
		if it.Recurrence != "cron" || it.Draggable {
			t.Errorf("cron occurrence must be recurrence=cron, draggable=false: %+v", it)
		}
		if it.Source != "schedule" || it.SourceID != "s1" {
			t.Errorf("wrong source linkage: %+v", it)
		}
		if it.Title != "run A" { // Input wins over loop name
			t.Errorf("title = %q, want %q", it.Title, "run A")
		}
		if it.FireAtMs <= now.UnixMilli() || it.FireAtMs > until {
			t.Errorf("fire %d outside (now, until]", it.FireAtMs)
		}
		if it.FireAtMs <= prev {
			t.Errorf("fires not strictly ascending: %d after %d", it.FireAtMs, prev)
		}
		prev = it.FireAtMs
	}
}

// A disabled schedule / bad cron yields nothing.
func TestExpandSchedule_DisabledOrInvalid(t *testing.T) {
	now := time.Now()
	until := now.Add(time.Hour).UnixMilli()
	if got := expandSchedule(ScheduleRow{ID: "x", CronExpr: "* * * * *", Enabled: false}, now.UnixMilli(), until, now, nil); got != nil {
		t.Errorf("disabled schedule must expand to nil, got %+v", got)
	}
	if got := expandSchedule(ScheduleRow{ID: "x", CronExpr: "not a cron", Enabled: true}, now.UnixMilli(), until, now, nil); got != nil {
		t.Errorf("invalid cron must expand to nil, got %+v", got)
	}
}

func TestExpandInterval(t *testing.T) {
	now := time.Now()
	nowMs := now.UnixMilli()
	j := CollectJob{ID: "j1", Profile: "p", Name: "poll", IntervalS: 3600, Enabled: true} // hourly, never run
	until := nowMs + 3*3600*1000

	got := expandInterval(j, PlannedItem{Source: "collect", SourceID: j.ID, Title: j.Name}, nowMs, until, now)

	if len(got) != 4 { // now, +1h, +2h, +3h (==until, inclusive)
		t.Fatalf("expected 4 hourly fires across 3h, got %d", len(got))
	}
	if got[0].FireAtMs != nowMs {
		t.Errorf("first interval fire = %d, want now %d", got[0].FireAtMs, nowMs)
	}
	for i, it := range got {
		if it.Recurrence != "interval" || it.Draggable {
			t.Errorf("interval occurrence must be recurrence=interval, draggable=false: %+v", it)
		}
		if i > 0 && it.FireAtMs-got[i-1].FireAtMs != 3600*1000 {
			t.Errorf("interval spacing wrong at %d", i)
		}
	}
}

func TestExpandTimeOfDay_DailyVsWeekdays(t *testing.T) {
	now := time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC) // Monday
	until := now.AddDate(0, 0, 7).UnixMilli()          // through next Monday 00:00

	daily := expandTimeOfDay(
		CollectJob{ID: "d", Name: "daily", RunAt: "09:00", Days: "daily", Enabled: true},
		PlannedItem{Source: "collect", SourceID: "d"}, now.UnixMilli(), until, now)
	if len(daily) != 7 { // Mon..Sun at 09:00
		t.Fatalf("daily: expected 7 occurrences, got %d", len(daily))
	}

	weekdays := expandTimeOfDay(
		CollectJob{ID: "w", Name: "wd", RunAt: "09:00", Days: "weekdays", Enabled: true},
		PlannedItem{Source: "collect", SourceID: "w"}, now.UnixMilli(), until, now)
	if len(weekdays) != 5 { // Mon..Fri (Sat/Sun skipped)
		t.Fatalf("weekdays: expected 5 occurrences, got %d", len(weekdays))
	}
	for _, it := range weekdays {
		wd := time.UnixMilli(it.FireAtMs).UTC().Weekday()
		if wd == time.Saturday || wd == time.Sunday {
			t.Errorf("weekdays expansion leaked a weekend fire: %v", wd)
		}
		if it.Recurrence != "timeofday" || it.Draggable {
			t.Errorf("timeofday occurrence must be recurrence=timeofday, draggable=false: %+v", it)
		}
	}
}

func TestExpandCollectJob_OneShot(t *testing.T) {
	now := time.Now()
	nowMs := now.UnixMilli()
	at := nowMs + 60_000

	// in-window, never run → one draggable point
	got := expandCollectJob(CollectJob{ID: "os", Name: "once", RunOnceAtMs: &at, Enabled: true}, nowMs, nowMs+3600_000, now)
	if len(got) != 1 {
		t.Fatalf("one-shot in window: expected 1 item, got %d", len(got))
	}
	if !got[0].Draggable || got[0].Recurrence != "once" || got[0].FireAtMs != at {
		t.Errorf("one-shot item wrong: %+v", got[0])
	}

	// already fired (LastRunAt set) → excluded
	ran := nowMs - 500
	if got := expandCollectJob(CollectJob{ID: "os", RunOnceAtMs: &at, LastRunAt: &ran, Enabled: true}, nowMs, nowMs+3600_000, now); got != nil {
		t.Errorf("fired one-shot must be excluded, got %+v", got)
	}

	// outside window → excluded
	far := nowMs + 10*3600_000
	if got := expandCollectJob(CollectJob{ID: "os", RunOnceAtMs: &far, Enabled: true}, nowMs, nowMs+3600_000, now); got != nil {
		t.Errorf("out-of-window one-shot must be excluded, got %+v", got)
	}

	// disabled → excluded
	if got := expandCollectJob(CollectJob{ID: "os", RunOnceAtMs: &at, Enabled: false}, nowMs, nowMs+3600_000, now); got != nil {
		t.Errorf("disabled job must be excluded, got %+v", got)
	}
}

func TestListPlannedItems_ProfileFilter(t *testing.T) {
	db := newMigratedTestDB(t)
	a := &App{db: db}
	now := time.Now()
	atA := now.Add(5 * time.Minute).UnixMilli()
	atB := now.Add(5 * time.Minute).UnixMilli()

	if err := db.UpsertCollectJob(CollectJob{ID: "ja", Profile: "a", Name: "A one-shot", TargetType: "runner", RunOnceAtMs: &atA, Enabled: true, CreatedAt: 1}); err != nil {
		t.Fatal(err)
	}
	if err := db.UpsertCollectJob(CollectJob{ID: "jb", Profile: "b", Name: "B one-shot", TargetType: "runner", RunOnceAtMs: &atB, Enabled: true, CreatedAt: 1}); err != nil {
		t.Fatal(err)
	}
	if err := db.UpsertSchedule(ScheduleRow{ID: "sa", SequenceID: "L", CronExpr: "0 * * * *", Enabled: true, WorkspaceProfile: "a", CreatedAt: "2024-01-01T00:00:00Z", UpdatedAt: "2024-01-01T00:00:00Z"}); err != nil {
		t.Fatal(err)
	}

	items, err := a.ListPlannedItems("a", now.UnixMilli(), now.Add(48*time.Hour).UnixMilli())
	if err != nil {
		t.Fatal(err)
	}
	var sawCollectA, sawSchedule bool
	for _, it := range items {
		if it.Profile != "a" {
			t.Errorf("profile filter leaked a non-'a' item: %+v", it)
		}
		if it.SourceID == "jb" {
			t.Errorf("profile 'b' job leaked into profile 'a' query: %+v", it)
		}
		if it.SourceID == "ja" {
			sawCollectA = true
		}
		if it.Source == "schedule" && it.SourceID == "sa" {
			sawSchedule = true
		}
	}
	if !sawCollectA {
		t.Error("expected profile 'a' one-shot in results")
	}
	if !sawSchedule {
		t.Error("expected profile 'a' schedule in results")
	}
}

func TestRescheduleOneShot(t *testing.T) {
	db := newTestDB(t)
	a := &App{db: db}
	now := time.Now()
	at := now.Add(10 * time.Minute).UnixMilli()

	if err := db.UpsertCollectJob(CollectJob{ID: "os1", Profile: "p", Name: "one-shot", TargetType: "runner", RunOnceAtMs: &at, Enabled: true, CreatedAt: 1}); err != nil {
		t.Fatal(err)
	}
	if err := db.UpsertCollectJob(CollectJob{ID: "rec", Profile: "p", Name: "recurring", IntervalS: 300, Enabled: true, CreatedAt: 1}); err != nil {
		t.Fatal(err)
	}

	newAt := now.Add(2 * time.Hour).UnixMilli()
	job, err := a.RescheduleOneShot("os1", newAt)
	if err != nil {
		t.Fatalf("reschedule one-shot: %v", err)
	}
	if job.RunOnceAtMs == nil || *job.RunOnceAtMs != newAt {
		t.Fatalf("returned job RunOnceAtMs = %v, want %d", job.RunOnceAtMs, newAt)
	}
	stored, _ := db.GetCollectJob("os1")
	if stored.RunOnceAtMs == nil || *stored.RunOnceAtMs != newAt {
		t.Fatalf("persisted RunOnceAtMs = %v, want %d", stored.RunOnceAtMs, newAt)
	}

	// recurring job is not reschedulable via this path
	if _, err := a.RescheduleOneShot("rec", newAt); err == nil {
		t.Error("expected error rescheduling a recurring job as one-shot")
	}
	// unknown job
	if _, err := a.RescheduleOneShot("nope", newAt); err == nil {
		t.Error("expected error for unknown job id")
	}
	// invalid time
	if _, err := a.RescheduleOneShot("os1", 0); err == nil {
		t.Error("expected error for non-positive fire time")
	}
}

func TestIsTopLevelTimelineID(t *testing.T) {
	keep := []string{"task:abc", "loop:run-1", "entry:job/entry", "action:x:retry:1"}
	drop := []string{"loop-step:run-1:3", "", "misc:thing"}
	for _, id := range keep {
		if !isTopLevelTimelineID(id) {
			t.Errorf("%q should be kept", id)
		}
	}
	for _, id := range drop {
		if isTopLevelTimelineID(id) {
			t.Errorf("%q should be dropped (esp. loop-step)", id)
		}
	}
}

func TestTimelineEventFromEntry_PrefersEnvelopeTimes(t *testing.T) {
	e := brainbox.AgentEventEntry{
		ID:     "task:1",
		Type:   "task.succeeded",
		Status: "done",
		Ts:     1000,
		Envelope: map[string]interface{}{
			"title":    "Did a thing",
			"start_at": float64(2000), // JSON numbers decode as float64
			"end_at":   float64(2500),
		},
	}
	ev := timelineEventFromEntry(e)
	if ev.Title != "Did a thing" || ev.StartAtMs != 2000 || ev.EndAtMs != 2500 {
		t.Fatalf("mapped event = %+v", ev)
	}
	// falls back to Ts when envelope lacks start_at
	ev2 := timelineEventFromEntry(brainbox.AgentEventEntry{ID: "loop:x", Ts: 777, Envelope: map[string]interface{}{}})
	if ev2.StartAtMs != 777 || ev2.EndAtMs != 777 {
		t.Fatalf("fallback to Ts failed: %+v", ev2)
	}
}
