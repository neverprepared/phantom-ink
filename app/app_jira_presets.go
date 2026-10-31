package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

// settingJiraPresetsPrefix + <profile> holds that profile's saved preset list
// as a JSON array. Per profile because JQL names projects, and projects belong
// to a site — two profiles can point at two different Atlassian instances.
const settingJiraPresetsPrefix = "jira_jql_presets:"

// JiraJQLPreset is one named query the operator can switch to with a click.
type JiraJQLPreset struct {
	Name string `json:"name"`
	JQL  string `json:"jql"`
}

// defaultJiraJQLPresets is what a profile sees before it has saved a list of
// its own. The first entry is deliberately the default query, so the chip row
// has an active member from the very first look.
//
// These are starting points, not policy: the operator edits the list and their
// version is stored, seeds and all.
func defaultJiraJQLPresets() []JiraJQLPreset {
	return []JiraJQLPreset{
		{Name: "my open work", JQL: defaultJiraJQL},
		{Name: "in progress", JQL: "assignee = currentUser() AND statusCategory = \"In Progress\" ORDER BY updated DESC"},
		{Name: "reported by me", JQL: "reporter = currentUser() AND resolution = Unresolved ORDER BY created DESC"},
		{Name: "updated this week", JQL: "assignee = currentUser() AND updated >= -7d ORDER BY updated DESC"},
	}
}

// GetJiraJQLPresets returns this profile's preset list.
//
// An UNSET setting means "never customised" and yields the seeds; an
// explicitly stored empty array means the operator deleted them all and is
// honoured as empty. A value that will not parse is treated as unset rather
// than as an empty list, so a mangled row cannot leave the tab with no chips
// and no way to get them back.
func (a *App) GetJiraJQLPresets(profile string) []JiraJQLPreset {
	if a.db == nil {
		return defaultJiraJQLPresets()
	}
	raw := strings.TrimSpace(a.db.GetSetting(settingJiraPresetsPrefix+profile, ""))
	if raw == "" {
		return defaultJiraJQLPresets()
	}
	var out []JiraJQLPreset
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		return defaultJiraJQLPresets()
	}
	if out == nil {
		// Stored "null" — same shape as unset.
		return defaultJiraJQLPresets()
	}
	return out
}

// normalizeJiraJQLPresets trims every field and rejects the three ways a list
// can be unusable: a nameless chip, a preset that would send Jira an empty
// query (which Jira reads as the whole instance), and two presets whose names
// a human cannot tell apart.
func normalizeJiraJQLPresets(presets []JiraJQLPreset) ([]JiraJQLPreset, error) {
	out := make([]JiraJQLPreset, 0, len(presets))
	seen := make(map[string]bool, len(presets))
	for i, p := range presets {
		name := strings.TrimSpace(p.Name)
		jql := strings.TrimSpace(p.JQL)
		if name == "" {
			return nil, fmt.Errorf("preset %d: name required", i+1)
		}
		if jql == "" {
			return nil, fmt.Errorf("preset %q: jql required", name)
		}
		key := strings.ToLower(name)
		if seen[key] {
			return nil, fmt.Errorf("duplicate preset name %q", name)
		}
		seen[key] = true
		out = append(out, JiraJQLPreset{Name: name, JQL: jql})
	}
	return out, nil
}

// SetJiraJQLPresets replaces this profile's whole list. The editor sends the
// list it has, so a rename, an edit, a delete and a reorder are all this one
// call.
//
// Saving presets does NOT change which query is active, so no ticket memo is
// dropped here — only ApplyJiraJQLPreset does that.
func (a *App) SetJiraJQLPresets(profile string, presets []JiraJQLPreset) error {
	if a.db == nil {
		return errNoDB
	}
	if strings.TrimSpace(profile) == "" {
		return errors.New("profile required")
	}
	clean, err := normalizeJiraJQLPresets(presets)
	if err != nil {
		return err
	}
	// Marshal the slice literal rather than `clean` when empty: an empty list
	// must store "[]" (customised, no presets), never "null" (reads as unset).
	if clean == nil {
		clean = []JiraJQLPreset{}
	}
	blob, err := json.Marshal(clean)
	if err != nil {
		return err
	}
	return a.db.SetSetting(settingJiraPresetsPrefix+profile, string(blob))
}

// ApplyJiraJQLPreset makes one preset this profile's active query.
//
// It delegates to SetJiraJQL rather than writing the setting itself, so the
// ticket-memo invalidation that every query change needs lives in exactly one
// place. Names match case-insensitively — the chip the operator clicked and
// the name they typed are the same query.
func (a *App) ApplyJiraJQLPreset(profile, name string) error {
	want := strings.ToLower(strings.TrimSpace(name))
	if want == "" {
		return errors.New("preset name required")
	}
	for _, p := range a.GetJiraJQLPresets(profile) {
		if strings.ToLower(strings.TrimSpace(p.Name)) == want {
			return a.SetJiraJQL(profile, p.JQL)
		}
	}
	return fmt.Errorf("no preset named %q", name)
}
