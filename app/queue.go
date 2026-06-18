package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"math"
	"os"
	"strings"
	"sync"
	"time"
)

// Task status constants — keep in sync with the tasks.status CHECK-less column
// values used in CRUD helpers. Adding a new status requires updating both UI
// filters and any code that branches on it.
const (
	TaskPending   = "pending"
	TaskRunning   = "running"
	TaskSucceeded = "succeeded"
	TaskFailed    = "failed"
	TaskCancelled = "cancelled"
)

// Task trigger provenance — answers "why was this task enqueued?"
const (
	TriggerManual   = "manual"
	TriggerSchedule = "schedule"
	TriggerWebhook  = "webhook"
	TriggerFollowup = "followup"
)

// Task is the runtime form of TaskRow, exposed to the frontend by app_queue.go.
// We mirror TaskRow to avoid leaking storage details (and to give us a place to
// hang derived fields later, e.g. loop_name).
type Task = TaskRow

// taskEvent payload pushed to the frontend whenever a task changes state, so
// the Tasks panel and loop runner stay live without polling.
type taskEvent struct {
	TaskID   string `json:"task_id"`
	SequenceID  string `json:"loop_id"`
	Status   string `json:"status"`
	Attempts int    `json:"attempts"`
	Error    string `json:"error"`
	At       string `json:"at"`
}

const taskEventName = "task:event"

// worker is the in-process task-queue runner. v1 is a single goroutine that
// polls the tasks table for eligible work, runs each loop to completion,
// then loops. Concurrency knobs (worker pool, per-loop rate limit) will go
// here when needed.
//
// Lifecycle: started by App.startup, stopped by App.shutdown via cancelling
// the context passed to Start.
type worker struct {
	app      *App
	interval time.Duration
	stopOnce sync.Once
	stopped  chan struct{}
}

func newWorker(a *App) *worker {
	return &worker{
		app:      a,
		interval: 2 * time.Second,
		stopped:  make(chan struct{}),
	}
}

// Start runs the worker loop until ctx is cancelled. Safe to call exactly
// once; subsequent calls are no-ops (defensive — there's only one worker).
func (w *worker) Start(ctx context.Context) {
	go func() {
		defer close(w.stopped)
		ticker := time.NewTicker(w.interval)
		defer ticker.Stop()

		// Best-effort: drain any tasks left in "running" from a previous app
		// session — they were interrupted, mark them failed so they don't
		// hang in the UI forever.
		w.recoverOrphans()

		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				w.tickOnce(ctx)
			}
		}
	}()
}

// Wait blocks until the worker goroutine has exited. Called during shutdown.
func (w *worker) Wait() {
	<-w.stopped
}

// recoverOrphans transitions any "running" tasks to "failed" on startup.
// A task being "running" at startup means the previous app crash or restart
// happened mid-execution; we have no way to resume so we mark it failed.
// The user can hit Retry from the Tasks panel.
func (w *worker) recoverOrphans() {
	if w.app.db == nil {
		return
	}
	// Recovery is cross-profile: any interrupted task needs to be marked failed
	// regardless of which workspace owned it.
	rows, err := w.app.db.ListTasks(TaskRunning, "", 200)
	if err != nil {
		return
	}
	now := time.Now().UTC().Format(time.RFC3339)
	for _, t := range rows {
		_, _ = w.app.db.MarkTaskFailed(t.ID, now, "", "interrupted by app restart", "")
		w.app.emitTaskEvent(t.ID, t.SequenceID, TaskFailed, t.Attempts, "interrupted by app restart")
	}
}

// tickOnce picks at most one eligible task and runs it to completion before
// returning. Bounded so a long loop doesn't starve the rest of the queue
// when we later add concurrency — for v1 with one worker, blocking is fine.
func (w *worker) tickOnce(ctx context.Context) {
	if w.app.db == nil {
		return
	}
	now := time.Now().UTC().Format(time.RFC3339)
	task, ok := w.app.db.ClaimNextTask(now)
	if !ok {
		return
	}
	w.app.emitTaskEvent(task.ID, task.SequenceID, TaskRunning, task.Attempts, "")

	runID, runErr := w.app.runSequenceForTask(ctx, task)
	finishedAt := time.Now().UTC().Format(time.RFC3339)

	if runErr == nil {
		if err := w.app.db.MarkTaskSucceeded(task.ID, finishedAt, runID); err != nil {
			fmt.Fprintf(os.Stderr, "warning: task %s mark succeeded: %v\n", task.ID, err)
		}
		w.app.emitTaskEvent(task.ID, task.SequenceID, TaskSucceeded, task.Attempts, "")
		return
	}

	// Failed — decide whether to requeue.
	retryAt := ""
	if task.Attempts < task.MaxAttempts {
		retryAt = backoffNext(task.Attempts).UTC().Format(time.RFC3339)
	}
	requeued, err := w.app.db.MarkTaskFailed(task.ID, finishedAt, runID, runErr.Error(), retryAt)
	if err != nil {
		fmt.Fprintf(os.Stderr, "warning: task %s mark failed: %v\n", task.ID, err)
	}
	if requeued {
		w.app.emitTaskEvent(task.ID, task.SequenceID, TaskPending, task.Attempts, runErr.Error())
	} else {
		// Terminal failure. emitTaskEvent dual-emits a task:<id> envelope with
		// status=failed; the bus is now the only attention source (P5), so no
		// attention_items row is needed here.
		w.app.emitTaskEvent(task.ID, task.SequenceID, TaskFailed, task.Attempts, runErr.Error())
	}
}

// backoffNext returns the wall-clock time at which the (attempts)-th retry
// becomes eligible. Exponential with a 60-second cap: 2s, 4s, 8s, 16s, 32s, 60s, …
func backoffNext(attempts int) time.Time {
	secs := math.Pow(2, float64(attempts))
	if secs > 60 {
		secs = 60
	}
	return time.Now().Add(time.Duration(secs) * time.Second)
}

// newTaskID is the opaque identifier for an enqueued task.
func newTaskID() string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return "task-" + hex.EncodeToString(b[:])
}

// validateTrigger returns the canonical trigger value (or "manual" if blank).
func validateTrigger(t string) (string, error) {
	switch strings.TrimSpace(t) {
	case "", TriggerManual:
		return TriggerManual, nil
	case TriggerSchedule, TriggerWebhook, TriggerFollowup:
		return t, nil
	default:
		return "", fmt.Errorf("unknown trigger %q", t)
	}
}
