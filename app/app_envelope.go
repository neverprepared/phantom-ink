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
// Callers use the typed helpers (emitCollectedEntryEnvelope, recordAction)
// below; this is the bare interface for ad-hoc events.
func (a *App) emitEnvelope(env outbox.Envelope) {
	if a == nil || a.outbox == nil {
		return
	}
	if err := a.outbox.Append(env); err != nil {
		fmt.Fprintf(os.Stderr, "outbox append failed: %v\n", err)
	}
}

// envelopeSource is the producer identifier used in every envelope this app
// emits. Brainbox treats it as part of the envelope's provenance and the UI
// uses it for source filtering.
const envelopeSource = "wails-app@local"

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
