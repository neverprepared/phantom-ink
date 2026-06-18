package main

import (
	"phantom-ink/brainbox"
)

// ---------------------------------------------------------------------------
// Live loops — Wails surface for the brainbox runtime Loop API.
//
// "Live" loops are LoopInstance records driven by the loop runner in brainbox
// (started via /api/loops/start or the GitHub webhook). They are conceptually
// distinct from the legacy local-SQLite Loop definitions ListLoops() returns;
// the latter are chain-style sequences that the desktop app authors and
// stores locally. A1's Loop runner ships them as proper iteration loops.
// ---------------------------------------------------------------------------

// ListLiveLoops returns the slim view of every loop the brainbox runtime
// knows about. Empty status filter = no filter.
func (a *App) ListLiveLoops(status string) ([]brainbox.LiveLoopSummary, error) {
	return a.client.ListLiveLoops(status)
}

// GetLiveLoop returns the full LoopInstance with its pinned template snapshot.
func (a *App) GetLiveLoop(id string) (brainbox.LiveLoop, error) {
	return a.client.GetLiveLoop(id)
}

// GetLiveLoopIterations returns the per-iteration metric rows feeding the
// convergence trend chart.
func (a *App) GetLiveLoopIterations(id string) ([]brainbox.LiveLoopIteration, error) {
	return a.client.GetLiveLoopIterations(id)
}

// CancelLiveLoop terminates an in-flight loop with the given operator reason.
func (a *App) CancelLiveLoop(id, reason string) (brainbox.LiveLoop, error) {
	return a.client.CancelLiveLoop(id, reason)
}

// ListLoopTemplates returns names visible to brainbox.
func (a *App) ListLoopTemplates() ([]string, error) {
	return a.client.ListLoopTemplates()
}

// StartLiveLoop fires a Loop by template name with initial artifact_refs.
// Used by the "Run a loop" UI (and by future drill-in retry buttons).
func (a *App) StartLiveLoop(templateName string, artifactRefs map[string]interface{}) (brainbox.LiveLoop, error) {
	return a.client.StartLiveLoop(templateName, artifactRefs)
}
