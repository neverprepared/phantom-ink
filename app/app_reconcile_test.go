package main

import (
	"database/sql"
	"testing"

	_ "modernc.org/sqlite"
)

// migratedTestDB opens an in-memory DB with the real migration chain applied, so
// these tests exercise the actual integrations schema (including the
// desired_running column) rather than a hand-written stand-in.
func migratedTestDB(t *testing.T) *DB {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	conn.SetMaxOpenConns(1)
	t.Cleanup(func() { conn.Close() })
	db := &DB{conn: conn}
	if err := db.migrate(); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	return db
}

func neverRunning(ServiceDef, ServiceConfig) bool  { return false }
func alwaysRunning(ServiceDef, ServiceConfig) bool { return true }

// A service the user started (desired_running) that is no longer up is the whole
// point of reconcile: it must be in the plan.
func TestReconcilePlanStartsDesiredButStoppedService(t *testing.T) {
	defs := []ServiceDef{{Name: "mindwalk", Label: "Memory Graph", Port: 9997}}
	rows := map[string]IntegrationRow{
		"mindwalk": {Name: "mindwalk", Enabled: true, DesiredRunning: true},
	}

	got := reconcilePlan(defs, rows, neverRunning)

	if len(got) != 1 || got[0] != "mindwalk" {
		t.Fatalf("reconcilePlan() = %v, want [mindwalk]", got)
	}
}

// The design decision, locked down: `enabled` is config intent only. A service
// that is enabled but was never started (or was stopped) must NOT be started —
// otherwise reconcile spins up MinIO/Ollama/1Password at every launch.
func TestReconcilePlanIgnoresEnabledWithoutDesiredRunning(t *testing.T) {
	defs := []ServiceDef{
		{Name: "minio", Port: 9090},
		{Name: "ollama", Port: 11434},
		{Name: "1password", Port: 8080},
	}
	rows := map[string]IntegrationRow{
		"minio":     {Name: "minio", Enabled: true},
		"ollama":    {Name: "ollama", Enabled: true},
		"1password": {Name: "1password", Enabled: true},
	}

	if got := reconcilePlan(defs, rows, neverRunning); len(got) != 0 {
		t.Fatalf("reconcilePlan() = %v, want empty (enabled alone must not start anything)", got)
	}
}

func TestReconcilePlanSkipsAlreadyRunning(t *testing.T) {
	defs := []ServiceDef{{Name: "mindwalk", Port: 9997}}
	rows := map[string]IntegrationRow{
		"mindwalk": {Name: "mindwalk", DesiredRunning: true},
	}

	if got := reconcilePlan(defs, rows, alwaysRunning); len(got) != 0 {
		t.Fatalf("reconcilePlan() = %v, want empty (already running)", got)
	}
}

// Native services are managed outside docker, remote ones live on another host,
// and Platform services belong to the Platform Services card — compose up is
// wrong for all three even when desired_running is set.
func TestReconcilePlanSkipsNativeRemoteAndPlatform(t *testing.T) {
	defs := []ServiceDef{
		{Name: "phantom-brain-mesh", Native: true},
		{Name: "opensearch", Port: 5601},
		{Name: "langfuse", Platform: true},
	}
	rows := map[string]IntegrationRow{
		"phantom-brain-mesh": {Name: "phantom-brain-mesh", DesiredRunning: true},
		"opensearch":         {Name: "opensearch", DesiredRunning: true, Remote: true},
		"langfuse":           {Name: "langfuse", DesiredRunning: true},
	}

	if got := reconcilePlan(defs, rows, neverRunning); len(got) != 0 {
		t.Fatalf("reconcilePlan() = %v, want empty", got)
	}
}

// A service with no row at all must not be started.
func TestReconcilePlanSkipsUnknownRow(t *testing.T) {
	defs := []ServiceDef{{Name: "mindwalk", Port: 9997}}

	if got := reconcilePlan(defs, map[string]IntegrationRow{}, neverRunning); len(got) != 0 {
		t.Fatalf("reconcilePlan() = %v, want empty (no row)", got)
	}
}

func TestSetDesiredRunningRoundTrip(t *testing.T) {
	db := migratedTestDB(t)
	if err := db.UpsertIntegration(IntegrationRow{Name: "mindwalk", Enabled: true}); err != nil {
		t.Fatalf("seed: %v", err)
	}

	if row, _ := db.GetIntegration("mindwalk"); row.DesiredRunning {
		t.Fatal("desired_running should default to false")
	}

	if err := db.SetDesiredRunning("mindwalk", true); err != nil {
		t.Fatalf("set true: %v", err)
	}
	row, ok := db.GetIntegration("mindwalk")
	if !ok || !row.DesiredRunning {
		t.Fatalf("after SetDesiredRunning(true): row=%+v ok=%v", row, ok)
	}

	if err := db.SetDesiredRunning("mindwalk", false); err != nil {
		t.Fatalf("set false: %v", err)
	}
	if row, _ := db.GetIntegration("mindwalk"); row.DesiredRunning {
		t.Fatal("after SetDesiredRunning(false): want false")
	}
}

// SetServiceConfig (the Integrations panel toggles) goes through
// UpsertIntegration, which knows nothing about desired_running. It must not
// clobber it, or editing a URL would silently disable self-healing.
func TestUpsertIntegrationPreservesDesiredRunning(t *testing.T) {
	db := migratedTestDB(t)
	if err := db.UpsertIntegration(IntegrationRow{Name: "mindwalk"}); err != nil {
		t.Fatalf("seed: %v", err)
	}
	if err := db.SetDesiredRunning("mindwalk", true); err != nil {
		t.Fatalf("set: %v", err)
	}

	// Simulate the UI editing the local URL / enabled flag.
	if err := db.UpsertIntegration(IntegrationRow{
		Name: "mindwalk", Enabled: true, LocalURL: "http://localhost:9997",
	}); err != nil {
		t.Fatalf("upsert: %v", err)
	}

	row, ok := db.GetIntegration("mindwalk")
	if !ok {
		t.Fatal("row missing")
	}
	if !row.DesiredRunning {
		t.Error("UpsertIntegration clobbered desired_running")
	}
	if row.LocalURL != "http://localhost:9997" {
		t.Errorf("LocalURL = %q, want the edited value", row.LocalURL)
	}
}

// SetDesiredRunning on a name with no row yet must create one, so a Start on a
// freshly-seeded service still records intent.
func TestSetDesiredRunningCreatesMissingRow(t *testing.T) {
	db := migratedTestDB(t)

	if err := db.SetDesiredRunning("mindwalk", true); err != nil {
		t.Fatalf("set: %v", err)
	}
	row, ok := db.GetIntegration("mindwalk")
	if !ok || !row.DesiredRunning {
		t.Fatalf("row=%+v ok=%v, want desired_running=true", row, ok)
	}
}
