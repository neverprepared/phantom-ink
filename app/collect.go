package main

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// ── Types ──────────────────────────────────────────────────────────────────

type CollectJob struct {
	ID             string `json:"id"`
	Profile        string `json:"profile"`
	Name           string `json:"name"`
	Command        string `json:"command"`       // shell target only
	IntervalS      int    `json:"interval_s"`    // interval scheduling
	Enabled        bool   `json:"enabled"`
	DefaultActions string `json:"default_actions"` // raw JSON array
	LastRunAt      *int64 `json:"last_run_at"`
	LastError      string `json:"last_error"`
	CreatedAt      int64  `json:"created_at"`
	// composable target
	TargetType   string `json:"target_type"`   // "shell" | "playbook" | "chain" | "runner"
	TargetID     string `json:"target_id"`     // playbook or chain ID
	TargetPrompt string `json:"target_prompt"` // prompt text for runner target
	// time-of-day scheduling (overrides interval_s when set)
	RunAt string `json:"run_at"` // "HH:MM", e.g. "08:30"
	Days  string `json:"days"`  // "daily" | "weekdays"
}

type CollectedEntry struct {
	JobID       string          `json:"job_id"`
	EntryID     string          `json:"entry_id"`
	Profile     string          `json:"profile"`
	Kind        string          `json:"kind"`
	Title       string          `json:"title"`
	Description string          `json:"description"`
	Value       string          `json:"value"`
	URL         string          `json:"url"`
	StartAt     *int64          `json:"start_at"`
	EndAt       *int64          `json:"end_at"`
	Status      string          `json:"status"`
	Tags        []string        `json:"tags"`
	Metadata    json.RawMessage `json:"metadata"`
	Actions     json.RawMessage `json:"actions"`
	CollectedAt int64           `json:"collected_at"`
}

// scriptEntry mirrors the JSON contract in contracts/timeline-entry.schema.json.
type scriptEntry struct {
	ID          string          `json:"id"`
	Kind        string          `json:"kind"`
	Title       string          `json:"title"`
	Description string          `json:"description"`
	Value       *string         `json:"value"`
	URL         *string         `json:"url"`
	StartAt     *int64          `json:"start_at"`
	EndAt       *int64          `json:"end_at"`
	Status      string          `json:"status"`
	Tags        []string        `json:"tags"`
	Metadata    json.RawMessage `json:"metadata"`
	Actions     json.RawMessage `json:"actions"`
}

// ── DB helpers ─────────────────────────────────────────────────────────────

func (db *DB) ListCollectJobs(profile string) ([]CollectJob, error) {
	q := `SELECT id, profile, name, command, interval_s, enabled, default_actions,
	             last_run_at, last_error, created_at,
	             target_type, target_id, target_prompt, run_at, days
	      FROM collect_jobs`
	args := []any{}
	if profile != "" {
		q += " WHERE profile = ?"
		args = append(args, profile)
	}
	q += " ORDER BY name ASC"
	rows, err := db.conn.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var jobs []CollectJob
	for rows.Next() {
		var j CollectJob
		var lastRunAt sql.NullInt64
		if err := rows.Scan(&j.ID, &j.Profile, &j.Name, &j.Command, &j.IntervalS,
			&j.Enabled, &j.DefaultActions, &lastRunAt, &j.LastError, &j.CreatedAt,
			&j.TargetType, &j.TargetID, &j.TargetPrompt, &j.RunAt, &j.Days); err != nil {
			return nil, err
		}
		if lastRunAt.Valid {
			j.LastRunAt = &lastRunAt.Int64
		}
		jobs = append(jobs, j)
	}
	return jobs, rows.Err()
}

func (db *DB) GetCollectJob(id string) (CollectJob, bool) {
	var j CollectJob
	var lastRunAt sql.NullInt64
	err := db.conn.QueryRow(`SELECT id, profile, name, command, interval_s, enabled,
		default_actions, last_run_at, last_error, created_at,
		target_type, target_id, target_prompt, run_at, days
		FROM collect_jobs WHERE id = ?`, id).
		Scan(&j.ID, &j.Profile, &j.Name, &j.Command, &j.IntervalS, &j.Enabled,
			&j.DefaultActions, &lastRunAt, &j.LastError, &j.CreatedAt,
			&j.TargetType, &j.TargetID, &j.TargetPrompt, &j.RunAt, &j.Days)
	if err != nil {
		return CollectJob{}, false
	}
	if lastRunAt.Valid {
		j.LastRunAt = &lastRunAt.Int64
	}
	return j, true
}

