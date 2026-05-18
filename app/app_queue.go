package main

import (
	"fmt"
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// Task queue — Wails bindings
// ---------------------------------------------------------------------------

// EnqueueTaskRequest is the JS-facing payload for enqueuing a chain run as
// a durable task. Most fields are optional; chain_id is required.
// WorkspaceProfile defaults to the currently-active profile — it's snapshotted
// here so the task always runs in the right context regardless of who's at
// the keyboard when the worker picks it up.
type EnqueueTaskRequest struct {
	ChainID          string `json:"chain_id"`
	Input            string `json:"input"`
	Cwd              string `json:"cwd"`
	Priority         int    `json:"priority"`
	MaxAttempts      int    `json:"max_attempts"`
	Trigger          string `json:"trigger"`
	ParentTaskID     string `json:"parent_task_id"`
	WorkspaceProfile string `json:"workspace_profile"`
	ScheduledFor     string `json:"scheduled_for"`
}

// EnqueueTask validates the request, persists a pending task, and returns the
// task ID. The in-app worker will pick it up on the next tick.
func (a *App) EnqueueTask(req EnqueueTaskRequest) (string, error) {
	if a.db == nil {
		return "", fmt.Errorf("database not initialized")
	}
	if strings.TrimSpace(req.ChainID) == "" {
		return "", fmt.Errorf("chain_id is required")
	}
	if _, ok := a.db.GetChain(req.ChainID); !ok {
		return "", fmt.Errorf("chain %q not found", req.ChainID)
	}
	trigger, err := validateTrigger(req.Trigger)
	if err != nil {
		return "", err
	}
	maxAttempts := req.MaxAttempts
	if maxAttempts < 1 {
		maxAttempts = 1
	}

	// Snapshot the active profile if the caller didn't pin one explicitly.
	// An empty workspace_profile at run time would be rejected by resolveCwd,
	// so surface the error here rather than later.
	profileName := strings.TrimSpace(req.WorkspaceProfile)
	if profileName == "" {
		profileName = a.activeProfileName()
	}
	if profileName == "" {
		return "", fmt.Errorf("no active profile — set one before enqueuing tasks")
	}
	if _, err := a.findProfile(profileName); err != nil {
		return "", fmt.Errorf("workspace_profile: %w", err)
	}

	t := TaskRow{
		ID:               newTaskID(),
		ChainID:          req.ChainID,
		Status:           TaskPending,
		Priority:         req.Priority,
		Input:            req.Input,
		Cwd:              req.Cwd,
		Trigger:          trigger,
		ParentTaskID:     req.ParentTaskID,
		WorkspaceProfile: profileName,
		EnqueuedAt:       time.Now().UTC().Format(time.RFC3339),
		ScheduledFor:     req.ScheduledFor,
		MaxAttempts:      maxAttempts,
	}
	if err := a.db.InsertTask(t); err != nil {
		return "", err
	}
	a.emitTaskEvent(t.ID, t.ChainID, TaskPending, 0, "")
	return t.ID, nil
}

// ListTasks returns recent tasks, newest first. Empty status returns all.
func (a *App) ListTasks(status string, limit int) ([]TaskRow, error) {
	if a.db == nil {
		return []TaskRow{}, fmt.Errorf("database not initialized")
	}
	rows, err := a.db.ListTasks(status, limit)
	if err != nil {
		return nil, err
	}
	if rows == nil {
		rows = []TaskRow{}
	}
	return rows, nil
}

// GetTask returns a single task by ID, or an error if not found.
func (a *App) GetTask(id string) (TaskRow, error) {
	if a.db == nil {
		return TaskRow{}, fmt.Errorf("database not initialized")
	}
	t, ok := a.db.GetTask(id)
	if !ok {
		return TaskRow{}, fmt.Errorf("task %q not found", id)
	}
	return t, nil
}

// CancelTask transitions a pending or running task to "cancelled". A running
// task's subprocess will still complete naturally; the worker will record the
// outcome but the user-visible state stays "cancelled". Future: hard-kill via
// shared context cancellation.
func (a *App) CancelTask(id string) error {
	if a.db == nil {
		return fmt.Errorf("database not initialized")
	}
	if err := a.db.CancelTask(id); err != nil {
		return err
	}
	if t, ok := a.db.GetTask(id); ok {
		a.emitTaskEvent(id, t.ChainID, TaskCancelled, t.Attempts, "")
	}
	return nil
}

// RetryTask resets a failed or cancelled task back to pending so the worker
// picks it up again. Attempts counter is zeroed so retry-budget restarts.
func (a *App) RetryTask(id string) error {
	if a.db == nil {
		return fmt.Errorf("database not initialized")
	}
	if err := a.db.RetryTask(id); err != nil {
		return err
	}
	if t, ok := a.db.GetTask(id); ok {
		a.emitTaskEvent(id, t.ChainID, TaskPending, 0, "")
	}
	return nil
}
