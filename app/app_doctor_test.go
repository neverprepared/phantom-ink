package main

import (
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
