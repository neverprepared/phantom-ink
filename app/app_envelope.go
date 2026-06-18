package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"phantom-ink/internal/outbox"
)

func timeNowUnixMilli() int64 { return time.Now().UnixMilli() }

// emitEnvelope hands an envelope to the outbox for at-least-once delivery to
// brainbox. Best-effort: a missing outbox (DB not open yet) is silently
// dropped so producers don't have to nil-check.
//
// Most callers prefer the typed helpers (emitTaskEnvelope, emitSequenceEnvelope)
// below; this is the bare interface for ad-hoc events.
func (a *App) emitEnvelope(env outbox.Envelope) {
	if a == nil || a.outbox == nil {
		return
	}
	if err := a.outbox.Append(env); err != nil {
		fmt.Fprintf(os.Stderr, "outbox append failed: %v\n", err)
	}
}

// taskEnvelopeStatus maps the local queue's task status strings into the
// agent-bus envelope status enum. Local "succeeded" becomes "done" so the bus
// uses one universal completion term.
func taskEnvelopeStatus(taskStatus string) string {
	switch taskStatus {
	case TaskPending:
		return "upcoming"
	case TaskRunning:
		return "active"
	case TaskSucceeded:
		return "done"
	case TaskFailed:
		return "failed"
	case TaskCancelled:
		return "done"
	}
	return taskStatus
}

// taskEnvelopeType returns the dotted envelope `type` for a task status.
func taskEnvelopeType(taskStatus string) string {
	switch taskStatus {
	case TaskPending:
		return "task.queued"
	case TaskRunning:
		return "task.running"
	case TaskSucceeded:
		return "task.succeeded"
	case TaskFailed:
		return "task.failed"
	case TaskCancelled:
		return "task.cancelled"
	}
	return "task." + taskStatus
}

// envelopeSource is the producer identifier used in every envelope this app
// emits. Brainbox treats it as part of the envelope's provenance and the UI
// uses it for source filtering.
const envelopeSource = "wails-app@local"

// sequenceEnvelopeStatus maps the loop event's status field into the envelope
// status enum used across the bus.
func sequenceEnvelopeStatus(loopStatus string) string {
	switch loopStatus {
	case "running":
		return "active"
	case "success":
		return "done"
	case "failed":
		return "failed"
	}
	return loopStatus
}

// sequenceContext is the retry context the AttentionRetry handler needs to
// re-enqueue a failed loop run. Threaded through emitSequenceEnvelope so the
// failure envelope carries it in metadata.
type sequenceContext struct {
	Input string
	Cwd   string
}

// emitSequenceEnvelope converts a SequenceRunEvent into one (or two) bus envelopes:
//   - run:start / run:done → envelope id=loop:<runID>
//   - step:start / step:done → envelope id=loop-step:<runID>:<index>,
//     with parent_id=loop:<runID>
//
// Stable IDs ensure brainbox upserts the same row across state transitions
// and dedup keeps at-least-once delivery safe. The sequenceContext is embedded
// in run-level envelope metadata so AttentionRetry can rebuild the
// EnqueueTaskRequest without a separate side table.
func (a *App) emitSequenceEnvelope(ev SequenceRunEvent, workspace string, cc sequenceContext) {
	if a == nil || a.outbox == nil {
		return
	}
	now := nowMillis()
	loopTitle := sequenceNameOrID(a.db, ev.SequenceID)
	envStatus := sequenceEnvelopeStatus(ev.Status)

	switch ev.Phase {
	case "run:start":
		a.emitEnvelope(outbox.Envelope{
			ID:        "loop:" + ev.RunID,
			Kind:      "event",
			Source:    envelopeSource,
			Type:      "loop.run.start",
			Status:    "active",
			Title:     loopTitle,
			Subtitle:  "loop run",
			Workspace: workspace,
			Tags:      []string{"loop"},
			StartAt:   &now,
			Metadata: map[string]interface{}{
				"loop_id": ev.SequenceID,
				"input":    cc.Input,
				"cwd":      cc.Cwd,
			},
		})
	case "run:done":
		var endAt *int64 = &now
		meta := map[string]interface{}{
			"loop_id": ev.SequenceID,
			"input":    cc.Input,
			"cwd":      cc.Cwd,
		}
		if ev.Error != "" {
			meta["error"] = ev.Error
		}
		a.emitEnvelope(outbox.Envelope{
			ID:        "loop:" + ev.RunID,
			Kind:      "event",
			Source:    envelopeSource,
			Type:      "loop.run.done",
			Status:    envStatus,
			Title:     loopTitle,
			Subtitle:  "loop run",
			Workspace: workspace,
			Tags:      []string{"loop"},
			EndAt:     endAt,
			Metadata:  meta,
		})
	case "step:start":
		a.emitEnvelope(outbox.Envelope{
			ID:        fmt.Sprintf("loop-step:%s:%d", ev.RunID, ev.StepIndex),
			Kind:      "event",
			Source:    envelopeSource,
			Type:      "loop.step.start",
			Status:    "active",
			Title:     fmt.Sprintf("Step %d · %s", ev.StepIndex+1, ev.AgentID),
			Workspace: workspace,
			ParentID:  "loop:" + ev.RunID,
			Tags:      []string{"loop", "step"},
			StartAt:   &now,
			Metadata: map[string]interface{}{
				"loop_id":   ev.SequenceID,
				"step_index": ev.StepIndex,
				"agent_id":   ev.AgentID,
			},
		})
	case "step:done":
		var endAt *int64 = &now
		meta := map[string]interface{}{
			"loop_id":   ev.SequenceID,
			"step_index": ev.StepIndex,
			"agent_id":   ev.AgentID,
			"exit_code":  ev.ExitCode,
		}
		if ev.Error != "" {
			meta["error"] = ev.Error
		}
		a.emitEnvelope(outbox.Envelope{
			ID:        fmt.Sprintf("loop-step:%s:%d", ev.RunID, ev.StepIndex),
			Kind:      "event",
			Source:    envelopeSource,
			Type:      "loop.step.done",
			Status:    envStatus,
			Title:     fmt.Sprintf("Step %d · %s", ev.StepIndex+1, ev.AgentID),
			Workspace: workspace,
			ParentID:  "loop:" + ev.RunID,
			Tags:      []string{"loop", "step"},
			EndAt:     endAt,
			Metadata:  meta,
		})
	}
}

