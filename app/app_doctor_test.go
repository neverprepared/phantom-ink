package main

import (
	"context"
	"database/sql"
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

// doctorTestDB is the minimum DB a doctor wiring test needs: platformNode()
// reads one row out of the settings table.
func doctorTestDB(t *testing.T, node string) *DB {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	conn.SetMaxOpenConns(1)
	if _, err := conn.Exec(`CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')`); err != nil {
		t.Fatalf("schema: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	db := &DB{conn: conn}
	if node != "" {
		if err := db.SetSetting(settingPlatformNode, node); err != nil {
			t.Fatalf("set %s: %v", settingPlatformNode, err)
		}
	}
	return db
}

func registeredTraefikCheck(t *testing.T, a *App) doctor.TraefikBoundCheck {
	t.Helper()
	c, ok := a.buildDoctorRegistry().ByID("traefik-bound")
	if !ok {
		t.Fatal("traefik-bound is not registered")
	}
	tc, ok := c.(doctor.TraefikBoundCheck)
	if !ok {
		t.Fatalf("traefik-bound is %T, want doctor.TraefikBoundCheck", c)
	}
	return tc
}

// Pin the wiring, not just the check. TraefikBoundCheck can only refuse to
// probe a remote node's port if the registry actually tells it there is one;
// buildDoctorRegistry originally never consulted platformNode(), which is the
// root cause behind the false pass. Dropping `RemoteNode: a.platformNode()`
// from the construction compiles and leaves every doctor-package test green,
// so the wiring needs its own pin.
func TestDoctorRegistryPassesThePlatformNodeToTraefik(t *testing.T) {
	a := &App{db: doctorTestDB(t, "m3-64")}
	if got := registeredTraefikCheck(t, a).RemoteNode; got != "m3-64" {
		t.Fatalf("RemoteNode = %q, want %q — the check cannot refuse an unprobeable remote port it was never told about", got, "m3-64")
	}
}

// Companion: with no node configured the field must stay empty, or the local
// probe (the whole point of the check) would never run.
func TestDoctorRegistryLeavesTraefikLocalWhenNoNodeSet(t *testing.T) {
	a := &App{db: doctorTestDB(t, "")}
	if got := registeredTraefikCheck(t, a).RemoteNode; got != "" {
		t.Fatalf("RemoteNode = %q, want empty for a local platform", got)
	}
}

func registeredPortShadowCheck(t *testing.T, a *App) doctor.PortShadowCheck {
	t.Helper()
	c, ok := a.buildDoctorRegistry().ByID("port-shadow")
	if !ok {
		t.Fatal("port-shadow is not registered")
	}
	pc, ok := c.(doctor.PortShadowCheck)
	if !ok {
		t.Fatalf("port-shadow is %T, want doctor.PortShadowCheck", c)
	}
	return pc
}

// The same wiring pin as the traefik one, from the other side: port-shadow can
// only decline to answer about a remote platform if the registry tells it there
// is one. Dropping `RemoteNode: a.platformNode()` compiles and leaves every
// doctor-package test green.
func TestDoctorRegistryPassesThePlatformNodeToPortShadow(t *testing.T) {
	a := &App{db: doctorTestDB(t, "m3-64")}
	if got := registeredPortShadowCheck(t, a).RemoteNode; got != "m3-64" {
		t.Fatalf("RemoteNode = %q, want %q — a local listener shadows nothing when the platform is remote", got, "m3-64")
	}
}

func TestDoctorRegistryLeavesPortShadowLocalWhenNoNodeSet(t *testing.T) {
	a := &App{db: doctorTestDB(t, "")}
	if got := registeredPortShadowCheck(t, a).RemoteNode; got != "" {
		t.Fatalf("RemoteNode = %q, want empty for a local platform", got)
	}
}

// doctorShippedCheckIDs is the registered set, exactly. Four checks the spec
// designed — registry-auth, vault-reachability, sor-migrations and
// profile-api-key — are deliberately absent; see the spec's "Deferred checks".
var doctorShippedCheckIDs = []string{
	"app-db",
	"direnv-approval",
	"docker-symlink",
	"gateway-mcp-pin",
	"github-token-shadow",
	"port-shadow",
	"service-health",
	"traefik-bound",
}

// Pin the exact registered SET, not just its invariants. This is not
// belt-and-braces: the drift it guards against actually happened on this
// branch. The spec advertised a twelve-check catalog while eight were
// registered, and nothing in CI noticed — the gap surfaced in human review,
// which is the expensive place to find it. An exact-set assertion fails on a
// drop, an addition, or a swap, so the next person who cancels or adds a check
// has to come here and say so, and the spec's inventory stops being able to
// silently diverge from the code.
//
// Adding a check? Add its id here and to the spec's catalog in the same commit.
func TestRegisteredCheckSetIsExactlyTheShippedEight(t *testing.T) {
	a := &App{}
	got := map[string]bool{}
	for _, c := range a.buildDoctorRegistry().All() {
		got[c.ID()] = true
	}
	if len(got) != len(doctorShippedCheckIDs) {
		t.Errorf("registered %d checks, want exactly %d", len(got), len(doctorShippedCheckIDs))
	}
	want := map[string]bool{}
	for _, id := range doctorShippedCheckIDs {
		want[id] = true
		if !got[id] {
			t.Errorf("check %q is expected but not registered", id)
		}
	}
	for id := range got {
		if !want[id] {
			t.Errorf("check %q is registered but not in the expected set — add it here and to the spec's catalog", id)
		}
	}
}
