package doctor

import (
	"context"
	"errors"
	"strings"
	"testing"
)

type stubCheck struct {
	id    string
	scope Scope
	res   Result
}

func (s stubCheck) ID() string                           { return s.id }
func (s stubCheck) Scope() Scope                         { return s.scope }
func (s stubCheck) Run(context.Context, Executor) Result { return s.res }

func TestRegisterRejectsDuplicateID(t *testing.T) {
	r := NewRegistry()
	if err := r.Register(stubCheck{id: "a", scope: ScopeLocal}); err != nil {
		t.Fatalf("first register: %v", err)
	}
	if err := r.Register(stubCheck{id: "a", scope: ScopeLocal}); err == nil {
		t.Fatal("want error on duplicate id, got nil")
	}
}

func TestByID(t *testing.T) {
	r := NewRegistry()
	_ = r.Register(stubCheck{id: "a", scope: ScopeLocal})
	if _, ok := r.ByID("a"); !ok {
		t.Fatal("want to find check a")
	}
	if _, ok := r.ByID("nope"); ok {
		t.Fatal("want miss for unknown id")
	}
}

func TestSortResultsWorstFirst(t *testing.T) {
	in := []Result{
		{ID: "p", State: StatePass},
		{ID: "w", State: StateWarn},
		{ID: "f", State: StateFail},
		{ID: "u", State: StateUnknown},
	}
	SortResults(in)
	got := []string{in[0].ID, in[1].ID, in[2].ID, in[3].ID}
	want := []string{"f", "u", "w", "p"}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("sort order = %v, want %v", got, want)
		}
	}
}

func TestAutoSafeRemedyMustHaveConfirm(t *testing.T) {
	bad := Remedy{Command: "do thing", Exec: []string{"true"}, AutoSafe: true, Confirm: ""}
	if err := bad.Validate(); err == nil {
		t.Fatal("want error: auto-safe remedy with no confirm text")
	}
	ok := Remedy{Command: "do thing", Exec: []string{"true"}, AutoSafe: true, Confirm: "This will do thing."}
	if err := ok.Validate(); err != nil {
		t.Fatalf("want valid, got %v", err)
	}
}

// Prose is a fine thing to show an operator and a terrible thing to execute.
func TestAutoSafeRemedyMustHaveExecArgv(t *testing.T) {
	prose := Remedy{
		Command:  "restart phantom-ink to run migrations",
		AutoSafe: true,
		Confirm:  "Restart the app.",
	}
	if err := prose.Validate(); err == nil {
		t.Fatal("want error: auto-safe remedy with no exec argv")
	}
}

// recordingExec wraps fakeExec and additionally records every argv passed to
// Shell, so a test can assert on what ran rather than just on what Shell was
// stubbed to return.
type recordingExec struct {
	fakeExec
	shellCalls [][]string
}

func (r *recordingExec) Shell(ctx context.Context, cmd []string) (string, int, error) {
	r.shellCalls = append(r.shellCalls, cmd)
	return r.fakeExec.Shell(ctx, cmd)
}

// Pin: RunDoctor's read-only promise — diagnosis never remediates. This
// registers a check whose Result carries an AutoSafe remedy with a real Exec
// argv, runs Registry.Run against a recordingExec that logs every Shell
// invocation, and asserts the remedy's own argv was never among them. This
// pins the actual promise Run makes (it only observes) rather than the
// incidental fact that RunDoctor itself performs no disk writes; if Run (or
// some future Check.Run) were changed to apply a matching remedy
// automatically, the remedy's exact argv would show up in shellCalls and this
// test would fail.
func TestRegistryRunNeverInvokesARemedy(t *testing.T) {
	remedyExec := []string{"touch", "/tmp/doctor-remedy-pin-marker"}
	check := stubCheck{
		id:    "remedy-check",
		scope: ScopeLocal,
		res: Result{
			ID:    "remedy-check",
			Scope: ScopeLocal,
			State: StateFail,
			Remedy: &Remedy{
				Command:  "touch /tmp/doctor-remedy-pin-marker",
				Exec:     remedyExec,
				AutoSafe: true,
				Confirm:  "This would create a marker file.",
			},
		},
	}
	r := NewRegistry()
	if err := r.Register(check); err != nil {
		t.Fatalf("register: %v", err)
	}
	ex := &recordingExec{}
	results := r.Run(context.Background(), ex, "")
	if len(results) != 1 || results[0].Remedy == nil {
		t.Fatalf("expected one result carrying a remedy, got %+v", results)
	}
	for _, call := range ex.shellCalls {
		if len(call) != len(remedyExec) {
			continue
		}
		match := true
		for i := range call {
			if call[i] != remedyExec[i] {
				match = false
				break
			}
		}
		if match {
			t.Fatalf("remedy exec %v was invoked during Run; diagnosis must never remediate", remedyExec)
		}
	}
}

// remedyCheck's Run returns `state` on the first call and `after` (when set)
// on every subsequent call, so a test can tell "the caller got the fresh
// re-run result" apart from "the caller got the stale pre-check result" —
// something a check that reports the same State on every call cannot do.
type remedyCheck struct {
	id    string
	state State
	after State
	rem   *Remedy
	runs  *int
}

func (c remedyCheck) ID() string   { return c.id }
func (c remedyCheck) Scope() Scope { return ScopeLocal }
func (c remedyCheck) Run(context.Context, Executor) Result {
	*c.runs++
	st := c.state
	if *c.runs > 1 && c.after != "" {
		st = c.after
	}
	return Result{ID: c.id, State: st, Remedy: c.rem}
}

