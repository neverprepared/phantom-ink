package main

import (
	"fmt"
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// Tasks (queue)
// ---------------------------------------------------------------------------

// TaskRow mirrors the tasks table. The runtime Task type lives in queue.go.
// WorkspaceProfile is snapshotted at enqueue time so the task always runs
// under the right profile context — see feedback_profiles_foundational.md.
type TaskRow struct {
	ID               string `json:"id"`
	SequenceID       string `json:"loop_id"`
	Status           string `json:"status"`
	Priority         int    `json:"priority"`
	Input            string `json:"input"`
	Cwd              string `json:"cwd"`
	Trigger          string `json:"trigger"`
	ParentTaskID     string `json:"parent_task_id"`
	WorkspaceProfile string `json:"workspace_profile"`
	EnqueuedAt       string `json:"enqueued_at"`
	ScheduledFor     string `json:"scheduled_for"`
	StartedAt        string `json:"started_at"`
	FinishedAt       string `json:"finished_at"`
	Attempts         int    `json:"attempts"`
	MaxAttempts      int    `json:"max_attempts"`
	LastError        string `json:"last_error"`
	ResultRunID      string `json:"result_run_id"`
}

func (db *DB) InsertTask(t TaskRow) error {
	_, err := db.conn.Exec(`
		INSERT INTO tasks (
			id, loop_id, status, priority, input, cwd, trigger, parent_task_id, workspace_profile,
			enqueued_at, scheduled_for, started_at, finished_at,
			attempts, max_attempts, last_error, result_run_id
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		t.ID, t.SequenceID, t.Status, t.Priority, t.Input, t.Cwd, t.Trigger, t.ParentTaskID, t.WorkspaceProfile,
		t.EnqueuedAt, t.ScheduledFor, t.StartedAt, t.FinishedAt,
		t.Attempts, t.MaxAttempts, t.LastError, t.ResultRunID)
	return err
}

// ClaimNextTask atomically transitions the highest-priority eligible pending
// task to "running" and returns it. Returns (TaskRow{}, false) when nothing
// is ready. "Eligible" means status='pending' AND (scheduled_for=” OR
// scheduled_for <= nowRFC3339).

// taskCols is the canonical tasks column list, kept in one place so the SELECT
// and the Scan can't drift out of sync.
const taskCols = `id, loop_id, status, priority, input, cwd, trigger, parent_task_id, workspace_profile,
	enqueued_at, scheduled_for, started_at, finished_at,
	attempts, max_attempts, last_error, result_run_id`

func scanTask(s rowScanner) (TaskRow, error) {
	var t TaskRow
	err := s.Scan(
		&t.ID, &t.SequenceID, &t.Status, &t.Priority, &t.Input, &t.Cwd, &t.Trigger, &t.ParentTaskID, &t.WorkspaceProfile,
		&t.EnqueuedAt, &t.ScheduledFor, &t.StartedAt, &t.FinishedAt,
		&t.Attempts, &t.MaxAttempts, &t.LastError, &t.ResultRunID)
	return t, err
}

func (db *DB) ClaimNextTask(nowRFC3339 string) (TaskRow, bool) {
	tx, err := db.conn.Begin()
	if err != nil {
		return TaskRow{}, false
	}
	defer tx.Rollback()

	t, err := scanTask(tx.QueryRow(`
		SELECT `+taskCols+`
		FROM tasks
		WHERE status = 'pending'
		  AND (scheduled_for = '' OR scheduled_for <= ?)
		ORDER BY priority DESC, enqueued_at ASC
		LIMIT 1`, nowRFC3339))
	if err != nil {
		return TaskRow{}, false
	}

	if _, err := tx.Exec(
		`UPDATE tasks SET status = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ?`,
		nowRFC3339, t.ID); err != nil {
		return TaskRow{}, false
	}
	if err := tx.Commit(); err != nil {
		return TaskRow{}, false
	}
	t.Status = "running"
	t.StartedAt = nowRFC3339
	t.Attempts++
	return t, true
}

func (db *DB) MarkTaskSucceeded(id, finishedAt, runID string) error {
	_, err := db.conn.Exec(
		`UPDATE tasks SET status = 'succeeded', finished_at = ?, result_run_id = ?, last_error = '' WHERE id = ?`,
		finishedAt, runID, id)
	return err
}

// MarkTaskFailed records a failure. If attempts < max_attempts the task is
// requeued (status reset to pending) with scheduled_for set for backoff.
// Returns true when the task is requeued.
func (db *DB) MarkTaskFailed(id, finishedAt, runID, errMsg, retryAt string) (requeued bool, err error) {
	tx, err := db.conn.Begin()
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	var attempts, maxAttempts int
	if err := tx.QueryRow(`SELECT attempts, max_attempts FROM tasks WHERE id = ?`, id).Scan(&attempts, &maxAttempts); err != nil {
		return false, err
	}
	if attempts < maxAttempts && retryAt != "" {
		if _, err := tx.Exec(
			`UPDATE tasks SET status = 'pending', scheduled_for = ?, last_error = ?, started_at = '' WHERE id = ?`,
			retryAt, errMsg, id); err != nil {
			return false, err
		}
		requeued = true
	} else {
		if _, err := tx.Exec(
			`UPDATE tasks SET status = 'failed', finished_at = ?, result_run_id = ?, last_error = ? WHERE id = ?`,
			finishedAt, runID, errMsg, id); err != nil {
			return false, err
		}
	}
	return requeued, tx.Commit()
}

func (db *DB) CancelTask(id string) error {
	res, err := db.conn.Exec(
		`UPDATE tasks SET status = 'cancelled', finished_at = ? WHERE id = ? AND status IN ('pending', 'running')`,
		time.Now().UTC().Format(time.RFC3339), id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return fmt.Errorf("task %q not pending or running", id)
	}
	return nil
}

func (db *DB) RetryTask(id string) error {
	_, err := db.conn.Exec(
		`UPDATE tasks SET status = 'pending', started_at = '', finished_at = '', last_error = '', scheduled_for = '', attempts = 0 WHERE id = ? AND status IN ('failed', 'cancelled')`,
		id)
	return err
}

func (db *DB) GetTask(id string) (TaskRow, bool) {
	t, err := scanTask(db.conn.QueryRow(`SELECT `+taskCols+` FROM tasks WHERE id = ?`, id))
	if err != nil {
		return t, false
	}
	return t, true
}

// ListTasks returns tasks filtered by status (empty = all) and workspace
// (empty = all). Limit is capped at 200 to keep the UI snappy.
func (db *DB) ListTasks(status, workspace string, limit int) ([]TaskRow, error) {
	if limit <= 0 || limit > 200 {
		limit = 200
	}
	var (
		where []string
		args  []any
	)
	if status != "" {
		where = append(where, "status = ?")
		args = append(args, status)
	}
	if workspace != "" {
		where = append(where, "workspace_profile = ?")
		args = append(args, workspace)
	}
	q := "SELECT " + taskCols + " FROM tasks"
	if len(where) > 0 {
		q += " WHERE " + strings.Join(where, " AND ")
	}
	q += " ORDER BY enqueued_at DESC LIMIT ?"
	args = append(args, limit)
	rows, err := db.conn.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []TaskRow
	for rows.Next() {
		t, err := scanTask(rows)
		if err != nil {
			continue
		}
		out = append(out, t)
	}
	return out, nil
}

// TaskStatsCounted returns task counts by status. Active tasks (pending/running)
// are always included; terminal tasks (succeeded/failed/cancelled) are filtered
// to those finished at or after `since` (RFC3339).
func (db *DB) TaskStatsCounted(since string) (pending, running, succeeded, failed, cancelled int, err error) {
	// Active tasks — not time-scoped
	rows, e := db.conn.Query(
		"SELECT status, COUNT(*) FROM tasks WHERE status IN ('pending','running') GROUP BY status")
	if e != nil {
		err = fmt.Errorf("count active tasks: %w", e)
		return
	}
	defer rows.Close()
	for rows.Next() {
		var status string
		var count int
		if rows.Scan(&status, &count) != nil {
			continue
		}
		switch status {
		case "pending":
			pending = count
		case "running":
			running = count
		}
	}
	rows.Close()

	// Terminal tasks within time window
	rows2, e := db.conn.Query(
		"SELECT status, COUNT(*) FROM tasks WHERE status IN ('succeeded','failed','cancelled') AND finished_at >= ? GROUP BY status",
		since)
	if e != nil {
		err = fmt.Errorf("count terminal tasks: %w", e)
		return
	}
	defer rows2.Close()
	for rows2.Next() {
		var status string
		var count int
		if rows2.Scan(&status, &count) != nil {
			continue
		}
		switch status {
		case "succeeded":
			succeeded = count
		case "failed":
			failed = count
		case "cancelled":
			cancelled = count
		}
	}
	return
}

// ---------------------------------------------------------------------------
