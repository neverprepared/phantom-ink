package main

import (
	"database/sql"
	"testing"

	_ "modernc.org/sqlite"
)

// This logic used to live in DashboardPanel.svelte: it built the owner maps
// from whatever widgets the client happened to hold, listed the jobs, and
// deleted orphans one at a time with every error swallowed by `catch {}`. The
// dangerous part was that an empty widget set — which is what a dashboard
// looks like before its config loads — read as "everything is an orphan".

func reconcileTestDB(t *testing.T) *DB {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	db := &DB{conn: conn}
	if err := db.migrate(); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	return db
}

func seedJob(t *testing.T, db *DB, id, profile, name, command, owner string) {
	t.Helper()
	_, err := db.conn.Exec(
		`INSERT INTO collect_jobs (id, profile, name, command, interval_s, enabled,
		   default_actions, last_error, created_at, source, owner_widget_id)
		 VALUES (?, ?, ?, ?, 60, 1, '[]', '', 0, 'widget', ?)`,
		id, profile, name, command, owner)
	if err != nil {
		t.Fatalf("seed %s: %v", id, err)
	}
}

func jobIDSet(t *testing.T, db *DB, profile string) map[string]bool {
	t.Helper()
	jobs, err := db.ListCollectJobs(profile)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	out := map[string]bool{}
	for _, j := range jobs {
		out[j.ID] = true
	}
	return out
}