// TestApplyRemedyRefusesNonAutoSafe proves both that a non-auto-safe remedy
// is refused AND that it was never executed. Asserting the error alone is not
// enough: a mutant that runs the remedy and only then checks AutoSafe (a
// "leaked execution" reordering, not a removal) would still return
// ErrNotAutoSafe and pass. recordingExec lets this test see what actually
// reached a shell, not just what the function returned.
func TestApplyRemedyRefusesNonAutoSafe(t *testing.T) {
	runs := 0
	r := NewRegistry()
	_ = r.Register(remedyCheck{
		id: "x", state: StateFail, runs: &runs,
		rem: &Remedy{Command: "rm -rf /", AutoSafe: false},
	})
	ex := &recordingExec{}
	_, err := r.ApplyRemedy(context.Background(), ex, "x")
	if !errors.Is(err, ErrNotAutoSafe) {
		t.Fatalf("err = %v, want ErrNotAutoSafe", err)
	}
	if len(ex.shellCalls) != 0 {
		t.Fatalf("shell was invoked %v for a non-auto-safe remedy; it must never run", ex.shellCalls)
	}
}

// TestApplyRemedyUnknownIDErrors additionally proves an unknown id never
// reaches a shell — not just that it returns an error.
func TestApplyRemedyUnknownIDErrors(t *testing.T) {
	r := NewRegistry()
	ex := &recordingExec{}
	if _, err := r.ApplyRemedy(context.Background(), ex, "nope"); err == nil {
		t.Fatal("want an error for an unknown check id")
	}
	if len(ex.shellCalls) != 0 {
		t.Fatalf("shell was invoked %v for an unknown check id; it must never run", ex.shellCalls)
	}
}

// TestApplyRemedyNoRemedyErrors additionally proves a check with no remedy
// never reaches a shell — not just that it returns an error.
func TestApplyRemedyNoRemedyErrors(t *testing.T) {
	runs := 0
	r := NewRegistry()
	_ = r.Register(remedyCheck{id: "x", state: StatePass, runs: &runs, rem: nil})
	ex := &recordingExec{}
	if _, err := r.ApplyRemedy(context.Background(), ex, "x"); err == nil {
		t.Fatal("want an error when the check offers no remedy")
	}
	if len(ex.shellCalls) != 0 {
		t.Fatalf("shell was invoked %v for a check with no remedy; it must never run", ex.shellCalls)
	}
}

// After a fix, the caller must see a re-run result, not an optimistic green,
// and not the stale pre-check result either. The check reports StateFail on
// its first Run and StatePass on every call after, so returning the
// pre-check result (a "discard the re-run" mutation that keeps the function
// shape intact — c.Run(ctx, ex); return pre, nil) is distinguishable from
// returning the actual re-run: the former yields StateFail here, the latter
// StatePass.
func TestApplyRemedyReRunsTheCheck(t *testing.T) {
	runs := 0
	r := NewRegistry()
	_ = r.Register(remedyCheck{
		id: "x", state: StateFail, after: StatePass, runs: &runs,
		rem: &Remedy{Command: "true", Exec: []string{"true"}, AutoSafe: true, Confirm: "Run true."},
	})
	ex := &fakeExec{shellOut: map[string]fakeShell{"true": {code: 0}}}
	got, err := r.ApplyRemedy(context.Background(), ex, "x")
	if err != nil {
		t.Fatalf("apply: %v", err)
	}
	if runs < 2 {
		t.Fatalf("check ran %d times, want the pre-check plus a re-run", runs)
	}
	if got.State != StatePass {
		t.Fatalf("state = %q, want StatePass — the result must be the re-run's truth (StatePass), not the discarded pre-check result (StateFail)", got.State)
	}
}

// Pin: a remedy that ran and was REJECTED must not read as one that worked.
// Executor.Shell reports a non-zero exit as (out, code, nil), so the obvious
// `if _, _, err := ex.Shell(...)` discards the only evidence that the fix was
// refused — `ln: Permission denied` then looks exactly like success, and the
// operator is told only that the check is "still failing".
//
// This test pins both halves at once. The stub exits 1 with a real rejection
// message, so discarding `code` loses the error entirely; and the check reports
// StateFail first and StateWarn after, so returning the stale pre-fix result
// instead of the re-run is distinguishable from returning the re-run.
func TestApplyRemedyNonZeroExitIsAnError(t *testing.T) {
	runs := 0
	r := NewRegistry()
	_ = r.Register(remedyCheck{
		id: "x", state: StateFail, after: StateWarn, runs: &runs,
		rem: &Remedy{
			Command: "ln -sf a b", Exec: []string{"ln", "-sf", "a", "b"},
			AutoSafe: true, Confirm: "Link a to b.",
		},
	})
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"ln -sf a b": {out: "ln: b: Permission denied\n", code: 1},
	}}
	got, err := r.ApplyRemedy(context.Background(), ex, "x")
	if err == nil {
		t.Fatal("want an error: the remedy exited non-zero, which must not read as success")
	}
	if !strings.Contains(err.Error(), "1") {
		t.Fatalf("err = %q, want it to name the exit code", err)
	}
	if !strings.Contains(err.Error(), "Permission denied") {
		t.Fatalf("err = %q, want it to carry the remedy's own output", err)
	}
	if got.State != StateWarn {
		t.Fatalf("state = %q, want StateWarn — the caller must still get the re-run observation, not the stale pre-fix result (StateFail)", got.State)
	}
}
