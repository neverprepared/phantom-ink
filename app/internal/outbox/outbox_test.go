package outbox

import (
	"context"
	"database/sql"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	_ "modernc.org/sqlite"
)

// schemaSQL mirrors db.go migration v22 — kept inline here so package tests
// don't depend on the app's migration plumbing.
const schemaSQL = `
CREATE TABLE outbox_events (
	rowid           INTEGER PRIMARY KEY AUTOINCREMENT,
	envelope_id     TEXT    NOT NULL,
	envelope_json   TEXT    NOT NULL,
	created_at      INTEGER NOT NULL,
	attempts        INTEGER NOT NULL DEFAULT 0,
	next_attempt_at INTEGER NOT NULL DEFAULT 0,
	last_error      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX idx_outbox_eligible ON outbox_events(next_attempt_at);
`

func newTestDB(t *testing.T) *sql.DB {
	t.Helper()
	// In-memory SQLite is per-connection — pin the pool to one connection so
	// the schema and all queries share the same database.
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(schemaSQL); err != nil {
		t.Fatalf("schema: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func testEnvelope(id string) Envelope {
	return Envelope{
		ID:     id,
		Kind:   "event",
		Title:  "test " + id,
		Source: "test@unit",
		Type:   "test.event",
		Status: "active",
	}
}

func TestAppendPersistsRow(t *testing.T) {
	db := newTestDB(t)
	o := New(db, func(_ context.Context, _ []Envelope) error { return nil }, Options{})
	if err := o.Append(testEnvelope("a")); err != nil {
		t.Fatalf("append: %v", err)
	}
	var n int
	if err := db.QueryRow(`SELECT COUNT(*) FROM outbox_events`).Scan(&n); err != nil {
		t.Fatalf("count: %v", err)
	}
	if n != 1 {
		t.Fatalf("expected 1 row, got %d", n)
	}
}

func TestAppendRequiresIDAndTitle(t *testing.T) {
	db := newTestDB(t)
	o := New(db, func(_ context.Context, _ []Envelope) error { return nil }, Options{})
	if err := o.Append(Envelope{Title: "no id"}); err == nil {
		t.Fatal("expected error for missing id")
	}
	if err := o.Append(Envelope{ID: "no-title"}); err == nil {
		t.Fatal("expected error for missing title")
	}
}

func TestFlushDeliversAndDrains(t *testing.T) {
	db := newTestDB(t)
	var delivered atomic.Int32
	var wg sync.WaitGroup
	wg.Add(3)
	o := New(db, func(_ context.Context, batch []Envelope) error {
		for range batch {
			delivered.Add(1)
			wg.Done()
		}
		return nil
	}, Options{CoalesceWait: 10 * time.Millisecond})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	o.Start(ctx)
	defer o.Stop()

	for _, id := range []string{"a", "b", "c"} {
		if err := o.Append(testEnvelope(id)); err != nil {
			t.Fatalf("append: %v", err)
		}
	}

	waitWG(t, &wg, 2*time.Second)
	if got := delivered.Load(); got != 3 {
		t.Fatalf("delivered = %d, want 3", got)
	}

	// Rows should be gone after successful delivery.
	deadline := time.Now().Add(1 * time.Second)
	for time.Now().Before(deadline) {
		var n int
		_ = db.QueryRow(`SELECT COUNT(*) FROM outbox_events`).Scan(&n)
		if n == 0 {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("outbox rows not drained")
}

func TestFailureRetainsRowAndBumpsAttempts(t *testing.T) {
	db := newTestDB(t)
	called := make(chan struct{}, 16)
	o := New(db, func(_ context.Context, _ []Envelope) error {
		select {
		case called <- struct{}{}:
		default:
		}
		return errors.New("brainbox down")
	}, Options{CoalesceWait: 10 * time.Millisecond})

	// Append BEFORE Start so the first wake the flush loop sees finds the row.
	// Avoids a race where the goroutine's initial wake fires (and finds zero
	// rows) before the test thread schedules Append.
	if err := o.Append(testEnvelope("a")); err != nil {
		t.Fatalf("append: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	o.Start(ctx)
	defer o.Stop()

	select {
	case <-called:
	case <-time.After(2 * time.Second):
		t.Fatal("deliverer never called")
	}

	// Give bumpFailures time to write back before we read attempts.
	deadline := time.Now().Add(500 * time.Millisecond)
	var rows, bumpedAttempts int
	for time.Now().Before(deadline) {
		if err := db.QueryRow(`SELECT COUNT(*), COALESCE(MAX(attempts),0) FROM outbox_events`).
			Scan(&rows, &bumpedAttempts); err != nil {
			t.Fatalf("count: %v", err)
		}
		if bumpedAttempts >= 1 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if rows != 1 {
		t.Fatalf("expected row to remain, got %d", rows)
	}
	if bumpedAttempts < 1 {
		t.Fatalf("expected attempts bumped, got %d", bumpedAttempts)
	}
}

func TestSurvivesRestart(t *testing.T) {
	db := newTestDB(t)
	// Simulate a crashed run: append rows, never start the flusher.
	o1 := New(db, func(_ context.Context, _ []Envelope) error { return nil }, Options{})
	for _, id := range []string{"a", "b"} {
		if err := o1.Append(testEnvelope(id)); err != nil {
			t.Fatalf("append: %v", err)
		}
	}

	// New process: same DB, fresh outbox. Should drain the stale rows on startup.
	var delivered atomic.Int32
	var wg sync.WaitGroup
	wg.Add(2)
	o2 := New(db, func(_ context.Context, batch []Envelope) error {
		for range batch {
			delivered.Add(1)
			wg.Done()
		}
		return nil
	}, Options{CoalesceWait: 10 * time.Millisecond})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	o2.Start(ctx)
	defer o2.Stop()

	waitWG(t, &wg, 2*time.Second)
	if got := delivered.Load(); got != 2 {
		t.Fatalf("delivered = %d, want 2", got)
	}
}

func TestNextRetryAtClampsToLastBucket(t *testing.T) {
	now := time.Now().UnixMilli()
	last := nextRetryAt(len(retrySchedule) + 5)
	// Should be ~30 minutes out (the last bucket), within a few seconds tolerance.
	want := now + int64(retrySchedule[len(retrySchedule)-1]/time.Millisecond)
	if abs(last-want) > 5_000 {
		t.Fatalf("clamp: last=%d want≈%d (delta %d)", last, want, abs(last-want))
	}
}

func waitWG(t *testing.T, wg *sync.WaitGroup, timeout time.Duration) {
	t.Helper()
	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(timeout):
		t.Fatal("timeout waiting for delivery")
	}
}

func abs(x int64) int64 {
	if x < 0 {
		return -x
	}
	return x
}
