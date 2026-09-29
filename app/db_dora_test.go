package main

import (
	"testing"
	"time"
)

// The DORA event store is the persistence half of the dashboard: metric
// computation is a pure function over these rows, so the two properties that
// matter here are profile isolation (one profile must never see another's
// deploys) and idempotency (re-syncing an overlapping window must not
// double-count a merge into deployment frequency).

func TestDeployEventsAreProfileScopedAndIdempotent(t *testing.T) {
	db := newMigratedTestDB(t)
	ev := DeployEventRow{
		Provider: "github", RepoFullName: "o/r", PRNumber: 1,
		Title: "Add thing", Author: "me",
		CreatedAt: "2026-09-01T10:00:00Z", MergedAt: "2026-09-01T12:00:00Z",
	}
	if err := db.UpsertDeployEvents("personal", []DeployEventRow{ev, ev}); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	if err := db.UpsertDeployEvents("work", []DeployEventRow{ev}); err != nil {
		t.Fatalf("upsert other profile: %v", err)
	}
	got, err := db.DeployEventsSince("personal", time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("want 1 event for personal (idempotent, not leaked), got %d", len(got))
	}
	if got[0].PRNumber != 1 {
		t.Fatalf("wrong row: %+v", got[0])
	}
	if got[0].Title != "Add thing" || got[0].Author != "me" || got[0].MergedAt != "2026-09-01T12:00:00Z" {
		t.Fatalf("fields did not round-trip: %+v", got[0])
	}
}

func TestDeployEventsSinceExcludesOlderMerges(t *testing.T) {
	db := newMigratedTestDB(t)
	rows := []DeployEventRow{
		{Provider: "github", RepoFullName: "o/r", PRNumber: 1, CreatedAt: "2026-07-01T10:00:00Z", MergedAt: "2026-07-01T12:00:00Z"},
		{Provider: "github", RepoFullName: "o/r", PRNumber: 2, CreatedAt: "2026-09-01T10:00:00Z", MergedAt: "2026-09-01T12:00:00Z"},
	}
	if err := db.UpsertDeployEvents("personal", rows); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	got, err := db.DeployEventsSince("personal", time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if len(got) != 1 || got[0].PRNumber != 2 {
		t.Fatalf("since must filter on merged_at: %+v", got)
	}
}

func TestFailureEventsAreProfileScopedAndIdempotent(t *testing.T) {
	db := newMigratedTestDB(t)
	ev := FailureEventRow{
		Provider: "github", RepoFullName: "o/r", RevertSHA: "abc123",
		RevertedSubject: "Add thing", MatchedPRNumber: 1,
		RevertedAt: "2026-09-02T12:00:00Z",
	}
	if err := db.UpsertFailureEvents("personal", []FailureEventRow{ev, ev}); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	if err := db.UpsertFailureEvents("work", []FailureEventRow{ev}); err != nil {
		t.Fatalf("upsert other profile: %v", err)
	}
	got, err := db.FailureEventsSince("personal", time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("want 1 failure for personal (idempotent, not leaked), got %d", len(got))
	}
	if got[0].RevertSHA != "abc123" || got[0].MatchedPRNumber != 1 || got[0].RevertedSubject != "Add thing" {
		t.Fatalf("fields did not round-trip: %+v", got[0])
	}
}

func TestWatermarkRoundTrips(t *testing.T) {
	db := newMigratedTestDB(t)
	zero, err := db.DORAWatermark("personal", "github", "o/r")
	if err != nil {
		t.Fatalf("missing watermark must be zero-time, not an error: %v", err)
	}
	if !zero.IsZero() {
		t.Fatalf("never-synced repo must report the zero time, got %v", zero)
	}
	want := time.Date(2026, 9, 20, 8, 0, 0, 0, time.UTC)
	if err := db.SetDORAWatermark("personal", "github", "o/r", want); err != nil {
		t.Fatalf("set: %v", err)
	}
	got, err := db.DORAWatermark("personal", "github", "o/r")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if !got.Equal(want) {
		t.Fatalf("want %v got %v", want, got)
	}
}

func TestWatermarkIsPerProfileAndAdvances(t *testing.T) {
	db := newMigratedTestDB(t)
	first := time.Date(2026, 9, 20, 8, 0, 0, 0, time.UTC)
	second := time.Date(2026, 9, 25, 8, 0, 0, 0, time.UTC)
	if err := db.SetDORAWatermark("personal", "github", "o/r", first); err != nil {
		t.Fatalf("set: %v", err)
	}
	// A second sync must MOVE the watermark, not be swallowed as a conflict —
	// unlike the event rows, this one is an update, not a DO NOTHING.
	if err := db.SetDORAWatermark("personal", "github", "o/r", second); err != nil {
		t.Fatalf("advance: %v", err)
	}
	got, _ := db.DORAWatermark("personal", "github", "o/r")
	if !got.Equal(second) {
		t.Fatalf("watermark did not advance: got %v want %v", got, second)
	}
	other, err := db.DORAWatermark("work", "github", "o/r")
	if err != nil {
		t.Fatalf("other profile: %v", err)
	}
	if !other.IsZero() {
		t.Fatalf("another profile must not see this profile's watermark: %v", other)
	}
}

func TestUpsertEventsWithNoRowsIsANoOp(t *testing.T) {
	db := newMigratedTestDB(t)
	if err := db.UpsertDeployEvents("personal", nil); err != nil {
		t.Fatalf("empty deploy upsert: %v", err)
	}
	if err := db.UpsertFailureEvents("personal", nil); err != nil {
		t.Fatalf("empty failure upsert: %v", err)
	}
}
