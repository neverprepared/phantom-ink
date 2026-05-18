package main

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// Schedules — Wails bindings
// ---------------------------------------------------------------------------

// Schedule is the JS-facing shape, mirroring ScheduleRow. We round-trip the
// stored row directly since there's no derived state worth hiding.
type Schedule = ScheduleRow

// SaveSchedule creates or updates a cron schedule. A blank ID generates a new
// one. The cron expression is validated against the standard 5-field grammar
// (plus @hourly/@daily/@weekly descriptors) — invalid expressions fail here
// rather than silently in the scheduler tick.
func (a *App) SaveSchedule(s Schedule) (Schedule, error) {
	if a.db == nil {
		return Schedule{}, fmt.Errorf("database not initialized")
	}
	if strings.TrimSpace(s.ChainID) == "" {
		return Schedule{}, fmt.Errorf("chain_id is required")
	}
	if _, ok := a.db.GetChain(s.ChainID); !ok {
		return Schedule{}, fmt.Errorf("chain %q not found", s.ChainID)
	}
	if err := validateCronExpr(s.CronExpr); err != nil {
		return Schedule{}, fmt.Errorf("invalid cron expression %q: %w", s.CronExpr, err)
	}
	// Snapshot the active profile on first save so cron firings stay under
	// the right workspace regardless of who's at the keyboard at fire time.
	if strings.TrimSpace(s.WorkspaceProfile) == "" {
		s.WorkspaceProfile = a.activeProfileName()
	}
	if s.WorkspaceProfile == "" {
		return Schedule{}, fmt.Errorf("no active profile — set one before saving schedules")
	}
	if _, err := a.findProfile(s.WorkspaceProfile); err != nil {
		return Schedule{}, fmt.Errorf("workspace_profile: %w", err)
	}
	now := time.Now().UTC().Format(time.RFC3339)
	if s.ID == "" {
		s.ID = newScheduleID()
		s.CreatedAt = now
	}
	s.UpdatedAt = now
	if err := a.db.UpsertSchedule(s); err != nil {
		return Schedule{}, err
	}
	return s, nil
}

// ListSchedules returns schedules for a chain. Empty chainID returns all.
// NextFireAt is computed here so the UI can show "next: …" without needing
// its own cron parser.
func (a *App) ListSchedules(chainID string) ([]Schedule, error) {
	if a.db == nil {
		return []Schedule{}, fmt.Errorf("database not initialized")
	}
	rows, err := a.db.ListSchedules(chainID)
	if err != nil {
		return nil, err
	}
	if rows == nil {
		rows = []Schedule{}
	}
	now := time.Now().UTC()
	for i := range rows {
		rows[i].NextFireAt = nextFireFor(rows[i], now)
	}
	return rows, nil
}

// UpcomingFire is one entry in the dashboard "upcoming work" list — a single
// schedule with its next computed fire time and the target chain.
type UpcomingFire struct {
	ScheduleID string `json:"schedule_id"`
	ChainID    string `json:"chain_id"`
	ChainName  string `json:"chain_name"`
	CronExpr   string `json:"cron_expr"`
	NextFireAt string `json:"next_fire_at"`
}

// ListUpcomingFires returns the next N upcoming schedule fires across all
// enabled schedules, sorted ascending. Limit defaults to 10. Used by the
// Dashboard overview.
func (a *App) ListUpcomingFires(limit int) ([]UpcomingFire, error) {
	if a.db == nil {
		return []UpcomingFire{}, fmt.Errorf("database not initialized")
	}
	if limit <= 0 {
		limit = 10
	}
	rows, err := a.db.ListSchedules("")
	if err != nil {
		return nil, err
	}
	chainsByID := make(map[string]string)
	if list, err := a.db.ListChains(); err == nil {
		for _, c := range list {
			chainsByID[c.ID] = c.Name
		}
	}
	now := time.Now().UTC()
	out := make([]UpcomingFire, 0, len(rows))
	for _, r := range rows {
		if !r.Enabled {
			continue
		}
		next := nextFireFor(r, now)
		if next == "" {
			continue
		}
		out = append(out, UpcomingFire{
			ScheduleID: r.ID, ChainID: r.ChainID,
			ChainName: chainsByID[r.ChainID], CronExpr: r.CronExpr,
			NextFireAt: next,
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].NextFireAt < out[j].NextFireAt })
	if len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

// TaskStats is a small aggregation used by the Dashboard. WindowHours bounds
// the count window (most recent N hours). Default 24.
type TaskStats struct {
	WindowHours int `json:"window_hours"`
	Pending     int `json:"pending"`
	Running     int `json:"running"`
	Succeeded   int `json:"succeeded"`
	Failed      int `json:"failed"`
	Cancelled   int `json:"cancelled"`
}

// GetTaskStats returns counts in the window. Pending/Running are always live
// (window is irrelevant — they haven't finished yet). Succeeded/Failed/
// Cancelled are scoped to finished_at within the window.
func (a *App) GetTaskStats(windowHours int) (TaskStats, error) {
	if a.db == nil {
		return TaskStats{}, fmt.Errorf("database not initialized")
	}
	if windowHours <= 0 {
		windowHours = 24
	}
	since := time.Now().UTC().Add(-time.Duration(windowHours) * time.Hour).Format(time.RFC3339)
	stats := TaskStats{WindowHours: windowHours}
	tasks, err := a.db.ListTasks("", 500)
	if err != nil {
		return stats, err
	}
	for _, t := range tasks {
		switch t.Status {
		case TaskPending:
			stats.Pending++
		case TaskRunning:
			stats.Running++
		case TaskSucceeded:
			if t.FinishedAt >= since {
				stats.Succeeded++
			}
		case TaskFailed:
			if t.FinishedAt >= since {
				stats.Failed++
			}
		case TaskCancelled:
			if t.FinishedAt >= since {
				stats.Cancelled++
			}
		}
	}
	return stats, nil
}

// nextFireFor returns the RFC3339 timestamp of the next scheduled fire after
// `now`, or "" when the cron expression is invalid or the schedule disabled.
func nextFireFor(s Schedule, now time.Time) string {
	if !s.Enabled {
		return ""
	}
	sched, err := cronParser.Parse(s.CronExpr)
	if err != nil {
		return ""
	}
	return sched.Next(now).UTC().Format(time.RFC3339)
}

// DeleteSchedule removes a schedule by ID.
func (a *App) DeleteSchedule(id string) error {
	if a.db == nil {
		return fmt.Errorf("database not initialized")
	}
	return a.db.DeleteSchedule(id)
}
