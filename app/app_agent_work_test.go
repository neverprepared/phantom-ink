package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"phantom-ink/opensearch"
)

func TestMergeLivenessStampsMatchingSessions(t *testing.T) {
	hb := int64(264000)
	rows := []AgentWorkRow{
		{SessionID: "live", LastSeen: "2026-10-17T12:00:00Z"},
		{SessionID: "langfuse-only", LastSeen: "2026-10-17T11:00:00Z"},
	}
	live := []opensearch.AgentSession{
		{
			SessionID: "live", Liveness: opensearch.LivenessActive,
			LastActivityMS: 40000, LastHeartbeatMS: &hb,
			// Fresher than the Langfuse row: OpenSearch ingests in ~33s while
			// the Stop hook lags a whole turn.
			LastSeen: "2026-10-17T12:05:00Z",
		},
		{SessionID: "opensearch-only", Liveness: opensearch.LivenessIdle},
	}

	mergeLiveness(rows, live)

	if !rows[0].LivenessAvailable {
		t.Error("matching row did not get the overlay")
	}
	if rows[0].Liveness != opensearch.LivenessActive {
		t.Errorf("Liveness = %q, want active", rows[0].Liveness)
	}
	if rows[0].LastActivityMS != 40000 {
		t.Errorf("LastActivityMS = %d, want 40000", rows[0].LastActivityMS)
	}
	if rows[0].LastHeartbeatMS == nil || *rows[0].LastHeartbeatMS != 264000 {
		t.Error("heartbeat age not carried through")
	}
	// The fresher timestamp must win, or the panel reports a working session as
	// last active minutes ago.
	if rows[0].LastSeen != "2026-10-17T12:05:00Z" {
		t.Errorf("LastSeen = %q, want OpenSearch's fresher value", rows[0].LastSeen)
	}

	// A session Langfuse knows and OpenSearch does not must show NO dot rather
	// than defaulting to dormant, which would assert something unmeasured.
	if rows[1].LivenessAvailable {
		t.Error("unmatched row claims liveness is available")
	}
	if rows[1].Liveness != "" {
		t.Errorf("unmatched row Liveness = %q, want empty", rows[1].Liveness)
	}
	if rows[1].LastSeen != "2026-10-17T11:00:00Z" {
		t.Error("unmatched row's LastSeen was altered")
	}
}

// An OpenSearch session with no Langfuse counterpart must not appear: the list
// is Langfuse's, and inventing a row with no observations would show a session
// the drill-down cannot open.
func TestMergeLivenessNeverAddsRows(t *testing.T) {
	rows := []AgentWorkRow{{SessionID: "a"}}
	mergeLiveness(rows, []opensearch.AgentSession{
		{SessionID: "b"}, {SessionID: "c"},
	})
	if len(rows) != 1 || rows[0].SessionID != "a" {
		t.Errorf("rows = %+v, want the single Langfuse row untouched", rows)
	}
}

func TestMergeLivenessKeepsLangfuseTimestampWhenFresher(t *testing.T) {
	rows := []AgentWorkRow{{SessionID: "s", LastSeen: "2026-10-17T12:10:00Z"}}
	mergeLiveness(rows, []opensearch.AgentSession{
		{SessionID: "s", Liveness: opensearch.LivenessActive, LastSeen: "2026-10-17T12:00:00Z"},
	})
	if rows[0].LastSeen != "2026-10-17T12:10:00Z" {
		t.Errorf("LastSeen = %q; the newer of the two must win", rows[0].LastSeen)
	}
}

func TestMergeLivenessHandlesEmptyInputs(t *testing.T) {
	mergeLiveness(nil, nil)
	mergeLiveness([]AgentWorkRow{}, []opensearch.AgentSession{{SessionID: "x"}})
	rows := []AgentWorkRow{{SessionID: "x"}}
	mergeLiveness(rows, nil)
	if rows[0].LivenessAvailable {
		t.Error("no OpenSearch data must mean no overlay")
	}
}

// The Langfuse environment is stamped from LANGFUSE_TRACING_ENVIRONMENT at
// emission, not from the profile name. They coincide on the development
// machine, which is precisely what hid this: a mismatch produces a correctly
// empty list with no explanation.
func TestLangfuseEnvironmentPrefersTheStampedValue(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, ".env"),
		[]byte("LANGFUSE_TRACING_ENVIRONMENT=prod-eu\nOTHER=x\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	a := &App{config: &Config{WorkspacesRoot: filepath.Dir(dir)}}
	got := a.langfuseEnvironment(filepath.Base(dir))
	if got != "prod-eu" {
		t.Errorf("langfuseEnvironment = %q, want the stamped value prod-eu", got)
	}
}