func (db *DB) UpsertCollectJob(j CollectJob) error {
	if j.DefaultActions == "" {
		j.DefaultActions = "[]"
	}
	if j.TargetType == "" {
		j.TargetType = "shell"
	}
	_, err := db.conn.Exec(`
		INSERT INTO collect_jobs
			(id, profile, name, command, interval_s, enabled, default_actions, last_error, created_at,
			 target_type, target_id, target_prompt, run_at, days)
		VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			profile         = excluded.profile,
			name            = excluded.name,
			command         = excluded.command,
			interval_s      = excluded.interval_s,
			enabled         = excluded.enabled,
			default_actions = excluded.default_actions,
			target_type     = excluded.target_type,
			target_id       = excluded.target_id,
			target_prompt   = excluded.target_prompt,
			run_at          = excluded.run_at,
			days            = excluded.days`,
		j.ID, j.Profile, j.Name, j.Command, j.IntervalS, boolToInt(j.Enabled),
		j.DefaultActions, j.CreatedAt,
		j.TargetType, j.TargetID, j.TargetPrompt, j.RunAt, j.Days)
	return err
}

func (db *DB) DeleteCollectJob(id string) error {
	_, err := db.conn.Exec("DELETE FROM collect_jobs WHERE id = ?", id)
	return err
}

func (db *DB) markCollectJobRun(id string, ranAt int64, runErr string) error {
	_, err := db.conn.Exec(
		"UPDATE collect_jobs SET last_run_at = ?, last_error = ? WHERE id = ?",
		ranAt, runErr, id)
	return err
}

func (db *DB) UpsertCollectedEntry(e CollectedEntry) error {
	tags, _ := json.Marshal(e.Tags)
	metadata := e.Metadata
	if metadata == nil {
		metadata = json.RawMessage("{}")
	}
	actions := e.Actions
	if actions == nil {
		actions = json.RawMessage("[]")
	}
	_, err := db.conn.Exec(`
		INSERT INTO collected_entries
			(job_id, entry_id, profile, kind, title, description, value, url,
			 start_at, end_at, status, tags, metadata, actions, collected_at)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
		ON CONFLICT(job_id, entry_id) DO UPDATE SET
			profile      = excluded.profile,
			kind         = excluded.kind,
			title        = excluded.title,
			description  = excluded.description,
			value        = excluded.value,
			url          = excluded.url,
			start_at     = excluded.start_at,
			end_at       = excluded.end_at,
			status       = excluded.status,
			tags         = excluded.tags,
			metadata     = excluded.metadata,
			actions      = excluded.actions,
			collected_at = excluded.collected_at`,
		e.JobID, e.EntryID, e.Profile, e.Kind, e.Title, e.Description,
		e.Value, e.URL, e.StartAt, e.EndAt, e.Status,
		string(tags), string(metadata), string(actions), e.CollectedAt)
	return err
}

func (db *DB) ListCollectedEntries(profile, kind, tag string) ([]CollectedEntry, error) {
	q := `SELECT job_id, entry_id, profile, kind, title, description, value, url,
	             start_at, end_at, status, tags, metadata, actions, collected_at
	      FROM collected_entries WHERE 1=1`
	args := []any{}
	if profile != "" {
		q += " AND profile = ?"
		args = append(args, profile)
	}
	if kind != "" {
		q += " AND kind = ?"
		args = append(args, kind)
	}
	if tag != "" {
		q += ` AND EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)`
		args = append(args, tag)
	}
	q += " ORDER BY COALESCE(start_at, collected_at) DESC"
	rows, err := db.conn.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanCollectedEntries(rows)
}

func (db *DB) GetLatestCollectedEntry(jobID, entryID string) (CollectedEntry, bool) {
	rows, err := db.conn.Query(`
		SELECT job_id, entry_id, profile, kind, title, description, value, url,
		       start_at, end_at, status, tags, metadata, actions, collected_at
		FROM collected_entries WHERE job_id = ? AND entry_id = ?`, jobID, entryID)
	if err != nil {
		return CollectedEntry{}, false
	}
	defer rows.Close()
	entries, err := scanCollectedEntries(rows)
	if err != nil || len(entries) == 0 {
		return CollectedEntry{}, false
	}
	return entries[0], true
}

