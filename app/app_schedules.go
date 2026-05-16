package main

import (
	"fmt"
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
	return rows, nil
}

// DeleteSchedule removes a schedule by ID.
func (a *App) DeleteSchedule(id string) error {
	if a.db == nil {
		return fmt.Errorf("database not initialized")
	}
	return a.db.DeleteSchedule(id)
}
