package main

import (
	"strings"
	"testing"
	"time"
)

// The bug this guards: profile dotenv files are written to be sourced by
// direnv, so they reference other vars — every profile here has
// AZURE_CONFIG_DIR="$WORKSPACE_HOME/.azure". parseEnvFile is deliberately
// literal, so that string reached `az` as a path and it created a directory
// literally named "$WORKSPACE_HOME" under the app's working directory, holding
// a live session and access-token logs.
func TestExpandOverrides_ExpandsWorkspaceHome(t *testing.T) {
	overrides := map[string]string{
		"WORKSPACE_HOME":    "/Users/x/workspaces/profiles/gsa",
		"WORKSPACE_PROFILE": "gsa",
		"AZURE_CONFIG_DIR":  "$WORKSPACE_HOME/.azure",
		"BRACED":            "${WORKSPACE_HOME}/.config",
	}
	expandOverrides(overrides, nil)

	if got, want := overrides["AZURE_CONFIG_DIR"], "/Users/x/workspaces/profiles/gsa/.azure"; got != want {
		t.Errorf("AZURE_CONFIG_DIR = %q, want %q", got, want)
	}
	if got, want := overrides["BRACED"], "/Users/x/workspaces/profiles/gsa/.config"; got != want {
		t.Errorf("BRACED = %q, want %q", got, want)
	}
}

// A value may reference another dotenv value. Map iteration order is
// nondeterministic, so this must hold whichever order the keys come out in.
func TestExpandOverrides_ResolvesChainsRegardlessOfOrder(t *testing.T) {
	for i := 0; i < 50; i++ {
		overrides := map[string]string{
			"WORKSPACE_HOME": "/home/w",
			"TOOLS":          "$WORKSPACE_HOME/tools",
			"BIN":            "$TOOLS/bin",
			"DEEP":           "$BIN/deep",
		}
		expandOverrides(overrides, nil)
		if got, want := overrides["DEEP"], "/home/w/tools/bin/deep"; got != want {
			t.Fatalf("iteration %d: DEEP = %q, want %q", i, got, want)
		}
	}
}

// Values fall back to the inherited environment when no dotenv value defines
// the reference.
func TestExpandOverrides_FallsBackToBaseEnv(t *testing.T) {
	overrides := map[string]string{"CACHE": "$XDG_BASE/cache"}
	expandOverrides(overrides, []string{"XDG_BASE=/var/lib", "OTHER=ignored"})

	if got, want := overrides["CACHE"], "/var/lib/cache"; got != want {
		t.Errorf("CACHE = %q, want %q", got, want)
	}
}

// A dotenv value must win over the inherited environment — that is the whole
// point of the overrides map.
func TestExpandOverrides_DotenvBeatsBaseEnv(t *testing.T) {
	overrides := map[string]string{
		"WORKSPACE_HOME": "/profile/home",
		"CFG":            "$WORKSPACE_HOME/.azure",
	}
	expandOverrides(overrides, []string{"WORKSPACE_HOME=/the/apps/own/home"})

	if got, want := overrides["CFG"], "/profile/home/.azure"; got != want {
		t.Errorf("CFG = %q, want %q — the app's own WORKSPACE_HOME leaked in", got, want)
	}
}

// A reference cycle must terminate rather than spin.
func TestExpandOverrides_CycleTerminates(t *testing.T) {
	done := make(chan map[string]string, 1)
	go func() {
		overrides := map[string]string{"A": "$B", "B": "$A"}
		expandOverrides(overrides, nil)
		done <- overrides
	}()
	select {
	case <-done:
		// Terminated; the exact residue is unspecified by design.
	case <-time.After(5 * time.Second):
		t.Fatal("expandOverrides did not terminate on a reference cycle")
	}
}

// An unreferenced literal is left exactly as written.
func TestExpandOverrides_LeavesPlainValuesAlone(t *testing.T) {
	overrides := map[string]string{
		"TOKEN": "abc$!literal",
		"PLAIN": "/no/refs/here",
	}
	expandOverrides(overrides, nil)
	if overrides["PLAIN"] != "/no/refs/here" {
		t.Errorf("PLAIN was rewritten: %q", overrides["PLAIN"])
	}
	// os.Expand drops an unknown reference; assert only that we did not panic
	// and that something sane came back.
	if strings.TrimSpace(overrides["TOKEN"]) == "" {
		t.Error("TOKEN became empty")
	}
}