// An empty owner set must delete NOTHING. "No widgets" is indistinguishable
// from "widgets not loaded yet", and the destructive reading of that ambiguity
// deleted live jobs.
func TestReconcileRefusesEmptyOwnerSet(t *testing.T) {
	db := reconcileTestDB(t)
	a := &App{db: db}
	seedJob(t, db, "job-1", "personal", "cpu", "echo 1", "widget-a")
	seedJob(t, db, "job-2", "personal", "mem", "echo 2", "widget-b")

	removed, err := a.ReconcileCollectJobs("personal", nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if removed != 0 {
		t.Errorf("removed %d jobs on an empty owner set", removed)
	}
	if got := jobIDSet(t, db, "personal"); len(got) != 2 {
		t.Errorf("job count = %d, want 2 — an empty claim set deleted live jobs", len(got))
	}
}

// The job each widget declares survives; its duplicates do not.
func TestReconcileKeepsDeclaredJobAndDropsDuplicates(t *testing.T) {
	db := reconcileTestDB(t)
	a := &App{db: db}
	seedJob(t, db, "keep", "personal", "cpu", "echo 1", "widget-a")
	seedJob(t, db, "dup-1", "personal", "cpu", "echo 1", "widget-a")
	seedJob(t, db, "dup-2", "personal", "cpu", "echo 1", "widget-a")

	removed, err := a.ReconcileCollectJobs("personal", []WidgetJobOwner{
		{WidgetID: "widget-a", KeepJobID: "keep", Label: "cpu", Command: "echo 1"},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if removed != 2 {
		t.Errorf("removed = %d, want 2", removed)
	}
	got := jobIDSet(t, db, "personal")
	if !got["keep"] {
		t.Error("the declared job was deleted")
	}
	if got["dup-1"] || got["dup-2"] {
		t.Error("a duplicate survived")
	}
}

// A job owned by a widget that is NOT in the claim set must be left alone —
// the claim set is "what these widgets own", not "the whole world".
func TestReconcileLeavesUnclaimedWidgetsAlone(t *testing.T) {
	db := reconcileTestDB(t)
	a := &App{db: db}
	seedJob(t, db, "keep", "personal", "cpu", "echo 1", "widget-a")
	seedJob(t, db, "other", "personal", "disk", "echo 3", "widget-z")

	if _, err := a.ReconcileCollectJobs("personal", []WidgetJobOwner{
		{WidgetID: "widget-a", KeepJobID: "keep", Label: "cpu", Command: "echo 1"},
	}); err != nil {
		t.Fatal(err)
	}
	if !jobIDSet(t, db, "personal")["other"] {
		t.Error("a job belonging to an unclaimed widget was deleted")
	}
}

// Legacy jobs predate owner_widget_id and are matched on "<label>::<command>".
func TestReconcileMatchesLegacyJobsByFingerprint(t *testing.T) {
	db := reconcileTestDB(t)
	a := &App{db: db}
	seedJob(t, db, "keep", "personal", "cpu", "echo 1", "widget-a")
	seedJob(t, db, "legacy-dup", "personal", "cpu", "echo 1", "") // no owner id

	removed, err := a.ReconcileCollectJobs("personal", []WidgetJobOwner{
		{WidgetID: "widget-a", KeepJobID: "keep", Label: "cpu", Command: "echo 1"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if removed != 1 {
		t.Errorf("removed = %d, want 1 (the legacy duplicate)", removed)
	}
	if jobIDSet(t, db, "personal")["legacy-dup"] {
		t.Error("the legacy duplicate survived")
	}
}

// An http-metric owner sends its config, not a command, and Go derives the
// fingerprint — so it matches a job the widget registered with that command.
func TestReconcileDerivesHTTPMetricFingerprint(t *testing.T) {
	db := reconcileTestDB(t)
	a := &App{db: db}
	cmd := httpMetricCommand("https://x/api", "Authorization: Bearer y", "data.count")
	seedJob(t, db, "keep", "personal", "hits", cmd, "widget-h")
	seedJob(t, db, "legacy-dup", "personal", "hits", cmd, "")

	removed, err := a.ReconcileCollectJobs("personal", []WidgetJobOwner{
		{
			WidgetID: "widget-h", KeepJobID: "keep", Label: "hits",
			HTTP: true, URL: "https://x/api", Header: "Authorization: Bearer y", Path: "data.count",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if removed != 1 {
		t.Errorf("removed = %d, want 1 — the derived fingerprint did not match", removed)
	}
}

// Reconciliation is scoped to one profile. Cross-profile deletion is a bug by
// project rule.
func TestReconcileIsProfileScoped(t *testing.T) {
	db := reconcileTestDB(t)
	a := &App{db: db}
	seedJob(t, db, "keep", "personal", "cpu", "echo 1", "widget-a")
	seedJob(t, db, "dup", "personal", "cpu", "echo 1", "widget-a")
	// Same widget id and command, different profile.
	seedJob(t, db, "other-profile", "gsa", "cpu", "echo 1", "widget-a")

	if _, err := a.ReconcileCollectJobs("personal", []WidgetJobOwner{
		{WidgetID: "widget-a", KeepJobID: "keep", Label: "cpu", Command: "echo 1"},
	}); err != nil {
		t.Fatal(err)
	}
	if !jobIDSet(t, db, "gsa")["other-profile"] {
		t.Error("reconciling one profile deleted another profile's job")
	}
}

// An owner with no keep id claims nothing, so it must not cause deletions.
func TestReconcileIgnoresOwnersWithoutAKeepID(t *testing.T) {
	db := reconcileTestDB(t)
	a := &App{db: db}
	seedJob(t, db, "job-1", "personal", "cpu", "echo 1", "widget-a")

	removed, err := a.ReconcileCollectJobs("personal", []WidgetJobOwner{
		{WidgetID: "widget-a", KeepJobID: "", Label: "cpu", Command: "echo 1"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if removed != 0 || !jobIDSet(t, db, "personal")["job-1"] {
		t.Error("an owner with no keep id caused a deletion")
	}
}

func TestReconcileRequiresAProfile(t *testing.T) {
	a := &App{db: reconcileTestDB(t)}
	if _, err := a.ReconcileCollectJobs("  ", []WidgetJobOwner{{WidgetID: "w", KeepJobID: "k"}}); err == nil {
		t.Error("expected an error for a blank profile")
	}
}

// The derived command must match what the frontend historically produced, or
// every existing http-metric job's fingerprint breaks on upgrade.
func TestHTTPMetricCommandShape(t *testing.T) {
	cases := []struct{ url, header, path, want string }{
		{"https://x/api", "", "", `op run -- curl -sf "https://x/api"`},
		{"https://x/api", "H: v", "", `op run -- curl -sf -H "H: v" "https://x/api"`},
		{"https://x/api", "", "a.b", `op run -- curl -sf "https://x/api" | jq -r '.a.b'`},
		{"https://x/api", "H: v", "a.b", `op run -- curl -sf -H "H: v" "https://x/api" | jq -r '.a.b'`},
	}
	for _, c := range cases {
		if got := httpMetricCommand(c.url, c.header, c.path); got != c.want {
			t.Errorf("httpMetricCommand(%q,%q,%q)\n got %q\nwant %q", c.url, c.header, c.path, got, c.want)
		}
	}
}
