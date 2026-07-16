package main

import (
	"context"
	"database/sql"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	_ "modernc.org/sqlite"

	"phantom-ink/internal/outbox"
)

// schemaSQL mirrors db.go migration v22 — kept inline so package tests
// don't depend on the full app migrate plumbing.
const outboxSchema = `
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

// newAppWithOutbox boots a minimal App wired only with an outbox + capturing
// deliverer. Tests don't need Wails runtime — RecordAction only touches the
// outbox.
func newAppWithOutbox(t *testing.T) (*App, *capturingDeliverer) {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(outboxSchema); err != nil {
		t.Fatalf("schema: %v", err)
	}
	t.Cleanup(func() { db.Close() })

	cap := &capturingDeliverer{}
	a := &App{}
	a.outbox = outbox.New(db, cap.deliver, outbox.Options{CoalesceWait: 10 * time.Millisecond})

	ctx, cancel := context.WithCancel(context.Background())
	a.outbox.Start(ctx)
	t.Cleanup(func() {
		cancel()
		a.outbox.Stop()
	})
	return a, cap
}

type capturingDeliverer struct {
	mu        sync.Mutex
	envelopes []outbox.Envelope
	calls     atomic.Int32
}

func (c *capturingDeliverer) deliver(_ context.Context, batch []outbox.Envelope) error {
	c.calls.Add(1)
	c.mu.Lock()
	defer c.mu.Unlock()
	c.envelopes = append(c.envelopes, batch...)
	return nil
}

func (c *capturingDeliverer) waitFor(t *testing.T, n int, within time.Duration) []outbox.Envelope {
	t.Helper()
	deadline := time.Now().Add(within)
	for time.Now().Before(deadline) {
		c.mu.Lock()
		got := len(c.envelopes)
		c.mu.Unlock()
		if got >= n {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	out := make([]outbox.Envelope, len(c.envelopes))
	copy(out, c.envelopes)
	return out
}

func TestRecordActionWritesSuccessEnvelope(t *testing.T) {
	a, cap := newAppWithOutbox(t)
	err := a.recordAction("task:t1", "retry", ActorUser, func() error { return nil })
	if err != nil {
		t.Fatalf("fn err: %v", err)
	}
	envs := cap.waitFor(t, 1, 2*time.Second)
	if len(envs) != 1 {
		t.Fatalf("envelopes = %d, want 1", len(envs))
	}
	e := envs[0]
	if e.Type == nil || *e.Type != "action.retry" {
		t.Errorf("type = %v", e.Type)
	}
	if e.ParentID == nil || *e.ParentID != "task:t1" {
		t.Errorf("parent_id = %v", e.ParentID)
	}
	if e.Outcome == nil || !e.Outcome.OK {
		t.Errorf("outcome not OK: %+v", e.Outcome)
	}
	if e.Outcome.Actor != ActorUser {
		t.Errorf("actor = %q", e.Outcome.Actor)
	}
	if e.Outcome.DurationMs == nil || *e.Outcome.DurationMs < 0 {
		t.Errorf("duration missing: %+v", e.Outcome.DurationMs)
	}
}

func TestRecordActionCapturesFailure(t *testing.T) {
	a, cap := newAppWithOutbox(t)
	got := a.recordAction("hub-task:abc", "respond", ActorUser, func() error {
		return errors.New("boom")
	})
	if got == nil || got.Error() != "boom" {
		t.Fatalf("expected error to propagate, got %v", got)
	}
	envs := cap.waitFor(t, 1, 2*time.Second)
	if len(envs) != 1 {
		t.Fatalf("envelopes = %d, want 1", len(envs))
	}
	e := envs[0]
	if e.Outcome == nil || e.Outcome.OK {
		t.Errorf("expected outcome.ok=false, got %+v", e.Outcome)
	}
	if e.Outcome.Error == nil || *e.Outcome.Error != "boom" {
		t.Errorf("outcome.error = %v", e.Outcome.Error)
	}
}

func TestRecordActionWithoutOutboxStillRunsFn(t *testing.T) {
	a := &App{} // no outbox
	ran := false
	err := a.recordAction("x", "noop", ActorSystem, func() error {
		ran = true
		return nil
	})
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if !ran {
		t.Fatal("fn did not run")
	}
}
