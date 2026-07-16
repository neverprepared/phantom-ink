package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"

	"phantom-ink/internal/contract"
	"phantom-ink/internal/outbox"
)

// playbookPollInterval is how often we check playbook status while waiting
// for a playbook step to finish.
const playbookPollInterval = 4 * time.Second

// playbookStepTimeout is the maximum time a single playbook step may run
// before the loop aborts it. Playbooks drive brainbox sessions which can
// be slow — 30 minutes is generous but bounded.
const playbookStepTimeout = 30 * time.Minute

// ---------------------------------------------------------------------------
// Sequences — runtime CRUD + execution
// ---------------------------------------------------------------------------

const sequenceRunEvent = "loop:run:event"

// ListSequences returns loops visible for the active profile: loops owned by
// that profile plus global loops (workspace_profile=""). When no profile is
// active, all loops are returned.
func (a *App) ListSequences() ([]Sequence, error) {
	if a.db == nil {
		return []Sequence{}, errNoDB
	}
	rawRows, err := a.db.ListSequences(a.activeProfileName())
	if err != nil {
		return nil, err
	}
	out := make([]Sequence, 0, len(rawRows))
	for _, r := range rawRows {
		steps, _ := sequenceStepsFromJSON(r.StepsJSON)
		followups, _ := sequenceFollowupsFromJSON(r.OnSuccessJSON)
		files, _ := sequenceFilesFromJSON(r.FilesJSON)
		out = append(out, Sequence{
			ID:               r.ID,
			Name:             r.Name,
			Description:      r.Description,
			Steps:            steps,
			Cwd:              r.Cwd,
			OnSuccess:        followups,
			Files:            files,
			WorkspaceProfile: r.WorkspaceProfile,
			CreatedAt:        r.CreatedAt,
			UpdatedAt:        r.UpdatedAt,
		})
	}
	return out, nil
}

// SaveSequence creates or updates a loop. A blank ID generates a new one.
// Names must be non-empty; steps may be empty (allowing draft loops).
func (a *App) SaveSequence(c Sequence) (Sequence, error) {
	if a.db == nil {
		return Sequence{}, errNoDB
	}
	if strings.TrimSpace(c.Name) == "" {
		return Sequence{}, fmt.Errorf("loop name is required")
	}
	now := time.Now().UTC().Format(time.RFC3339)
	if c.ID == "" {
		c.ID = newSequenceID()
		c.CreatedAt = now
	}
	c.UpdatedAt = now
	stepsJSON, err := sequenceStepsToJSON(c.Steps)
	if err != nil {
		return Sequence{}, err
	}
	followupsJSON, err := sequenceFollowupsToJSON(c.OnSuccess)
	if err != nil {
		return Sequence{}, err
	}
	// Validate follow-up loop references exist so the user gets feedback at
	// save time rather than mid-run.
	for i, f := range c.OnSuccess {
		if f.SequenceID == "" {
			return Sequence{}, fmt.Errorf("on_success[%d]: loop_id is required", i)
		}
		if _, ok := a.db.GetSequence(f.SequenceID); !ok {
			return Sequence{}, fmt.Errorf("on_success[%d]: loop %q not found", i, f.SequenceID)
		}
	}
	// Normalize file paths: strip whitespace, drop leading slash so loops
	// stay profile-portable, and reject anything that tries to escape via
	// .. segments. The actual existence/profile check happens at run time
	// (resolveProfileFile) when we know which profile is in play.
	cleanFiles := make([]string, 0, len(c.Files))
	for i, raw := range c.Files {
		f := strings.TrimSpace(raw)
		if f == "" {
			continue
		}
		f = strings.TrimPrefix(f, "/")
		clean := filepath.Clean(f)
		if clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
			return Sequence{}, fmt.Errorf("files[%d]: %q escapes profile root", i, raw)
		}
		cleanFiles = append(cleanFiles, clean)
	}
	c.Files = cleanFiles
	filesJSON, err := sequenceFilesToJSON(cleanFiles)
	if err != nil {
		return Sequence{}, err
	}
	if err := a.db.UpsertSequence(SequenceRow{
		ID: c.ID, Name: c.Name, Description: c.Description,
		StepsJSON: stepsJSON, Cwd: c.Cwd, OnSuccessJSON: followupsJSON,
		FilesJSON: filesJSON, WorkspaceProfile: c.WorkspaceProfile,
		CreatedAt: c.CreatedAt, UpdatedAt: c.UpdatedAt,
	}); err != nil {
		return Sequence{}, err
	}
	return c, nil
}