func scanCollectedEntries(rows *sql.Rows) ([]CollectedEntry, error) {
	var entries []CollectedEntry
	for rows.Next() {
		var e CollectedEntry
		var startAt, endAt sql.NullInt64
		var tagsRaw, metaRaw, actionsRaw string
		if err := rows.Scan(&e.JobID, &e.EntryID, &e.Profile, &e.Kind, &e.Title,
			&e.Description, &e.Value, &e.URL, &startAt, &endAt, &e.Status,
			&tagsRaw, &metaRaw, &actionsRaw, &e.CollectedAt); err != nil {
			return nil, err
		}
		if startAt.Valid {
			e.StartAt = &startAt.Int64
		}
		if endAt.Valid {
			e.EndAt = &endAt.Int64
		}
		_ = json.Unmarshal([]byte(tagsRaw), &e.Tags)
		e.Metadata = json.RawMessage(metaRaw)
		e.Actions = json.RawMessage(actionsRaw)
		entries = append(entries, e)
	}
	return entries, rows.Err()
}

// ── Collect scheduler ──────────────────────────────────────────────────────

type collectScheduler struct {
	app      *App
	interval time.Duration
	stopOnce sync.Once
	stopped  chan struct{}
}

func newCollectScheduler(a *App) *collectScheduler {
	return &collectScheduler{
		app:      a,
		interval: 30 * time.Second,
		stopped:  make(chan struct{}),
	}
}

func (s *collectScheduler) Start(ctx context.Context) {
	go func() {
		defer close(s.stopped)
		ticker := time.NewTicker(s.interval)
		defer ticker.Stop()
		s.tick() // run immediately on startup to refresh stale data
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				s.tick()
			}
		}
	}()
}

func (s *collectScheduler) Wait() { <-s.stopped }

func (s *collectScheduler) tick() {
	if s.app.db == nil {
		return
	}
	jobs, err := s.app.db.ListCollectJobs("")
	if err != nil {
		fmt.Fprintf(os.Stderr, "collect: list jobs: %v\n", err)
		return
	}
	now := time.Now()
	for _, job := range jobs {
		if !job.Enabled {
			continue
		}
		if !collectJobIsDue(job, now) {
			continue
		}
		go s.runJob(job)
	}
}

// collectJobIsDue returns true when a job should fire at the given moment.
// Time-of-day jobs (run_at != "") fire once per day within the matching minute.
// Interval jobs (run_at == "") fire when enough time has passed since last run.
func collectJobIsDue(job CollectJob, now time.Time) bool {
	if job.RunAt != "" {
		if job.Days == "weekdays" {
			wd := now.Weekday()
			if wd == time.Saturday || wd == time.Sunday {
				return false
			}
		}
		// Parse "HH:MM" and check if now is within that minute today.
		var h, m int
		if _, err := fmt.Sscanf(job.RunAt, "%d:%d", &h, &m); err != nil {
			return false
		}
		target := time.Date(now.Year(), now.Month(), now.Day(), h, m, 0, 0, now.Location())
		if now.Before(target) || now.After(target.Add(time.Minute)) {
			return false
		}
		// Already ran today?
		if job.LastRunAt != nil {
			last := time.UnixMilli(*job.LastRunAt)
			if last.Year() == now.Year() && last.YearDay() == now.YearDay() {
				return false
			}
		}
		return true
	}
	// Interval mode.
	if job.LastRunAt == nil {
		return true
	}
	elapsed := now.UnixMilli() - *job.LastRunAt
	return elapsed >= int64(job.IntervalS)*1000
}

func (s *collectScheduler) runJob(job CollectJob) {
	now := time.Now().UnixMilli()
	entries, runErr := s.app.dispatchCollectJob(job)
	errStr := ""
	if runErr != nil {
		errStr = runErr.Error()
		fmt.Fprintf(os.Stderr, "collect: job %q (%s): %v\n", job.Name, job.ID, runErr)
	}
	if err := s.app.db.markCollectJobRun(job.ID, now, errStr); err != nil {
		fmt.Fprintf(os.Stderr, "collect: mark run %s: %v\n", job.ID, err)
	}
	for _, e := range entries {
		if err := s.app.db.UpsertCollectedEntry(e); err != nil {
			fmt.Fprintf(os.Stderr, "collect: upsert entry %s/%s: %v\n", job.ID, e.EntryID, err)
		}
	}
	if len(entries) > 0 {
		s.app.emitCollectUpdate(job.Profile)
	}
}

// dispatchCollectJob executes a job according to its target_type.
// Shell jobs return timeline entries; playbook/chain/runner jobs manage their
// own output and return nil entries (last_run_at is still recorded).
func (a *App) dispatchCollectJob(job CollectJob) ([]CollectedEntry, error) {
	switch job.TargetType {
	case "playbook":
		_, err := a.RunPlaybook(job.TargetID, job.Profile, "")
		return nil, err
	case "chain":
		_, err := a.RunChain(job.TargetID, "", "")
		return nil, err
	case "runner":
		// Fire as a one-shot shell command using the local claude binary.
		runnerJob := job
		runnerJob.Command = fmt.Sprintf("claude --dangerously-skip-permissions -p %q", job.TargetPrompt)
		return a.runCollectCommand(runnerJob)
	default: // "shell" or legacy empty
		return a.runCollectCommand(job)
	}
}

