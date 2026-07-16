package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"phantom-ink/internal/contract"
	"phantom-ink/internal/outbox"
)

func timeNowUnixMilli() int64 { return time.Now().UnixMilli() }

// The envelope fields are generated pointers (contract.AgentEnvelope), so these
// tiny helpers take the address of a value or a mapped status without a named
// temporary at each call site.
func ptr[T any](v T) *T { return &v }

// statusPtr adapts an EnvelopeStatus into the *EnvelopeStatus the generated
// envelope carries. Kept distinct from ptr for readable call sites.
func statusPtr(s contract.EnvelopeStatus) *contract.EnvelopeStatus { return &s }

// optStr returns nil for an empty string so an unset optional field is omitted
// from the wire JSON (matching the old plain-string `omitempty` behaviour) —
// important for fields like workspace, where brainbox COALESCEs NULL but would
// overwrite state with a bare "".
func optStr(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

// atMillis returns a *int for the generated start_at/end_at fields. Epoch
// milliseconds fit int on the 64-bit desktop targets this app builds for.
func atMillis(ms int64) *int { v := int(ms); return &v }

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
// agent-bus envelope status enum (contract.EnvelopeStatus — the single generated
// source, no bare status literals). Local "succeeded" becomes "done" so the bus
// uses one universal completion term. An unrecognised local status falls back to
// active (in-progress) rather than passing the raw string through — an off-enum
// value would now fail schema conformance.
func taskEnvelopeStatus(taskStatus string) contract.EnvelopeStatus {
	switch taskStatus {
	case TaskPending:
		return contract.EnvelopeStatusUpcoming
	case TaskRunning:
		return contract.EnvelopeStatusActive
	case TaskSucceeded:
		return contract.EnvelopeStatusDone
	case TaskFailed:
		return contract.EnvelopeStatusFailed
	case TaskCancelled:
		return contract.EnvelopeStatusDone
	}
	return contract.EnvelopeStatusActive
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

// sequenceEnvelopeStatus maps the loop event's status field into the generated
// envelope status enum used across the bus. Unknown states fall back to active
// so the emitted value always conforms to the contract enum.
func sequenceEnvelopeStatus(loopStatus string) contract.EnvelopeStatus {
	switch loopStatus {
	case "running":
		return contract.EnvelopeStatusActive
	case "success":
		return contract.EnvelopeStatusDone
	case "failed":
		return contract.EnvelopeStatusFailed
	}
	return contract.EnvelopeStatusActive
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
			Source:    ptr(envelopeSource),
			Type:      ptr("loop.run.start"),
			Status:    statusPtr(contract.EnvelopeStatusActive),
			Title:     loopTitle,
			Subtitle:  ptr("loop run"),
			Workspace: optStr(workspace),
			Tags:      []string{"loop"},
			StartAt:   atMillis(now),
			Metadata: map[string]interface{}{
				"loop_id": ev.SequenceID,
				"input":   cc.Input,
				"cwd":     cc.Cwd,
			},
		})
	case "run:done":
		meta := map[string]interface{}{
			"loop_id": ev.SequenceID,
			"input":   cc.Input,
			"cwd":     cc.Cwd,
		}
		if ev.Error != "" {
			meta["error"] = ev.Error
		}
		a.emitEnvelope(outbox.Envelope{
			ID:        "loop:" + ev.RunID,
			Kind:      "event",
			Source:    ptr(envelopeSource),
			Type:      ptr("loop.run.done"),
			Status:    statusPtr(envStatus),
			Title:     loopTitle,
			Subtitle:  ptr("loop run"),
			Workspace: optStr(workspace),
			Tags:      []string{"loop"},
			EndAt:     atMillis(now),
			Metadata:  meta,
		})
	case "step:start":
		a.emitEnvelope(outbox.Envelope{
			ID:        fmt.Sprintf("loop-step:%s:%d", ev.RunID, ev.StepIndex),
			Kind:      "event",
			Source:    ptr(envelopeSource),
			Type:      ptr("loop.step.start"),
			Status:    statusPtr(contract.EnvelopeStatusActive),
			Title:     fmt.Sprintf("Step %d · %s", ev.StepIndex+1, ev.AgentID),
			Workspace: optStr(workspace),
			ParentID:  ptr("loop:" + ev.RunID),
			Tags:      []string{"loop", "step"},
			StartAt:   atMillis(now),
			Metadata: map[string]interface{}{
				"loop_id":    ev.SequenceID,
				"step_index": ev.StepIndex,
				"agent_id":   ev.AgentID,
			},
		})
	case "step:done":
		meta := map[string]interface{}{
			"loop_id":    ev.SequenceID,
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
			Source:    ptr(envelopeSource),
			Type:      ptr("loop.step.done"),
			Status:    statusPtr(envStatus),
			Title:     fmt.Sprintf("Step %d · %s", ev.StepIndex+1, ev.AgentID),
			Workspace: optStr(workspace),
			ParentID:  ptr("loop:" + ev.RunID),
			Tags:      []string{"loop", "step"},
			EndAt:     atMillis(now),
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
		"job_id":     e.JobID,
		"entry_id":   e.EntryID,
		"job_name":   job.Name,
		"entry_kind": e.Kind,
	}
	a.emitEnvelope(outbox.Envelope{
		ID:        id,
		Kind:      "event",
		Source:    ptr(envelopeSource),
		Type:      ptr("entry.collected"),
		Status:    statusPtr(envStatus),
		Title:     firstNonEmpty(e.Title, e.EntryID),
		Subtitle:  ptr(fmt.Sprintf("%s · %s", e.Kind, job.Name)),
		Workspace: optStr(e.Profile),
		URL:       optStr(e.URL),
		StartAt:   atMillis(now),
		Tags:      append([]string{"entry"}, e.Tags...),
		Metadata:  meta,
		Actions:   toActionElems(actionsList),
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
		DurationMs: ptr(int(duration)),
	}
	if err != nil {
		outcome.Error = ptr(err.Error())
	}

	endAt := start + duration
	title := fmt.Sprintf("action %s", actionName)
	a.emitEnvelope(outbox.Envelope{
		ID:       fmt.Sprintf("action:%s:%s:%d", targetID, actionName, start),
		Kind:     "event",
		Source:   ptr(envelopeSource),
		Type:     ptr("action." + actionName),
		Status:   statusPtr(contract.EnvelopeStatusDone),
		Title:    title,
		ParentID: optStr(targetID),
		Tags:     []string{"action", actionName},
		StartAt:  atMillis(start),
		EndAt:    atMillis(endAt),
		Outcome:  outcome,
		Metadata: map[string]interface{}{"target": targetID},
	})
	return err
}

// toActionElems converts decoded action objects into the generated element
// type. []map[string]any and []AgentEnvelopeActionsElem share an underlying
// shape but are distinct named types, so Go needs an explicit copy.
func toActionElems(in []map[string]any) []contract.AgentEnvelopeActionsElem {
	if len(in) == 0 {
		return nil
	}
	out := make([]contract.AgentEnvelopeActionsElem, len(in))
	for i, m := range in {
		out[i] = m
	}
	return out
}
