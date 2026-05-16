package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// ---------------------------------------------------------------------------
// Chains — runtime CRUD + execution
// ---------------------------------------------------------------------------

const chainRunEvent = "chain:run:event"

// ListChains returns every saved chain with steps materialized from JSON.
func (a *App) ListChains() ([]Chain, error) {
	if a.db == nil {
		return []Chain{}, fmt.Errorf("database not initialized")
	}
	rawRows, err := a.db.ListChains()
	if err != nil {
		return nil, err
	}
	out := make([]Chain, 0, len(rawRows))
	for _, r := range rawRows {
		steps, _ := chainStepsFromJSON(r.StepsJSON)
		out = append(out, Chain{
			ID:          r.ID,
			Name:        r.Name,
			Description: r.Description,
			Steps:       steps,
			Cwd:         r.Cwd,
			CreatedAt:   r.CreatedAt,
			UpdatedAt:   r.UpdatedAt,
		})
	}
	return out, nil
}

// SaveChain creates or updates a chain. A blank ID generates a new one.
// Names must be non-empty; steps may be empty (allowing draft chains).
func (a *App) SaveChain(c Chain) (Chain, error) {
	if a.db == nil {
		return Chain{}, fmt.Errorf("database not initialized")
	}
	if strings.TrimSpace(c.Name) == "" {
		return Chain{}, fmt.Errorf("chain name is required")
	}
	now := time.Now().UTC().Format(time.RFC3339)
	if c.ID == "" {
		c.ID = newChainID()
		c.CreatedAt = now
	}
	c.UpdatedAt = now
	stepsJSON, err := chainStepsToJSON(c.Steps)
	if err != nil {
		return Chain{}, err
	}
	if err := a.db.UpsertChain(ChainRow{
		ID: c.ID, Name: c.Name, Description: c.Description,
		StepsJSON: stepsJSON, Cwd: c.Cwd,
		CreatedAt: c.CreatedAt, UpdatedAt: c.UpdatedAt,
	}); err != nil {
		return Chain{}, err
	}
	return c, nil
}

// DeleteChain removes a chain by ID. Past runs in chain_runs are kept so
// users can still browse history; orphaned runs are filtered out in the UI.
func (a *App) DeleteChain(id string) error {
	if a.db == nil {
		return fmt.Errorf("database not initialized")
	}
	return a.db.DeleteChain(id)
}

// ListChainRuns returns the most recent runs for a chain, newest first.
func (a *App) ListChainRuns(chainID string, limit int) ([]ChainRunRow, error) {
	if a.db == nil {
		return []ChainRunRow{}, fmt.Errorf("database not initialized")
	}
	return a.db.ListChainRuns(chainID, limit)
}

// RunChain executes a chain end-to-end. Returns the runID immediately and
// streams progress via the chain:run:event Wails event. Each step's output
// is fed to the next step's {{prev.output}} template slot. The initial
// {{input}} slot is filled with the `input` argument.
//
// Steps are rejected pre-run if any agent is missing from the catalog, not
// detected on PATH, or disabled. The visibility rule (detected && enabled)
// is enforced here so a chain saved when an agent was available can't be
// silently run later after that agent disappears.
func (a *App) RunChain(id, input, cwdOverride string) (string, error) {
	if a.db == nil {
		return "", fmt.Errorf("database not initialized")
	}
	row, ok := a.db.GetChain(id)
	if !ok {
		return "", fmt.Errorf("chain %q not found", id)
	}
	steps, err := chainStepsFromJSON(row.StepsJSON)
	if err != nil {
		return "", err
	}
	if len(steps) == 0 {
		return "", fmt.Errorf("chain has no steps")
	}

	usable, err := a.UsableAgents()
	if err != nil {
		return "", err
	}
	usableByID := make(map[string]DetectedAgent, len(usable))
	for _, u := range usable {
		usableByID[u.ID] = u
	}
	for i, step := range steps {
		if _, ok := usableByID[step.AgentID]; !ok {
			return "", fmt.Errorf("step %d: agent %q is not enabled or not detected", i+1, step.AgentID)
		}
		if _, ok := agentDescriptor(step.AgentID); !ok {
			return "", fmt.Errorf("step %d: agent %q is not in the catalog", i+1, step.AgentID)
		}
		switch step.Executor {
		case "", "host":
			// supported
		default:
			return "", fmt.Errorf("step %d: executor %q is not yet implemented (only \"host\" today)", i+1, step.Executor)
		}
	}

	runID := newRunID()
	startedAt := time.Now().UTC().Format(time.RFC3339)
	if err := a.db.InsertChainRun(ChainRunRow{
		ID: runID, ChainID: id, StartedAt: startedAt, Status: "running", LogJSON: "[]",
	}); err != nil {
		return "", err
	}

	baseCwd := cwdOverride
	if baseCwd == "" {
		baseCwd = row.Cwd
	}

	go func() { _ = a.executeChain(runID, id, input, baseCwd, steps) }()
	return runID, nil
}

