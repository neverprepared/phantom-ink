// Package outbox is a durable, at-least-once delivery queue for agent-event-bus
// envelopes. Producers call Append; a background flusher batches eligible rows
// to a delivery callback (typically brainbox /api/agent_events) and removes
// them on success. Failures bump attempts and schedule a retry per
// retrySchedule. Brainbox dedups by envelope.id, so at-least-once is safe.
//
// The store is SQLite-backed (table outbox_events created by db migration v22).
// We keep the schema and SQL here, not in db.go, so the package is self-contained.
package outbox

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"phantom-ink/internal/contract"
)

// Envelope is the timeline-entry contract type, GENERATED from
// neverprepared/phantom-contracts (see internal/contract). It is aliased here so
// producers keep referring to outbox.Envelope, but the shape can no longer be
// hand-edited into drift — edit the brainbox model and regenerate. Outcome is
// the action-result sub-object.
type Envelope = contract.AgentEnvelope

// Outcome populates when an envelope IS an action result (type="action.*").
type Outcome = contract.ActionOutcome

// Deliverer ships a batch of envelopes upstream. Returns nil on success; a
// non-nil error keeps the rows in the queue for retry. Implementations should
// treat partial failures by returning an error so the whole batch retries —
// brainbox dedup by id makes this safe.
type Deliverer func(ctx context.Context, batch []Envelope) error

// retrySchedule mirrors the runner's ResultQueue retry curve.
var retrySchedule = []time.Duration{
	5 * time.Second,
	15 * time.Second,
	60 * time.Second,
	5 * time.Minute,
	30 * time.Minute,
}

// Outbox is the producer/consumer handle.
type Outbox struct {
	db         *sql.DB
	deliver    Deliverer
	batchSize  int
	coalesceMS time.Duration

	stopCh chan struct{}
	wakeCh chan struct{}
	doneCh chan struct{}

	mu      sync.Mutex
	pending int // best-effort counter for status UI; truth lives in SQLite
}

// Options for New.
type Options struct {
	BatchSize    int           // default 50
	CoalesceWait time.Duration // default 200ms
}

// New constructs an Outbox bound to the given *sql.DB (must already have the
// outbox_events table from migration v22) and a Deliverer that ships batches.
func New(db *sql.DB, deliver Deliverer, opts Options) *Outbox {
	if opts.BatchSize <= 0 {
		opts.BatchSize = 50
	}
	if opts.CoalesceWait <= 0 {
		opts.CoalesceWait = 200 * time.Millisecond
	}
	return &Outbox{
		db:         db,
		deliver:    deliver,
		batchSize:  opts.BatchSize,
		coalesceMS: opts.CoalesceWait,
		stopCh:     make(chan struct{}),
		wakeCh:     make(chan struct{}, 1),
		doneCh:     make(chan struct{}),
	}
}

// Start launches the flush loop in a goroutine. Cancel ctx or call Stop to halt.
func (o *Outbox) Start(ctx context.Context) {
	go o.flushLoop(ctx)
}

// Stop signals the flush loop to exit and waits up to 2s for it to drain.
func (o *Outbox) Stop() {
	close(o.stopCh)
	select {
	case <-o.doneCh:
	case <-time.After(2 * time.Second):
	}
}

// Append persists one envelope to the queue and nudges the flush loop. Safe to
// call from any goroutine. Returns immediately after the row commits.
func (o *Outbox) Append(env Envelope) error {
	if env.ID == "" || env.Title == "" {
		return errors.New("outbox: envelope id and title are required")
	}
	if env.Kind == "" {
		env.Kind = "event"
	}
	raw, err := json.Marshal(env)
	if err != nil {
		return fmt.Errorf("marshal envelope: %w", err)
	}
	now := time.Now().UnixMilli()
	_, err = o.db.Exec(
		`INSERT INTO outbox_events (envelope_id, envelope_json, created_at, next_attempt_at)
		 VALUES (?, ?, ?, ?)`,
		env.ID, string(raw), now, now,
	)
	if err != nil {
		return fmt.Errorf("insert outbox row: %w", err)
	}
	o.mu.Lock()
	o.pending++
	o.mu.Unlock()
	select {
	case o.wakeCh <- struct{}{}:
	default: // already pending
	}
	return nil
}

// Pending returns the best-effort count of un-delivered envelopes for status UI.
// Authoritative count lives in SQLite; this is a hint that may briefly drift.
func (o *Outbox) Pending() int {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.pending
}