// With no stamp, the profile name is the only sensible guess.
func TestLangfuseEnvironmentFallsBackToTheProfileName(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("OTHER=x\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	a := &App{config: &Config{WorkspacesRoot: filepath.Dir(dir)}}
	name := filepath.Base(dir)
	if got := a.langfuseEnvironment(name); got != name {
		t.Errorf("langfuseEnvironment = %q, want the profile name %q", got, name)
	}
}

// An unknown profile must fall back to the profile NAME, never to another
// profile's stamped environment read through the ambient WORKSPACE_HOME.
func TestLangfuseEnvironmentHandlesAnUnknownProfile(t *testing.T) {
	root := t.TempDir()
	t.Setenv("WORKSPACE_HOME", root)
	a := &App{config: &Config{WorkspacesRoot: root}}
	if got := a.langfuseEnvironment("nope"); got != "nope" {
		t.Errorf("langfuseEnvironment = %q, want nope — anything else means it "+
			"read another profile's environment", got)
	}
}

// strictProfileHome must never resolve to another profile's workspace.
// a.profileWorkspaceHome falls back to the AMBIENT WORKSPACE_HOME, so asking
// for a profile that does not exist previously returned the workspace that
// launched the app -- and would have handed back ITS Langfuse credentials.
func TestStrictProfileHomeRefusesUnknownProfiles(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "real"), 0o700); err != nil {
		t.Fatal(err)
	}
	// An ambient WORKSPACE_HOME is exactly the trap: it must be ignored.
	t.Setenv("WORKSPACE_HOME", filepath.Join(root, "real"))
	a := &App{config: &Config{WorkspacesRoot: root}}

	if got := a.strictProfileHome("real"); got != filepath.Join(root, "real") {
		t.Errorf("strictProfileHome(real) = %q, want the real home", got)
	}
	for _, bad := range []string{"nope", "", "..", ".", "../real", "a/b"} {
		if got := a.strictProfileHome(bad); got != "" {
			t.Errorf("strictProfileHome(%q) = %q, want \"\" — resolving to any "+
				"home here leaks another profile's credentials", bad, got)
		}
	}
}

func TestStrictProfileHomeRefusesWithoutAWorkspacesRoot(t *testing.T) {
	a := &App{config: &Config{}}
	if got := a.strictProfileHome("personal"); got != "" {
		t.Errorf("strictProfileHome = %q; with no root it must fail closed", got)
	}
	b := &App{}
	if got := b.strictProfileHome("personal"); got != "" {
		t.Errorf("strictProfileHome = %q with nil config; must fail closed", got)
	}
}

// The overlay is a decoration and must never be able to stop the list. A
// permanent spinner is exactly what happened when it could.
func TestOverlayTimeoutIsShorterThanTheQueryTimeout(t *testing.T) {
	if livenessOverlayTimeout >= agentSessionQueryTimeout {
		t.Errorf("livenessOverlayTimeout %v must be shorter than "+
			"agentSessionQueryTimeout %v — the overlay is optional and should "+
			"give up first", livenessOverlayTimeout, agentSessionQueryTimeout)
	}
	if livenessOverlayTimeout <= 0 {
		t.Error("an unbounded overlay can hang the whole list")
	}
}

// The chart timeout must exceed the list's: GetCharts issues four aggregations
// over a wider window than a single session page.
func TestChartsTimeoutExceedsTheListTimeout(t *testing.T) {
	if chartsQueryTimeout <= agentSessionQueryTimeout {
		t.Errorf("chartsQueryTimeout %v should exceed agentSessionQueryTimeout %v",
			chartsQueryTimeout, agentSessionQueryTimeout)
	}
}

// The chart payload must marshal with empty arrays, never nulls: the panel
// iterates all four, and a null threw during render once already.
//
// Also pins the shape FLAT. Embedding *langfuse.Charts made Wails' TypeScript
// generator emit a class referencing langfuse.Point and langfuse.Share without
// defining either, which broke type-checking of the generated models.ts.
func TestAgentWorkChartsMarshalsWithoutNulls(t *testing.T) {
	blob, err := json.Marshal(&AgentWorkCharts{Granularity: "day"})
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"cost_series", "obs_series", "cost_by_model", "obs_by_type"} {
		if !strings.Contains(string(blob), `"`+field+`"`) {
			t.Errorf("%s missing from the payload: %s", field, blob)
		}
	}
	// A zero-value struct has nil slices, so this documents that callers must
	// populate them -- which GetAgentWorkCharts does via make().
	var out AgentWorkCharts
	if err := json.Unmarshal(blob, &out); err != nil {
		t.Fatalf("round trip: %v", err)
	}
	if out.Granularity != "day" {
		t.Errorf("granularity lost in round trip: %+v", out)
	}
}
