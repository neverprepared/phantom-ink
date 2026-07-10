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
	Command        string `json:"command"`    // shell target only
	IntervalS      int    `json:"interval_s"` // interval scheduling
	Enabled        bool   `json:"enabled"`
	DefaultActions string `json:"default_actions"` // raw JSON array
	LastRunAt      *int64 `json:"last_run_at"`
	LastError      string `json:"last_error"`
	CreatedAt      int64  `json:"created_at"`
	// composable target
	TargetType   string `json:"target_type"`   // "shell" | "playbook" | "loop" | "runner"
	TargetID     string `json:"target_id"`     // playbook or loop ID
	TargetPrompt string `json:"target_prompt"` // prompt text for runner target
	// time-of-day scheduling (overrides interval_s when set)
	RunAt string `json:"run_at"` // "HH:MM", e.g. "08:30"
	Days  string `json:"days"`   // "daily" | "weekdays"
	// Source identifies where the job was created. "widget" means it is
	// owned by a dashboard widget; "" means user-created via the Jobs panel.
	Source string `json:"source"`
	// OwnerWidgetID links a widget-sourced job to the dashboard widget that
	// owns it. Empty when source != "widget" or for legacy jobs.
	OwnerWidgetID string `json:"owner_widget_id"`
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

const collectJobCols = `id, profile, name, command, interval_s, enabled, default_actions,
	last_run_at, last_error, created_at,
	target_type, target_id, target_prompt, run_at, days, source, owner_widget_id`

func scanCollectJob(s rowScanner) (CollectJob, error) {
	var j CollectJob
	var lastRunAt sql.NullInt64
	err := s.Scan(&j.ID, &j.Profile, &j.Name, &j.Command, &j.IntervalS,
		&j.Enabled, &j.DefaultActions, &lastRunAt, &j.LastError, &j.CreatedAt,
		&j.TargetType, &j.TargetID, &j.TargetPrompt, &j.RunAt, &j.Days, &j.Source, &j.OwnerWidgetID)
	if lastRunAt.Valid {
		j.LastRunAt = &lastRunAt.Int64
	}
	return j, err
}

func (db *DB) ListCollectJobs(profile string) ([]CollectJob, error) {
	q := `SELECT ` + collectJobCols + ` FROM collect_jobs`
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
		j, err := scanCollectJob(rows)
		if err != nil {
			return nil, err
		}
		jobs = append(jobs, j)
	}
	return jobs, rows.Err()
}