// runCollectCommand executes the job's command and parses the JSON output.
func (a *App) runCollectCommand(job CollectJob) ([]CollectedEntry, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	var cmd *exec.Cmd
	if job.Profile != "" {
		if wh := a.profileWorkspaceHome(job.Profile); wh != "" {
			if direnvBin := findDirenv(); direnvBin != "" {
				cmd = exec.CommandContext(ctx, direnvBin, "exec", wh, "/bin/sh", "-c", job.Command)
				cmd.Env = os.Environ()
			}
		}
	}
	if cmd == nil {
		cmd = exec.CommandContext(ctx, "/bin/sh", "-c", job.Command)
		cmd.Env = profileEnv(job.Profile)
	}

	out, err := cmd.Output()
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) && len(exitErr.Stderr) > 0 {
			return nil, fmt.Errorf("exit %d: %s", exitErr.ExitCode(), strings.TrimSpace(string(exitErr.Stderr)))
		}
		return nil, fmt.Errorf("command failed: %w", err)
	}

	var raw []scriptEntry
	if err := json.Unmarshal(out, &raw); err != nil {
		return nil, fmt.Errorf("invalid JSON output: %w", err)
	}

	now := time.Now().UnixMilli()
	entries := make([]CollectedEntry, 0, len(raw))
	for _, r := range raw {
		if r.ID == "" || r.Kind == "" || r.Title == "" {
			continue // skip entries missing required fields
		}
		e := CollectedEntry{
			JobID:       job.ID,
			EntryID:     r.ID,
			Profile:     job.Profile,
			Kind:        r.Kind,
			Title:       r.Title,
			Description: r.Description,
			Status:      r.Status,
			Tags:        r.Tags,
			Metadata:    r.Metadata,
			Actions:     r.Actions,
			CollectedAt: now,
		}
		if r.Value != nil {
			e.Value = *r.Value
		}
		if r.URL != nil {
			e.URL = *r.URL
		}
		if e.Status == "" {
			e.Status = "active"
		}
		e.StartAt = r.StartAt
		e.EndAt = r.EndAt
		entries = append(entries, e)
	}
	return entries, nil
}

func (a *App) emitCollectUpdate(profile string) {
	if a.ctx != nil {
		runtime.EventsEmit(a.ctx, "collect:update", profile)
	}
}

// ── Wails-bound methods ────────────────────────────────────────────────────

func (a *App) ListCollectJobs(profile string) ([]CollectJob, error) {
	if a.db == nil {
		return nil, fmt.Errorf("db not ready")
	}
	return a.db.ListCollectJobs(profile)
}

func (a *App) SaveCollectJob(job CollectJob) (CollectJob, error) {
	if a.db == nil {
		return CollectJob{}, fmt.Errorf("db not ready")
	}
	if job.ID == "" {
		job.ID = newCollectJobID()
		job.CreatedAt = time.Now().UnixMilli()
	}
	if job.TargetType == "" {
		job.TargetType = "shell"
	}
	if job.IntervalS <= 0 && job.RunAt == "" {
		job.IntervalS = 300
	}
	if err := a.db.UpsertCollectJob(job); err != nil {
		return CollectJob{}, err
	}
	return job, nil
}

func (a *App) DeleteCollectJob(id string) error {
	if a.db == nil {
		return fmt.Errorf("db not ready")
	}
	return a.db.DeleteCollectJob(id)
}

func (a *App) RunCollectJobNow(id string) error {
	if a.db == nil {
		return fmt.Errorf("db not ready")
	}
	job, ok := a.db.GetCollectJob(id)
	if !ok {
		return fmt.Errorf("job %s not found", id)
	}
	go a.collectScheduler.runJob(job)
	return nil
}

func (a *App) ListCollectedEntries(profile, kind, tag string) ([]CollectedEntry, error) {
	if a.db == nil {
		return nil, fmt.Errorf("db not ready")
	}
	return a.db.ListCollectedEntries(profile, kind, tag)
}

func (a *App) GetLatestCollectedEntry(jobID, entryID string) (*CollectedEntry, error) {
	if a.db == nil {
		return nil, fmt.Errorf("db not ready")
	}
	e, ok := a.db.GetLatestCollectedEntry(jobID, entryID)
	if !ok {
		return nil, nil
	}
	return &e, nil
}

func newCollectJobID() string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return "cjob-" + hex.EncodeToString(b[:])
}
