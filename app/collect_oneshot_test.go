package main

import (
	"testing"
	"time"
)

func ptrInt64(v int64) *int64 { return &v }

// collectJobIsDue must fire a one-shot ("run once at T") job exactly once: only
// after the wall clock reaches T, and never again once last_run_at is set.
func TestCollectJobIsDue_OneShot(t *testing.T) {
	now := time.Now()
	nowMs := now.UnixMilli()

	cases := []struct {
		name string
		job  CollectJob
		want bool
	}{
		{
			name: "future T is not due",
			job:  CollectJob{RunOnceAtMs: ptrInt64(nowMs + 60_000)},
			want: false,
		},
		{
			name: "past T, never run, is due",
			job:  CollectJob{RunOnceAtMs: ptrInt64(nowMs - 1_000)},
			want: true,
		},
		{
			name: "exactly at T is due",
			job:  CollectJob{RunOnceAtMs: ptrInt64(nowMs)},
			want: true,
		},
		{
			name: "past T but already ran is not due",
			job:  CollectJob{RunOnceAtMs: ptrInt64(nowMs - 1_000), LastRunAt: ptrInt64(nowMs - 500)},
			want: false,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := collectJobIsDue(tc.job, now); got != tc.want {
				t.Fatalf("collectJobIsDue = %v, want %v", got, tc.want)
			}
		})
	}
}

// A one-shot job's RunOnceAtMs must survive the upsert → scan round-trip, and a
// recurring job must read back as nil (interval mode still takes over).
func TestCollectJob_RunOnceAtMsRoundTrip(t *testing.T) {
	db := newTestDB(t)
	at := time.Now().Add(2 * time.Minute).UnixMilli()

	oneShot := CollectJob{
		ID: "os1", Profile: "p", Name: "one-shot", TargetType: "runner",
		TargetPrompt: "do the thing", RunOnceAtMs: &at, Enabled: true, CreatedAt: 1,
	}
	if err := db.UpsertCollectJob(oneShot); err != nil {
		t.Fatalf("upsert one-shot: %v", err)
	}
	got, ok := db.GetCollectJob("os1")
	if !ok {
		t.Fatal("one-shot job not found after upsert")
	}
	if got.RunOnceAtMs == nil || *got.RunOnceAtMs != at {
		t.Fatalf("RunOnceAtMs round-trip = %v, want %d", got.RunOnceAtMs, at)
	}

	recurring := CollectJob{ID: "r1", Profile: "p", Name: "recurring", IntervalS: 300, CreatedAt: 1}
	if err := db.UpsertCollectJob(recurring); err != nil {
		t.Fatalf("upsert recurring: %v", err)
	}
	got2, _ := db.GetCollectJob("r1")
	if got2.RunOnceAtMs != nil {
		t.Fatalf("recurring RunOnceAtMs = %v, want nil", *got2.RunOnceAtMs)
	}
}