// executeChain runs every step in order, streaming events as it goes. It
// catches the first failure and stops; downstream steps are skipped. Returns
// nil on success or a wrapped error describing which step failed. Callers
// that want fire-and-forget behavior (the RunChain binding) discard the error;
// the queue worker uses it to decide retry vs. final-failure.
func (a *App) executeChain(runID, chainID, initialInput, baseCwd string, steps []ChainStep) error {
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	log := make([]ChainRunEvent, 0, len(steps)*2+2)

	emit := func(ev ChainRunEvent) {
		ev.At = time.Now().UTC().Format(time.RFC3339)
		ev.RunID = runID
		ev.ChainID = chainID
		log = append(log, ev)
		if a.ctx != nil {
			runtime.EventsEmit(a.ctx, chainRunEvent, ev)
		}
	}

	emit(ChainRunEvent{Phase: "run:start", Status: "running"})

	prevOutput := initialInput
	status := "success"
	var failure string

	for i, step := range steps {
		desc, _ := agentDescriptor(step.AgentID)
		stepCwd := step.Cwd
		if stepCwd == "" {
			stepCwd = baseCwd
		}
		prompt := renderPromptTemplate(step.PromptTemplate, initialInput, prevOutput)

		emit(ChainRunEvent{Phase: "step:start", StepIndex: i, AgentID: step.AgentID, Status: "running"})

		stdout, stderr, exitCode, err := runChainStep(ctx, desc, prompt, stepCwd)
		if err != nil {
			status = "failed"
			failure = fmt.Sprintf("step %d (%s): %v", i+1, step.AgentID, err)
			emit(ChainRunEvent{
				Phase: "step:done", StepIndex: i, AgentID: step.AgentID,
				Output: stdout, Stderr: stderr, ExitCode: exitCode,
				Error: err.Error(), Status: "failed",
			})
			break
		}
		emit(ChainRunEvent{
			Phase: "step:done", StepIndex: i, AgentID: step.AgentID,
			Output: stdout, Stderr: stderr, ExitCode: exitCode, Status: "success",
		})
		prevOutput = stdout
	}

	finishedAt := time.Now().UTC().Format(time.RFC3339)
	logJSON, _ := json.Marshal(log)
	if err := a.db.UpdateChainRun(runID, finishedAt, status, string(logJSON)); err != nil {
		fmt.Fprintf(os.Stderr, "warning: failed to persist chain run %s: %v\n", runID, err)
	}

	emit(ChainRunEvent{Phase: "run:done", Status: status, Error: failure})
	if status != "success" {
		return fmt.Errorf("%s", failure)
	}
	return nil
}

// runChainForTask is the queue worker's synchronous entry point. It validates
// the task's chain, allocates a run row, executes the chain, and returns the
// runID plus any error. Unlike RunChain (which kicks off a goroutine and
// returns immediately), this blocks until the chain completes so the worker
// can mark the task succeeded/failed accordingly.
func (a *App) runChainForTask(ctx context.Context, task TaskRow) (string, error) {
	if a.db == nil {
		return "", fmt.Errorf("database not initialized")
	}
	row, ok := a.db.GetChain(task.ChainID)
	if !ok {
		return "", fmt.Errorf("chain %q not found", task.ChainID)
	}
	steps, err := chainStepsFromJSON(row.StepsJSON)
	if err != nil {
		return "", err
	}
	if len(steps) == 0 {
		return "", fmt.Errorf("chain has no steps")
	}

	usable, err := a.UsableAgents()
	if err != nil {
		return "", err
	}
	usableByID := make(map[string]DetectedAgent, len(usable))
	for _, u := range usable {
		usableByID[u.ID] = u
	}
	for i, step := range steps {
		if _, ok := usableByID[step.AgentID]; !ok {
			return "", fmt.Errorf("step %d: agent %q is not enabled or not detected", i+1, step.AgentID)
		}
		switch step.Executor {
		case "", "host":
		default:
			return "", fmt.Errorf("step %d: executor %q not implemented", i+1, step.Executor)
		}
	}

	runID := newRunID()
	startedAt := time.Now().UTC().Format(time.RFC3339)
	if err := a.db.InsertChainRun(ChainRunRow{
		ID: runID, ChainID: task.ChainID, StartedAt: startedAt, Status: "running", LogJSON: "[]",
	}); err != nil {
		return "", err
	}

	cwd := task.Cwd
	if cwd == "" {
		cwd = row.Cwd
	}
	_ = ctx // executeChain reads ctx from a.ctx; we don't pass it through yet
	return runID, a.executeChain(runID, task.ChainID, task.Input, cwd, steps)
}

// emitTaskEvent pushes a state change to the frontend, mirroring the chain
// event pattern. The Tasks panel listens on task:event for live updates.
func (a *App) emitTaskEvent(taskID, chainID, status string, attempts int, errMsg string) {
	if a.ctx == nil {
		return
	}
	runtime.EventsEmit(a.ctx, taskEventName, taskEvent{
		TaskID: taskID, ChainID: chainID, Status: status,
		Attempts: attempts, Error: errMsg,
		At: time.Now().UTC().Format(time.RFC3339),
	})
}

// newChainID returns a short opaque chain identifier.
func newChainID() string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return "chain-" + hex.EncodeToString(b[:])
}
