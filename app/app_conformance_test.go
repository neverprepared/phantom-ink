package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/santhosh-tekuri/jsonschema/v5"
	_ "modernc.org/sqlite"

	"phantom-ink/internal/contract"
	"phantom-ink/internal/outbox"
)

// TestEmitHelpersConformToContract is the T5 conformance gate: every emit*
// helper's real, marshaled output must validate against the pinned
// timeline-entry schema (contract.SchemaJSON, embedded from the fetched
// phantom-contracts tag). A field the model drops or renames — the drift this
// task exists to prevent — fails here instead of silently shipping to the bus.
//
// The envelopes are captured through the same outbox delivery path production
// uses, so what we validate is exactly what would go on the wire.
func TestEmitHelpersConformToContract(t *testing.T) {
	sch := compileContractSchema(t)
	a, cap := newConformanceApp(t)

	// Drive each emit* helper with a representative input. The set covers every
	// producer: sequence run/step (both phases), task state change, collected
	// entry with actions, and an action outcome (success + failure).
	ws := "work"
	cc := sequenceContext{Input: "do the thing", Cwd: "/tmp"}
	a.emitSequenceEnvelope(SequenceRunEvent{RunID: "r1", SequenceID: "s1", Phase: "run:start", Status: "running"}, ws, cc)
	a.emitSequenceEnvelope(SequenceRunEvent{RunID: "r1", SequenceID: "s1", Phase: "run:done", Status: "success"}, ws, cc)
	a.emitSequenceEnvelope(SequenceRunEvent{RunID: "r1", SequenceID: "s1", Phase: "step:start", StepIndex: 0, AgentID: "dev"}, ws, cc)
	a.emitSequenceEnvelope(SequenceRunEvent{RunID: "r1", SequenceID: "s1", Phase: "step:done", StepIndex: 0, AgentID: "dev", Status: "failed", Error: "boom", ExitCode: 1}, ws, cc)

	a.emitTaskEnvelope("t1", "s1", TaskRunning, 1, "")
	a.emitTaskEnvelope("t2", "s1", TaskFailed, 3, "exceeded retries")

	a.emitCollectedEntryEnvelope(
		CollectJob{Name: "calendar", Profile: "work"},
		CollectedEntry{
			JobID: "j1", EntryID: "e1", Profile: "work", Kind: "event",
			Title: "Standup", Status: "action_needed", URL: "https://meet",
			Tags:    []string{"calendar"},
			Actions: json.RawMessage(`[{"label":"Open","kind":"open_url","url":"https://meet"}]`),
		},
	)

	_ = a.recordAction("task:t1", "retry", ActorUser, func() error { return nil })
	_ = a.recordAction("task:t2", "respond", ActorUser, func() error { return errors.New("nope") })

	// 4 sequence + 2 task + 1 entry + 2 action = 9 envelopes.
	const want = 9
	envs := cap.waitFor(t, want, 3*time.Second)
	if len(envs) != want {
		t.Fatalf("captured %d envelopes, want %d", len(envs), want)
	}

	for i, env := range envs {
		raw, err := json.Marshal(env)
		if err != nil {
			t.Fatalf("marshal envelope %d: %v", i, err)
		}
		var doc any
		if err := json.Unmarshal(raw, &doc); err != nil {
			t.Fatalf("unmarshal envelope %d: %v", i, err)
		}
		if err := sch.Validate(doc); err != nil {
			t.Errorf("envelope %d (%s) fails v2.1 conformance: %v\njson=%s",
				i, envID(env), err, raw)
		}
	}
}

// envID pulls a stable label for test failure messages.
func envID(e outbox.Envelope) string {
	if e.Type != nil {
		return e.ID + " type=" + *e.Type
	}
	return e.ID
}

// compileContractSchema compiles the embedded pinned schema. Using the embedded
// bytes (not a network fetch) keeps the test hermetic — CI has no access to the
// private phantom-contracts repo.
func compileContractSchema(t *testing.T) *jsonschema.Schema {
	t.Helper()
	c := jsonschema.NewCompiler()
	if err := c.AddResource("timeline-entry.schema.json", bytes.NewReader(contract.SchemaJSON)); err != nil {
		t.Fatalf("add schema resource: %v", err)
	}
	sch, err := c.Compile("timeline-entry.schema.json")
	if err != nil {
		t.Fatalf("compile schema: %v", err)
	}
	return sch
}

// newConformanceApp boots an App with an outbox, a capturing deliverer, and a
// bare in-memory DB (no tables). emitTaskEnvelope needs a non-nil db but
// tolerates missing rows/tables — GetTask/GetSequence report "not found" — so
// this is enough to exercise the real marshaling path.
func newConformanceApp(t *testing.T) (*App, *capturingDeliverer) {
	t.Helper()
	appDB, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open app db: %v", err)
	}
	appDB.SetMaxOpenConns(1)
	if _, err := appDB.Exec(outboxSchema); err != nil {
		t.Fatalf("outbox schema: %v", err)
	}
	t.Cleanup(func() { appDB.Close() })

	cap := &capturingDeliverer{}
	a := &App{db: &DB{conn: appDB}}
	a.outbox = outbox.New(appDB, cap.deliver, outbox.Options{CoalesceWait: 10 * time.Millisecond})

	ctx, cancel := context.WithCancel(context.Background())
	a.outbox.Start(ctx)
	t.Cleanup(func() {
		cancel()
		a.outbox.Stop()
	})
	return a, cap
}