// PendingFromDB queries the authoritative count from SQLite. Use sparingly.
func (o *Outbox) PendingFromDB() int {
	var n int
	if err := o.db.QueryRow(`SELECT COUNT(*) FROM outbox_events`).Scan(&n); err != nil {
		return 0
	}
	o.mu.Lock()
	o.pending = n
	o.mu.Unlock()
	return n
}

func (o *Outbox) flushLoop(ctx context.Context) {
	defer close(o.doneCh)
	// Sync the counter once at startup so the UI shows leftover envelopes from
	// previous runs immediately.
	o.PendingFromDB()
	// Always wake on launch in case there are stale rows.
	o.scheduleWake()

	for {
		select {
		case <-ctx.Done():
			return
		case <-o.stopCh:
			return
		case <-o.wakeCh:
			// Coalesce: wait briefly so a burst becomes one batch.
			select {
			case <-time.After(o.coalesceMS):
			case <-o.stopCh:
				return
			case <-ctx.Done():
				return
			}
			o.drainEligible(ctx)
		case <-time.After(5 * time.Second):
			// Heartbeat: also drain anything whose retry timer fired.
			o.drainEligible(ctx)
		}
	}
}

func (o *Outbox) scheduleWake() {
	select {
	case o.wakeCh <- struct{}{}:
	default:
	}
}

type outboxRow struct {
	rowID    int64
	envelope Envelope
	attempts int
}

func (o *Outbox) drainEligible(ctx context.Context) {
	for {
		rows, err := o.fetchEligible(o.batchSize)
		if err != nil || len(rows) == 0 {
			return
		}
		envelopes := make([]Envelope, len(rows))
		ids := make([]int64, len(rows))
		for i, r := range rows {
			envelopes[i] = r.envelope
			ids[i] = r.rowID
		}
		err = o.deliver(ctx, envelopes)
		if err == nil {
			o.deleteRows(ids)
			o.mu.Lock()
			o.pending -= len(ids)
			if o.pending < 0 {
				o.pending = 0
			}
			o.mu.Unlock()
			// Loop to pick up next batch if any.
			continue
		}
		// Failure — bump attempts/last_error/next_attempt_at on every row in
		// the batch using its own current attempts count.
		o.bumpFailures(rows, err.Error())
		return // back off; next wake or heartbeat will retry
	}
}

func (o *Outbox) fetchEligible(limit int) ([]outboxRow, error) {
	now := time.Now().UnixMilli()
	rows, err := o.db.Query(`
		SELECT rowid, envelope_json, attempts
		FROM outbox_events
		WHERE next_attempt_at <= ?
		ORDER BY rowid
		LIMIT ?`, now, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []outboxRow
	for rows.Next() {
		var r outboxRow
		var raw string
		if err := rows.Scan(&r.rowID, &raw, &r.attempts); err != nil {
			continue
		}
		if err := json.Unmarshal([]byte(raw), &r.envelope); err != nil {
			// Corrupt row — log and drop so it can't block delivery forever.
			_, _ = o.db.Exec(`DELETE FROM outbox_events WHERE rowid = ?`, r.rowID)
			continue
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (o *Outbox) deleteRows(ids []int64) {
	tx, err := o.db.Begin()
	if err != nil {
		return
	}
	for _, id := range ids {
		_, _ = tx.Exec(`DELETE FROM outbox_events WHERE rowid = ?`, id)
	}
	_ = tx.Commit()
}

func (o *Outbox) bumpFailures(rows []outboxRow, msg string) {
	tx, err := o.db.Begin()
	if err != nil {
		return
	}
	for _, r := range rows {
		next := nextRetryAt(r.attempts + 1)
		_, _ = tx.Exec(
			`UPDATE outbox_events
			 SET attempts = ?, next_attempt_at = ?, last_error = ?
			 WHERE rowid = ?`,
			r.attempts+1, next, msg, r.rowID,
		)
	}
	_ = tx.Commit()
}

// nextRetryAt returns the wall-clock millisecond for the Nth retry, using
// retrySchedule (and clamping to the last bucket beyond its length).
func nextRetryAt(attempts int) int64 {
	idx := attempts - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= len(retrySchedule) {
		idx = len(retrySchedule) - 1
	}
	return time.Now().Add(retrySchedule[idx]).UnixMilli()
}