func nowMillis() int64 {
	return timeNowUnixMilli()
}

// emitCollectedEntryEnvelope bridges collection-script output into the bus.
// Entries with a non-empty actions[] become attention-eligible (needs_action);
// other entries don't go to the bus because the existing collected_entries
// table is the right home for non-actionable timeline data.
//
// Status mapping follows entryStatusToAttention so terminal failures land as
// `failed` rather than `needs_action` if the script set that explicitly.
func (a *App) emitCollectedEntryEnvelope(job CollectJob, e CollectedEntry) {
	if a == nil || a.outbox == nil {
		return
	}
	if !hasActions(e.Actions) {
		return
	}
	id := "entry:" + e.JobID + "/" + e.EntryID
	now := nowMillis()
	envStatus := entryStatusToAttention(e.Status)

	var actionsList []map[string]any
	if len(e.Actions) > 0 {
		_ = json.Unmarshal(e.Actions, &actionsList)
	}

	meta := map[string]interface{}{
		"job_id":      e.JobID,
		"entry_id":    e.EntryID,
		"job_name":    job.Name,
		"entry_kind":  e.Kind,
	}
	a.emitEnvelope(outbox.Envelope{
		ID:        id,
		Kind:      "event",
		Source:    envelopeSource,
		Type:      "entry.collected",
		Status:    envStatus,
		Title:     firstNonEmpty(e.Title, e.EntryID),
		Subtitle:  fmt.Sprintf("%s · %s", e.Kind, job.Name),
		Workspace: e.Profile,
		URL:       e.URL,
		StartAt:   &now,
		Tags:      append([]string{"entry"}, e.Tags...),
		Metadata:  meta,
		Actions:   actionsList,
	})
}

// ── Action outcome recording ──────────────────────────────────────────────────

// Default actors used by the convenience wrappers below.
const (
	ActorUser   = "user"
	ActorSystem = "system"
)

// recordAction runs fn, times it, and writes an `action.<name>` envelope to
// the outbox with parent_id linking back to the target. The action envelope
// itself always has status="done" — its `outcome.ok` tells the consumer
// whether the underlying action succeeded.
//
// Returns whatever fn returned, so call sites stay simple:
//
//	return a.recordAction("task:"+id, "retry", ActorUser, func() error { return a.doRetry(id) })
//
// Use ActorUser for UI-driven clicks, ActorSystem for daemon-fired actions,
// and "agent:<name>" for automation rules. Unexported so it doesn't get
// auto-bound to JS — UI must call the wrapped methods, never forge actions.
func (a *App) recordAction(targetID, actionName, actor string, fn func() error) error {
	start := nowMillis()
	err := fn()
	duration := nowMillis() - start

	if a == nil || a.outbox == nil {
		return err
	}

	outcome := &outbox.Outcome{
		OK:         err == nil,
		Actor:      actor,
		DurationMs: &duration,
	}
	if err != nil {
		outcome.Error = err.Error()
	}

	endAt := start + duration
	title := fmt.Sprintf("action %s", actionName)
	a.emitEnvelope(outbox.Envelope{
		ID:       fmt.Sprintf("action:%s:%s:%d", targetID, actionName, start),
		Kind:     "event",
		Source:   envelopeSource,
		Type:     "action." + actionName,
		Status:   "done",
		Title:    title,
		ParentID: targetID,
		Tags:     []string{"action", actionName},
		StartAt:  &start,
		EndAt:    &endAt,
		Outcome:  outcome,
		Metadata: map[string]interface{}{"target": targetID},
	})
	return err
}

