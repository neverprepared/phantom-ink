package doctor

import (
	"context"
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
