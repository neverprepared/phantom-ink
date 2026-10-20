package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

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
// iterates all three, and a null threw during render once already.
//
// Also pins the shape FLAT. Embedding *langfuse.Charts made Wails' TypeScript
// generator emit a class referencing langfuse.Point and langfuse.Share without
// defining either, which broke type-checking of the generated models.ts.
func TestAgentWorkChartsMarshalsWithoutNulls(t *testing.T) {
	blob, err := json.Marshal(&AgentWorkCharts{Granularity: "day"})
	if err != nil {
		t.Fatal(err)
	}
	// obs_series and obs_by_type were Langfuse concepts and are gone; the
	// breakdown is now cost_by_source, which is the split Langfuse could not
	// produce.
	for _, field := range []string{"cost_series", "cost_by_model", "cost_by_source"} {
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
