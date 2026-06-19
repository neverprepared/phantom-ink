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

// GetLoopTemplate returns the raw YAML and metadata for one template.
// Drives the Templates tab editor (display today, write in PR 5).
func (a *App) GetLoopTemplate(name string) (brainbox.LoopTemplate, error) {
	return a.client.GetLoopTemplate(name)
}

// ValidateLoopTemplate runs schema validation on raw YAML without saving.
func (a *App) ValidateLoopTemplate(rawYAML string) (brainbox.LoopTemplateValidation, error) {
	return a.client.ValidateLoopTemplate(rawYAML)
}

// DryRunLoopTemplate plans iteration 1 against a sample envelope.
func (a *App) DryRunLoopTemplate(name string, envelope map[string]interface{}) (map[string]interface{}, error) {
	return a.client.DryRunLoopTemplate(name, envelope)
}

// PutLoopTemplate writes raw YAML to the user dir. fork=true creates a
// user override of a built-in.
func (a *App) PutLoopTemplate(name, rawYAML string, fork bool) (brainbox.LoopTemplate, error) {
	return a.client.PutLoopTemplate(name, rawYAML, fork)
}

// DeleteLoopTemplate removes a user template. 403 on built-ins.
func (a *App) DeleteLoopTemplate(name string) error {
	return a.client.DeleteLoopTemplate(name)
}

// GetLoopTemplateSchema returns the LoopSpec JSON Schema for editor
// Intellisense (PR 5 + 6).
func (a *App) GetLoopTemplateSchema() (map[string]interface{}, error) {
	return a.client.GetLoopTemplateSchema()
}

// AssistLoopTemplate runs an AI Assist call (generate / refine / explain)
// against the brainbox-side claude-backed authoring helper.
func (a *App) AssistLoopTemplate(req brainbox.LoopAssistRequest) (brainbox.LoopAssistResult, error) {
	return a.client.AssistLoopTemplate(req)
}

// StartLiveLoop fires a Loop by template name with initial artifact_refs.
// Used by the "Run a loop" UI (and by future drill-in retry buttons).
func (a *App) StartLiveLoop(templateName string, artifactRefs map[string]interface{}) (brainbox.LiveLoop, error) {
	return a.client.StartLiveLoop(templateName, artifactRefs)
}