// DeleteSequence removes a loop by ID. Past runs in loop_runs are kept so
// users can still browse history; orphaned runs are filtered out in the UI.
func (a *App) DeleteSequence(id string) error {
	if err := a.requireDB(); err != nil {
		return err
	}
	return a.db.DeleteSequence(id)
}

// ListSequenceRuns returns the most recent runs for a loop, newest first.
func (a *App) ListSequenceRuns(loopID string, limit int) ([]SequenceRunRow, error) {
	if a.db == nil {
		return []SequenceRunRow{}, errNoDB
	}
	return a.db.ListSequenceRuns(loopID, limit)
}

// RunSequence executes a loop end-to-end. Returns the runID immediately and
// streams progress via the loop:run:event Wails event. Each step's output
// is fed to the next step's {{prev.output}} template slot. The initial
// {{input}} slot is filled with the `input` argument.
//
// The active profile is used for cwd resolution — loops never run
// "globally" outside a profile. See feedback_profiles_foundational.md.
//
// Steps are rejected pre-run if any agent is missing from the catalog, not
// detected on PATH, or disabled. The visibility rule (detected && enabled)
// is enforced here so a loop saved when an agent was available can't be
// silently run later after that agent disappears.
func (a *App) RunSequence(id, input, cwdOverride string) (string, error) {
	if a.db == nil {
		return "", errNoDB
	}
	row, ok := a.db.GetSequence(id)
	if !ok {
		return "", fmt.Errorf("loop %q not found", id)
	}
	steps, err := sequenceStepsFromJSON(row.StepsJSON)
	if err != nil {
		return "", err
	}
	if len(steps) == 0 {
		return "", fmt.Errorf("loop has no steps")
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

	// Foreground runs always execute under the currently-active profile —
	// the user is at the keyboard, and we want the immediate run to operate
	// on whatever they're looking at. (Background tasks snapshot at enqueue.)
	profileName := a.activeProfileName()
	if profileName == "" {
		return "", fmt.Errorf("no active profile — set one before running loops")
	}
	if _, err := a.findProfile(profileName); err != nil {
		return "", fmt.Errorf("active profile lookup: %w", err)
	}

	runID := newRunID()
	startedAt := time.Now().UTC().Format(time.RFC3339)
	if err := a.db.InsertSequenceRun(SequenceRunRow{
		ID: runID, SequenceID: id, StartedAt: startedAt, Status: "running", LogJSON: "[]",
	}); err != nil {
		return "", err
	}

	baseCwd := cwdOverride
	if baseCwd == "" {
		baseCwd = row.Cwd
	}

	// Resolve loop.files to absolute paths under the profile root. Any file
	// that escapes is a hard error (consistent with cwd resolution).
	loopFiles, _ := sequenceFilesFromJSON(row.FilesJSON)
	filesArg, err := a.renderFilesArg(profileName, loopFiles)
	if err != nil {
		return "", err
	}

	go func() { _ = a.executeSequence(runID, id, input, baseCwd, steps, profileName, filesArg) }()
	return runID, nil
}

// executeSequence runs every step in order, streaming events as it goes. It
// catches the first failure and stops; downstream steps are skipped. Returns
// nil on success or a wrapped error describing which step failed. Callers
// that want fire-and-forget behavior (the RunSequence binding) discard the error;
// the queue worker uses it to decide retry vs. final-failure.
//
// profileName is required: every cwd (loop-level, step-level) is resolved
// against that profile's workspace_home, with traversal outside the root
// rejected. On-success follow-ups inherit the same profile so autonomous
// flows stay in their lane.
func (a *App) executeSequence(runID, loopID, initialInput, baseCwd string, steps []SequenceStep, profileName, filesArg string) error {
	// Use an independent context for subprocess execution. a.ctx is the Wails
	// window context and gets cancelled on window close/resize events — tying
	// long-running agent processes to it would kill them unexpectedly.
	ctx := context.Background()
	log := make([]SequenceRunEvent, 0, len(steps)*2+2)

	cc := sequenceContext{Input: initialInput, Cwd: baseCwd}
	emit := func(ev SequenceRunEvent) {
		ev.At = time.Now().UTC().Format(time.RFC3339)
		ev.RunID = runID
		ev.SequenceID = loopID
		log = append(log, ev)
		if a.ctx != nil {
			runtime.EventsEmit(a.ctx, sequenceRunEvent, ev)
		}
		a.emitSequenceEnvelope(ev, profileName, cc)
	}

	emit(SequenceRunEvent{Phase: "run:start", Status: "running"})

	prevOutput := initialInput
	status := "success"
	var failure string

	for i, step := range steps {
		switch step.Type {
		case "playbook":
			if step.PlaybookID == "" {
				status = "failed"
				failure = fmt.Sprintf("step %d: playbook step has no playbook_id", i+1)
				emit(SequenceRunEvent{
					Phase: "step:done", StepIndex: i,
					Error: failure, Status: "failed",
				})
				goto done
			}
			emit(SequenceRunEvent{Phase: "step:start", StepIndex: i, AgentID: "playbook:" + step.PlaybookID, Status: "running"})
			playbookOut, err := a.runPlaybookStep(ctx, step.PlaybookID, profileName)
			if err != nil {
				status = "failed"
				failure = fmt.Sprintf("step %d (playbook:%s): %v", i+1, step.PlaybookID, err)
				emit(SequenceRunEvent{
					Phase: "step:done", StepIndex: i, AgentID: "playbook:" + step.PlaybookID,
					Error: err.Error(), Status: "failed",
				})
				goto done
			}
			emit(SequenceRunEvent{
				Phase: "step:done", StepIndex: i, AgentID: "playbook:" + step.PlaybookID,
				Output: playbookOut, Status: "success",
			})
			prevOutput = playbookOut

		default: // "agent" or legacy empty string
			desc, _ := agentDescriptor(step.AgentID)
			rawCwd := step.Cwd
			if rawCwd == "" {
				rawCwd = baseCwd
			}
			resolvedCwd, err := a.resolveCwd(profileName, rawCwd)
			if err != nil {
				status = "failed"
				failure = fmt.Sprintf("step %d (%s): %v", i+1, step.AgentID, err)
				emit(SequenceRunEvent{
					Phase: "step:done", StepIndex: i, AgentID: step.AgentID,
					Error: err.Error(), Status: "failed",
				})
				goto done
			}
			prompt := renderPromptTemplate(step.PromptTemplate, initialInput, prevOutput, filesArg)

			emit(SequenceRunEvent{Phase: "step:start", StepIndex: i, AgentID: step.AgentID, Status: "running"})

			stdout, stderr, exitCode, err := runSequenceStep(ctx, desc, prompt, resolvedCwd)
			if err != nil {
				status = "failed"
				failure = fmt.Sprintf("step %d (%s): %v", i+1, step.AgentID, err)
				emit(SequenceRunEvent{
					Phase: "step:done", StepIndex: i, AgentID: step.AgentID,
					Output: stdout, Stderr: stderr, ExitCode: exitCode,
					Error: err.Error(), Status: "failed",
				})
				goto done
			}
			emit(SequenceRunEvent{
				Phase: "step:done", StepIndex: i, AgentID: step.AgentID,
				Output: stdout, Stderr: stderr, ExitCode: exitCode, Status: "success",
			})
			prevOutput = stdout
		}
	}
done:

	finishedAt := time.Now().UTC().Format(time.RFC3339)
	logJSON, _ := json.Marshal(log)
	if err := a.db.UpdateSequenceRun(runID, finishedAt, status, string(logJSON)); err != nil {
		logErr("failed to persist loop run %s: %v", runID, err)
	}

	// Sequence failure attention now flows through the bus: the run:done envelope
	// below carries status=failed and metadata (loop_id, input, cwd) needed
	// for AttentionRetry. No attention_items row needed (P5).
	emit(SequenceRunEvent{Phase: "run:done", Status: status, Error: failure})
	if status != "success" {
		return fmt.Errorf("%s", failure)
	}

	// Declarative follow-ups: enqueue any loops listed in loop.on_success.
	// Failures here are logged but don't bubble up — the primary loop
	// succeeded; a downstream enqueue error shouldn't retroactively fail it.
	// Profile is inherited so autonomous flows stay in their lane.
	a.enqueueFollowups(loopID, prevOutput, profileName)
	return nil
}

// enqueueFollowups looks up the parent loop's on_success entries and submits
// a task for each, inheriting the parent's workspace profile so the whole
// flow stays under one workspace context.
func (a *App) enqueueFollowups(parentSequenceID, lastOutput, parentProfile string) {
	if a.db == nil {
		return
	}
	row, ok := a.db.GetSequence(parentSequenceID)
	if !ok {
		return
	}
	followups, err := sequenceFollowupsFromJSON(row.OnSuccessJSON)
	if err != nil || len(followups) == 0 {
		return
	}
	for i, f := range followups {
		input := lastOutput
		switch f.InputFrom {
		case "", "stdout":
			input = lastOutput
		case "literal":
			input = f.InputLiteral
		default:
			fmt.Fprintf(os.Stderr, "warning: on_success[%d]: unknown input_from %q, defaulting to stdout\n", i, f.InputFrom)
		}
		if _, err := a.EnqueueTask(EnqueueTaskRequest{
			SequenceID:       f.SequenceID,
			Input:            input,
			Cwd:              f.Cwd,
			Trigger:          TriggerFollowup,
			WorkspaceProfile: parentProfile,
		}); err != nil {
			fmt.Fprintf(os.Stderr, "warning: enqueue followup %d (%s): %v\n", i, f.SequenceID, err)
		}
	}
}

// runSequenceForTask is the queue worker's synchronous entry point. It validates
// the task's loop, allocates a run row, executes the loop, and returns the
// runID plus any error. Unlike RunSequence (which kicks off a goroutine and
// returns immediately), this blocks until the loop completes so the worker
// can mark the task succeeded/failed accordingly.
//
// The task's WorkspaceProfile is the execution context: all cwd values are
// resolved against that profile's workspace_home via resolveCwd. Empty
// profile is a hard error — loops never run "globally."
func (a *App) runSequenceForTask(ctx context.Context, task TaskRow) (string, error) {
	if a.db == nil {
		return "", errNoDB
	}
	row, ok := a.db.GetSequence(task.SequenceID)
	if !ok {
		return "", fmt.Errorf("loop %q not found", task.SequenceID)
	}
	if task.WorkspaceProfile == "" {
		return "", fmt.Errorf("task %s has no workspace_profile (legacy row?)", task.ID)
	}
	steps, err := sequenceStepsFromJSON(row.StepsJSON)
	if err != nil {
		return "", err
	}
	if len(steps) == 0 {
		return "", fmt.Errorf("loop has no steps")
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
	if err := a.db.InsertSequenceRun(SequenceRunRow{
		ID: runID, SequenceID: task.SequenceID, StartedAt: startedAt, Status: "running", LogJSON: "[]",
	}); err != nil {
		return "", err
	}

	cwd := task.Cwd
	if cwd == "" {
		cwd = row.Cwd
	}
	loopFiles, _ := sequenceFilesFromJSON(row.FilesJSON)
	filesArg, err := a.renderFilesArg(task.WorkspaceProfile, loopFiles)
	if err != nil {
		return "", err
	}
	_ = ctx // executeSequence reads ctx from a.ctx; we don't pass it through yet
	return runID, a.executeSequence(runID, task.SequenceID, task.Input, cwd, steps, task.WorkspaceProfile, filesArg)
}

// renderFilesArg resolves each loop.files entry to an absolute path under
// the profile root and joins them with spaces (shell-quoted). Empty when
// the loop has no files. Errors if any file escapes the profile root,
// matching the cwd isolation guarantees.
func (a *App) renderFilesArg(profileName string, files []string) (string, error) {
	if len(files) == 0 {
		return "", nil
	}
	parts := make([]string, 0, len(files))
	for _, rel := range files {
		abs, err := a.resolveCwd(profileName, rel)
		if err != nil {
			return "", fmt.Errorf("file %q: %w", rel, err)
		}
		parts = append(parts, shellQuote(abs))
	}
	return strings.Join(parts, " "), nil
}

// emitTaskEvent pushes a state change to the frontend, mirroring the loop
// event pattern. The Tasks panel listens on task:event for live updates.
//
// Also dual-emits an agent-event-bus envelope so cross-machine consumers see
// the same state transition. Producers keep using emitTaskEvent — the
// envelope emission is implicit.
func (a *App) emitTaskEvent(taskID, loopID, status string, attempts int, errMsg string) {
	if a.ctx == nil {
		return
	}
	runtime.EventsEmit(a.ctx, taskEventName, taskEvent{
		TaskID: taskID, SequenceID: loopID, Status: status,
		Attempts: attempts, Error: errMsg,
		At: time.Now().UTC().Format(time.RFC3339),
	})
	a.emitTaskEnvelope(taskID, loopID, status, attempts, errMsg)
}

// emitTaskEnvelope builds and queues the bus envelope for a task state change.
// Kept private to this file; emitTaskEvent is the public producer entry point.
func (a *App) emitTaskEnvelope(taskID, loopID, status string, attempts int, errMsg string) {
	if a == nil || a.outbox == nil || a.db == nil {
		return
	}
	var (
		title       = fmt.Sprintf("Task %s", status)
		subtitle    = sequenceNameOrID(a.db, loopID)
		workspace   string
		maxAttempts = 1
	)
	if row, ok := a.db.GetTask(taskID); ok {
		workspace = row.WorkspaceProfile
		maxAttempts = row.MaxAttempts
		if row.Input != "" {
			d := strings.TrimSpace(row.Input)
			if len(d) > 120 {
				d = d[:119] + "…"
			}
			title = d
		}
	}
	meta := map[string]interface{}{
		"loop_id":      loopID,
		"attempts":     attempts,
		"max_attempts": maxAttempts,
	}
	if errMsg != "" {
		meta["last_error"] = errMsg
	}
	envStatus := taskEnvelopeStatus(status)
	now := time.Now().UnixMilli()
	var endAt *int
	if envStatus == contract.EnvelopeStatusDone || envStatus == contract.EnvelopeStatusFailed {
		endAt = atMillis(now)
	}
	a.emitEnvelope(outbox.Envelope{
		ID:        "task:" + taskID,
		Kind:      "event",
		Source:    ptr(envelopeSource),
		Type:      ptr(taskEnvelopeType(status)),
		Status:    statusPtr(envStatus),
		Title:     title,
		Subtitle:  optStr(subtitle),
		Workspace: optStr(workspace),
		ParentID:  ptr("loop:" + loopID),
		Tags:      []string{"task"},
		Metadata:  meta,
		EndAt:     endAt,
	})
}

// runPlaybookStep triggers a brainbox playbook and polls until it reaches a
// terminal state. Returns a brief status summary as the step output (fed into
// {{prev.output}} for downstream steps). Blocks up to playbookStepTimeout.
func (a *App) runPlaybookStep(parent context.Context, playbookID, profileName string) (string, error) {
	if a.client == nil {
		return "", fmt.Errorf("brainbox client not available")
	}
	ctx, cancel := context.WithTimeout(parent, playbookStepTimeout)
	defer cancel()

	if _, err := a.client.RunPlaybook(playbookID, profileName, ""); err != nil {
		return "", fmt.Errorf("start playbook: %w", err)
	}

	ticker := time.NewTicker(playbookPollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return "", fmt.Errorf("timed out waiting for playbook %s", playbookID)
		case <-ticker.C:
			pb, err := a.client.GetPlaybook(playbookID)
			if err != nil {
				return "", fmt.Errorf("poll playbook: %w", err)
			}
			switch pb.Status {
			case "completed":
				return fmt.Sprintf("playbook %q completed (%d tasks)", pb.Name, len(pb.Tasks)), nil
			case "failed":
				return "", fmt.Errorf("playbook %q failed", pb.Name)
			case "cancelled":
				return "", fmt.Errorf("playbook %q was cancelled", pb.Name)
			}
			// "running" or "idle" — keep polling
		}
	}
}

// newSequenceID returns a short opaque loop identifier.
func newSequenceID() string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return "loop-" + hex.EncodeToString(b[:])
}