func (db *DB) GetCollectJob(id string) (CollectJob, bool) {
	j, err := scanCollectJob(db.conn.QueryRow(`SELECT `+collectJobCols+` FROM collect_jobs WHERE id = ?`, id))
	if err != nil {
		return CollectJob{}, false
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
			 target_type, target_id, target_prompt, run_at, days, source, owner_widget_id)
		VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?)
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
			days            = excluded.days,
			source          = excluded.source,
			owner_widget_id = excluded.owner_widget_id`,
		j.ID, j.Profile, j.Name, j.Command, j.IntervalS, boolToInt(j.Enabled),
		j.DefaultActions, j.CreatedAt,
		j.TargetType, j.TargetID, j.TargetPrompt, j.RunAt, j.Days, j.Source, j.OwnerWidgetID)
	return err
}

func (db *DB) DeleteCollectJob(id string) error {
	_, err := db.conn.Exec("DELETE FROM collect_jobs WHERE id = ?", id)
	return err
}

// GetCollectJobByOwner returns the job owned by a specific dashboard widget
// (profile + owner_widget_id). Used to make widget registration idempotent so
// a widget re-registering on remount reuses its job instead of creating a dup.
// When multiple exist (from a past race), the oldest wins for stability.
func (db *DB) GetCollectJobByOwner(profile, ownerWidgetID string) (CollectJob, bool) {
	if ownerWidgetID == "" {
		return CollectJob{}, false
	}
	var id string
	err := db.conn.QueryRow(
		`SELECT id FROM collect_jobs
		 WHERE profile = ? AND owner_widget_id = ?
		 ORDER BY created_at ASC, id ASC LIMIT 1`,
		profile, ownerWidgetID).Scan(&id)
	if err != nil {
		return CollectJob{}, false
	}
	return db.GetCollectJob(id)
}

// DeleteCollectJobsByOwner removes every job owned by a widget (profile +
// owner_widget_id). Called when a widget is removed so its job doesn't linger
// as an orphan that keeps running on the scheduler.
func (db *DB) DeleteCollectJobsByOwner(profile, ownerWidgetID string) (int, error) {
	if ownerWidgetID == "" {
		return 0, nil
	}
	res, err := db.conn.Exec(
		"DELETE FROM collect_jobs WHERE profile = ? AND owner_widget_id = ?",
		profile, ownerWidgetID)
	if err != nil {
		return 0, err
	}
	n, _ := res.RowsAffected()
	return int(n), nil
}

// DedupeWidgetJobs collapses duplicate widget-owned jobs to one per
// (profile, owner_widget_id), keeping the oldest. Concurrent auto-registration
// (a widget's fetch firing several times before the first create committed)
// previously created several rows for the same widget; this heals that state.
// Returns the number of rows removed.
func (db *DB) DedupeWidgetJobs() (int, error) {
	res, err := db.conn.Exec(`
		DELETE FROM collect_jobs
		WHERE owner_widget_id != ''
		  AND id NOT IN (
		    SELECT id FROM (
		      SELECT id, ROW_NUMBER() OVER (
		        PARTITION BY profile, owner_widget_id
		        ORDER BY created_at ASC, id ASC
		      ) AS rn
		      FROM collect_jobs
		      WHERE owner_widget_id != ''
		    ) WHERE rn = 1
		  )`)
	if err != nil {
		return 0, err
	}
	n, _ := res.RowsAffected()
	return int(n), nil
}

// PruneOrphanWidgetJobs deletes widget-owned collect jobs whose owning widget
// no longer exists in that profile's saved dashboard layout. Rebuilding a
// dashboard (and the earlier cross-profile layout clone) left jobs bound to
// widget ids that are gone; they keep running on the scheduler and pile up as
// duplicates in the Jobs pane. Deleting them from the pane didn't stick because
// nothing re-links an orphan — but nothing removed it either.
//
// Only profiles that HAVE a saved layout are pruned: a missing layout can't be
// distinguished from "not loaded yet". Runs once at startup, when the saved
// layout is authoritative (no in-memory unsaved widgets exist yet). Returns the
// number of rows removed.
func (db *DB) PruneOrphanWidgetJobs() (int, error) {
	rows, err := db.conn.Query(`SELECT key, value FROM settings WHERE key LIKE 'dashboard_layout:%'`)
	if err != nil {
		return 0, err
	}
	type layoutT struct {
		Widgets []struct {
			ID string `json:"id"`
		} `json:"widgets"`
	}
	profileIDs := map[string]map[string]bool{}
	for rows.Next() {
		var key, val string
		if err := rows.Scan(&key, &val); err != nil {
			rows.Close()
			return 0, err
		}
		profile := strings.TrimPrefix(key, "dashboard_layout:")
		var lay layoutT
		if json.Unmarshal([]byte(val), &lay) != nil {
			continue
		}
		ids := map[string]bool{}
		for _, w := range lay.Widgets {
			if w.ID != "" {
				ids[w.ID] = true
			}
		}
		profileIDs[profile] = ids
	}
	rows.Close()

	total := 0
	for profile, ids := range profileIDs {
		jr, err := db.conn.Query(
			`SELECT id, owner_widget_id FROM collect_jobs WHERE profile = ? AND owner_widget_id != ''`,
			profile)
		if err != nil {
			return total, err
		}
		var toDelete []string
		for jr.Next() {
			var id, owner string
			if err := jr.Scan(&id, &owner); err != nil {
				jr.Close()
				return total, err
			}
			if !ids[owner] {
				toDelete = append(toDelete, id)
			}
		}
		jr.Close()
		for _, id := range toDelete {
			if _, err := db.conn.Exec(`DELETE FROM collect_jobs WHERE id = ?`, id); err != nil {
				return total, err
			}
			total++
		}
	}
	return total, nil
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
	inflight sync.Map // job.ID → struct{}: prevents concurrent runs of the same job
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
		if _, loaded := s.inflight.LoadOrStore(job.ID, struct{}{}); loaded {
			continue
		}
		j := job
		go func() {
			defer s.inflight.Delete(j.ID)
			s.runJob(j)
		}()
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
		// P5: entries that carry actions[] become attention-eligible bus rows.
		// The bus is the single attention source; the legacy scrape of
		// collected_entries in app_attention.go has been retired.
		s.app.emitCollectedEntryEnvelope(job, e)
	}
	if len(entries) > 0 {
		s.app.emitCollectUpdate(job.Profile)
		// Emit automation events for each collected entry.
		if s.app.automations != nil {
			for _, e := range entries {
				entry := e
				s.app.automations.Emit(AutomationEvent{
					Type:    "entry_created",
					Profile: job.Profile,
					Entry:   &entry,
				})
			}
		}
	}
	// Emit job_complete event.
	if s.app.automations != nil {
		j := job
		s.app.automations.Emit(AutomationEvent{
			Type:    "job_complete",
			Profile: job.Profile,
			Job:     &j,
		})
	}
}

// dispatchCollectJob executes a job according to its target_type.
// Shell jobs return timeline entries; playbook/loop/runner jobs manage their
// own output and return nil entries (last_run_at is still recorded).
func (a *App) dispatchCollectJob(job CollectJob) ([]CollectedEntry, error) {
	// Dashboard-widget jobs emit a scalar value, not a timeline-entries
	// array. Take the simpler dispatch path.
	if job.Source == "widget" {
		return a.runWidgetCommand(job)
	}
	switch job.TargetType {
	case "playbook":
		_, err := a.RunPlaybook(job.TargetID, job.Profile, "")
		return nil, err
	case "loop":
		_, err := a.RunSequence(job.TargetID, "", "")
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

// runWidgetCommand runs the job's shell command and stores its stdout as a
// single CollectedEntry — no JSON-entries-array contract. EntryID matches
// the widget's GetLatestCollectedEntry lookup key (job.Name == config.label).
func (a *App) runWidgetCommand(job CollectJob) ([]CollectedEntry, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, "/bin/sh", "-c", job.Command)
	cmd.Env = a.resolveProfileEnv(job.Profile)
	out, err := cmd.Output()
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) && len(exitErr.Stderr) > 0 {
			return nil, fmt.Errorf("exit %d: %s", exitErr.ExitCode(), strings.TrimSpace(string(exitErr.Stderr)))
		}
		return nil, fmt.Errorf("command failed: %w", err)
	}
	value := strings.TrimSpace(string(out))
	now := time.Now().UnixMilli()
	entry := CollectedEntry{
		JobID:       job.ID,
		EntryID:     job.Name,
		Profile:     job.Profile,
		Kind:        "metric",
		Title:       job.Name,
		Value:       value,
		Status:      "active",
		CollectedAt: now,
		Metadata:    json.RawMessage(`{}`),
		Actions:     json.RawMessage(`[]`),
	}
	return []CollectedEntry{entry}, nil
}

// runCollectCommand executes the job's command and parses the JSON output.
func (a *App) runCollectCommand(job CollectJob) ([]CollectedEntry, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, "/bin/sh", "-c", job.Command)
	cmd.Env = a.resolveProfileEnv(job.Profile)

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

// collectJobSaveMu serialises the find-or-create in SaveCollectJob so two
// concurrent widget registrations for the same (profile, owner_widget_id)
// resolve to a single row instead of racing to insert duplicates.
var collectJobSaveMu sync.Mutex

func (a *App) SaveCollectJob(job CollectJob) (CollectJob, error) {
	if a.db == nil {
		return CollectJob{}, fmt.Errorf("db not ready")
	}

	// Idempotent registration for widget-owned jobs: if this is a create
	// (no id) for a widget that already has a job, reuse that job's id so we
	// update it in place rather than spawn a duplicate. The mutex makes the
	// lookup+insert atomic against concurrent registrations from the same
	// widget (a metric widget's fetch can fire several times at once).
	if job.ID == "" && job.OwnerWidgetID != "" {
		collectJobSaveMu.Lock()
		defer collectJobSaveMu.Unlock()
		if existing, ok := a.db.GetCollectJobByOwner(job.Profile, job.OwnerWidgetID); ok {
			job.ID = existing.ID
			job.CreatedAt = existing.CreatedAt
		}
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

// DeleteCollectJobsByOwner removes the job(s) a dashboard widget owns. Called
// when the widget is removed so its collect job stops running. Safe to call
// with an empty owner (no-op).
func (a *App) DeleteCollectJobsByOwner(profile, ownerWidgetID string) (int, error) {
	if a.db == nil {
		return 0, fmt.Errorf("db not ready")
	}
	return a.db.DeleteCollectJobsByOwner(profile, ownerWidgetID)
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
