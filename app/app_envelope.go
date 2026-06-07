package main

import (
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
// Most callers prefer the typed helpers (emitTaskEnvelope, emitChainEnvelope)
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

// chainEnvelopeStatus maps the chain event's status field into the envelope
// status enum used across the bus.
func chainEnvelopeStatus(chainStatus string) string {
	switch chainStatus {
	case "running":
		return "active"
	case "success":
		return "done"
	case "failed":
		return "failed"
	}
	return chainStatus
}

// emitChainEnvelope converts a ChainRunEvent into one (or two) bus envelopes:
//   - run:start / run:done → envelope id=chain:<runID>
//   - step:start / step:done → envelope id=chain-step:<runID>:<index>,
//     with parent_id=chain:<runID>
//
// Stable IDs ensure brainbox upserts the same row across state transitions
// and dedup keeps at-least-once delivery safe.
func (a *App) emitChainEnvelope(ev ChainRunEvent, workspace string) {
	if a == nil || a.outbox == nil {
		return
	}
	now := nowMillis()
	chainTitle := chainNameOrID(a.db, ev.ChainID)
	envStatus := chainEnvelopeStatus(ev.Status)

	switch ev.Phase {
	case "run:start":
		a.emitEnvelope(outbox.Envelope{
			ID:        "chain:" + ev.RunID,
			Kind:      "event",
			Source:    envelopeSource,
			Type:      "chain.run.start",
			Status:    "active",
			Title:     chainTitle,
			Subtitle:  "chain run",
			Workspace: workspace,
			Tags:      []string{"chain"},
			StartAt:   &now,
			Metadata:  map[string]interface{}{"chain_id": ev.ChainID},
		})
	case "run:done":
		var endAt *int64 = &now
		meta := map[string]interface{}{"chain_id": ev.ChainID}
		if ev.Error != "" {
			meta["error"] = ev.Error
		}
		a.emitEnvelope(outbox.Envelope{
			ID:        "chain:" + ev.RunID,
			Kind:      "event",
			Source:    envelopeSource,
			Type:      "chain.run.done",
			Status:    envStatus,
			Title:     chainTitle,
			Subtitle:  "chain run",
			Workspace: workspace,
			Tags:      []string{"chain"},
			EndAt:     endAt,
			Metadata:  meta,
		})
	case "step:start":
		a.emitEnvelope(outbox.Envelope{
			ID:        fmt.Sprintf("chain-step:%s:%d", ev.RunID, ev.StepIndex),
			Kind:      "event",
			Source:    envelopeSource,
			Type:      "chain.step.start",
			Status:    "active",
			Title:     fmt.Sprintf("Step %d · %s", ev.StepIndex+1, ev.AgentID),
			Workspace: workspace,
			ParentID:  "chain:" + ev.RunID,
			Tags:      []string{"chain", "step"},
			StartAt:   &now,
			Metadata: map[string]interface{}{
				"chain_id":   ev.ChainID,
				"step_index": ev.StepIndex,
				"agent_id":   ev.AgentID,
			},
		})
	case "step:done":
		var endAt *int64 = &now
		meta := map[string]interface{}{
			"chain_id":   ev.ChainID,
			"step_index": ev.StepIndex,
			"agent_id":   ev.AgentID,
			"exit_code":  ev.ExitCode,
		}
		if ev.Error != "" {
			meta["error"] = ev.Error
		}
		a.emitEnvelope(outbox.Envelope{
			ID:        fmt.Sprintf("chain-step:%s:%d", ev.RunID, ev.StepIndex),
			Kind:      "event",
			Source:    envelopeSource,
			Type:      "chain.step.done",
			Status:    envStatus,
			Title:     fmt.Sprintf("Step %d · %s", ev.StepIndex+1, ev.AgentID),
			Workspace: workspace,
			ParentID:  "chain:" + ev.RunID,
			Tags:      []string{"chain", "step"},
			EndAt:     endAt,
			Metadata:  meta,
		})
	}
}

func nowMillis() int64 {
	return timeNowUnixMilli()
}

