package main

import (
	"testing"

	"phantom-ink/brainbox"
)

// Server schedules project onto the planned timeline as exactly ONE item each.
// The server materializes a single next_run_at rather than deriving fire times,
// so one next fire is genuinely all that is known — fanning out further would
// mean reimplementing cron and DST client-side, and the extra rows would be
// guesses rather than data.

func ms(v int64) *int64 { return &v }

func TestExpandServerScheduleYieldsOneItem(t *testing.T) {
	got := expandServerSchedule(brainbox.Schedule{
		ID: "s1", Name: "nightly", Profile: "personal", Enabled: true,
		NextRunAt: ms(1_500),
		Schedule:  map[string]interface{}{"kind": "interval", "every_s": 300},
		Actions:   []map[string]interface{}{{"type": "run_profile_command"}},
	}, 1_000, 2_000)

	if len(got) != 1 {
		t.Fatalf("expected exactly one planned item, got %d", len(got))
	}
	item := got[0]
	if item.Source != "schedule" || item.SourceID != "s1" {
		t.Fatalf("provenance wrong: %+v", item)
	}
	if item.FireAtMs != 1_500 || item.Title != "nightly" || item.Profile != "personal" {
		t.Fatalf("item wrong: %+v", item)
	}
	if item.Recurrence != "interval" {
		t.Fatalf("recurrence = %q, want interval", item.Recurrence)
	}
	if item.Kind != "run_profile_command" {
		t.Fatalf("kind should be the action type, got %q", item.Kind)
	}
}

// Dragging rewrites a one-shot's fire time through the LOCAL collect-job path,
// which a server schedule has no row for — so server rows must never be
// draggable, including one-shots.
func TestServerScheduleIsNeverDraggable(t *testing.T) {
	got := expandServerSchedule(brainbox.Schedule{
		ID: "s1", Name: "once", Enabled: true, NextRunAt: ms(1_500),
		Schedule: map[string]interface{}{"kind": "at", "fire_at_ms": 1_500},
	}, 1_000, 2_000)

	if len(got) != 1 {
		t.Fatalf("expected one item, got %d", len(got))
	}
	if got[0].Draggable {
		t.Fatal("a server schedule must not be draggable — the mutation path is the API")
	}
	if got[0].Recurrence != "once" {
		t.Fatalf("recurrence = %q, want once", got[0].Recurrence)
	}
}

func TestExpandServerScheduleRespectsTheWindow(t *testing.T) {
	base := brainbox.Schedule{ID: "s1", Name: "n", Enabled: true,
		Schedule: map[string]interface{}{"kind": "interval", "every_s": 60}}

	before := base
	before.NextRunAt = ms(500)
	if got := expandServerSchedule(before, 1_000, 2_000); len(got) != 0 {
		t.Fatalf("a fire before the window must not appear: %+v", got)
	}

	after := base
	after.NextRunAt = ms(5_000)
	if got := expandServerSchedule(after, 1_000, 2_000); len(got) != 0 {
		t.Fatalf("a fire after the window must not appear: %+v", got)
	}
}

func TestExpandServerScheduleSkipsDisabledAndUnscheduled(t *testing.T) {
	disabled := brainbox.Schedule{ID: "s1", Name: "n", Enabled: false, NextRunAt: ms(1_500),
		Schedule: map[string]interface{}{"kind": "interval", "every_s": 60}}
	if got := expandServerSchedule(disabled, 1_000, 2_000); len(got) != 0 {
		t.Fatal("a disabled schedule has no planned fire")
	}

	// An exhausted one-shot has next_run_at cleared; it must not render as
	// "planned" forever.
	exhausted := brainbox.Schedule{ID: "s2", Name: "n", Enabled: true, NextRunAt: nil,
		Schedule: map[string]interface{}{"kind": "at", "fire_at_ms": 1}}
	if got := expandServerSchedule(exhausted, 1_000, 2_000); len(got) != 0 {
		t.Fatal("a schedule with no next fire has nothing to plan")
	}
}

func TestExpandServerScheduleMapsCadenceToRecurrence(t *testing.T) {
	cases := map[string]string{
		"interval": "interval",
		"daily":    "timeofday",
		"at":       "once",
		"cron":     "cron",
	}
	for kind, want := range cases {
		got := expandServerSchedule(brainbox.Schedule{
			ID: "s", Name: "n", Enabled: true, NextRunAt: ms(1_500),
			Schedule: map[string]interface{}{"kind": kind},
		}, 1_000, 2_000)
		if len(got) != 1 || got[0].Recurrence != want {
			t.Errorf("kind %q -> recurrence %v, want %q", kind, got, want)
		}
	}
}
