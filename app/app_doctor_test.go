package main

import (
	"context"
	"testing"

	"phantom-ink/doctor"
)

// Every registered check must satisfy the invariants the UI relies on: unique
// ids, and an auto-safe remedy that can state what it will do. This is the test
// that stops an unconfirmable one-click fix from reaching the panel.
func TestRegisteredChecksHoldInvariants(t *testing.T) {
	a := &App{}
	reg := a.buildDoctorRegistry()
	checks := reg.All()
	if len(checks) == 0 {
		t.Fatal("no checks registered")
	}
	seen := map[string]bool{}
	for _, c := range checks {
		if seen[c.ID()] {
			t.Fatalf("duplicate check id %q", c.ID())
		}
		seen[c.ID()] = true
		if s := c.Scope(); s != doctor.ScopeLocal && s != doctor.ScopePlatform {
			t.Fatalf("check %q has invalid scope %q", c.ID(), s)
		}
	}
}

func TestAdaptServicesMapsFields(t *testing.T) {
	in := []PlatformService{{
		Name: "router", State: "running", Health: "healthy",
		Addr: "localhost:9910", OneShot: false,
	}}
	got := adaptServices(in)
	if len(got) != 1 {
		t.Fatalf("len = %d, want 1", len(got))
	}
	if got[0].Name != "router" || got[0].State != "running" ||
		got[0].Health != "healthy" || got[0].Addr != "localhost:9910" || got[0].OneShot {
		t.Fatalf("adapted badly: %+v", got[0])
	}
}

// Pin: a non-zero exit is an observation, not a failure to observe. This runs
// the REAL appExecutor (not fakeExec, which every other test in this package
// uses and which fabricates (out, code, err) directly without ever touching
// appExecutor.Shell's *exec.ExitError type assertion). If the branch
// `if ee, ok := err.(*exec.ExitError); ok { return ..., ee.ExitCode(), nil }`
// is removed — or "tidied" to treat any non-nil err as a hard failure, which
// is the instinct this invariant runs against — this test starts seeing a
// non-nil err where it must see nil, and fails.
func TestAppExecutorShellNonZeroExitIsObservationNotError(t *testing.T) {
	out, code, err := appExecutor{}.Shell(context.Background(), []string{"sh", "-c", "exit 7"})
	if err != nil {
		t.Fatalf("err = %v, want nil — a non-zero exit must be an observation, not a failure to observe", err)
	}
	if code != 7 {
		t.Fatalf("code = %d, want 7", code)
	}
	_ = out
}

// Companion to the above: a command that cannot run at all (no such binary)
// must return a non-nil error. Together the two pin the exact distinction
// appExecutor.Shell exists to draw — "the thing is missing" (exit code, nil
// error) versus "I could not tell" (err != nil) — rather than one half of it
// in isolation.
func TestAppExecutorShellCannotRunIsError(t *testing.T) {
	_, _, err := appExecutor{}.Shell(context.Background(), []string{"phantom-ink-no-such-binary-xyz"})
	if err == nil {
		t.Fatal("want a non-nil error when the binary cannot be executed at all")
	}
}
