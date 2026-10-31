package main

import "testing"

func TestJQLPresetsSeededWhenUnset(t *testing.T) {
	a := newJiraTestApp(t)
	got := a.GetJiraJQLPresets("work")
	if len(got) == 0 {
		t.Fatal("no presets for a fresh profile; want the seeded set")
	}
	// The first seeded preset is the default query, so the chip row has an
	// active member before the operator has saved anything.
	if got[0].JQL != defaultJiraJQL {
		t.Fatalf("first seeded preset jql = %q, want the default query", got[0].JQL)
	}
	for _, p := range got {
		if p.Name == "" || p.JQL == "" {
			t.Fatalf("seeded preset is incomplete: %+v", p)
		}
	}
}

func TestJQLPresetsArePerProfile(t *testing.T) {
	a := newJiraTestApp(t)
	mine := []JiraJQLPreset{{Name: "escalations", JQL: "labels = escalation"}}
	if err := a.SetJiraJQLPresets("work", mine); err != nil {
		t.Fatalf("SetJiraJQLPresets: %v", err)
	}
	if got := a.GetJiraJQLPresets("work"); len(got) != 1 || got[0].Name != "escalations" {
		t.Fatalf("work presets = %+v", got)
	}
	// personal must still see the seeds, not work's list.
	personal := a.GetJiraJQLPresets("personal")
	for _, p := range personal {
		if p.Name == "escalations" {
			t.Fatal("work's presets leaked into personal")
		}
	}
}

func TestSetJiraJQLPresetsValidates(t *testing.T) {
	a := newJiraTestApp(t)
	cases := map[string][]JiraJQLPreset{
		"blank name":     {{Name: "  ", JQL: "project = ABC"}},
		"blank jql":      {{Name: "mine", JQL: "   "}},
		"duplicate name": {{Name: "mine", JQL: "project = A"}, {Name: "Mine", JQL: "project = B"}},
	}
	for label, presets := range cases {
		if err := a.SetJiraJQLPresets("work", presets); err == nil {
			t.Fatalf("%s accepted", label)
		}
	}
}

func TestSetJiraJQLPresetsTrimsAndAllowsEmptyList(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.SetJiraJQLPresets("work", []JiraJQLPreset{{Name: " mine ", JQL: " project = ABC "}}); err != nil {
		t.Fatalf("SetJiraJQLPresets: %v", err)
	}
	got := a.GetJiraJQLPresets("work")
	if len(got) != 1 || got[0].Name != "mine" || got[0].JQL != "project = ABC" {
		t.Fatalf("presets not trimmed: %+v", got)
	}
	// Clearing the list is a real choice — it must persist as empty rather
	// than falling back to the seeds, or deleting every preset is impossible.
	if err := a.SetJiraJQLPresets("work", nil); err != nil {
		t.Fatalf("clear presets: %v", err)
	}
	if got := a.GetJiraJQLPresets("work"); len(got) != 0 {
		t.Fatalf("cleared presets came back as %+v", got)
	}
}

func TestApplyJiraJQLPresetSetsActiveQuery(t *testing.T) {
	a := newJiraTestApp(t)
	presets := []JiraJQLPreset{
		{Name: "mine", JQL: "assignee = currentUser()"},
		{Name: "reported", JQL: "reporter = currentUser()"},
	}
	if err := a.SetJiraJQLPresets("work", presets); err != nil {
		t.Fatalf("SetJiraJQLPresets: %v", err)
	}
	if err := a.ApplyJiraJQLPreset("work", "reported"); err != nil {
		t.Fatalf("ApplyJiraJQLPreset: %v", err)
	}
	if got := a.GetJiraJQL("work"); got != "reporter = currentUser()" {
		t.Fatalf("active jql = %q, want the preset's", got)
	}
	if got := a.GetJiraJQL("personal"); got != defaultJiraJQL {
		t.Fatalf("applying work's preset changed personal: %q", got)
	}
}

func TestApplyJiraJQLPresetMatchesNameCaseInsensitively(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.SetJiraJQLPresets("work", []JiraJQLPreset{{Name: "In Progress", JQL: "status = 'In Progress'"}}); err != nil {
		t.Fatalf("SetJiraJQLPresets: %v", err)
	}
	if err := a.ApplyJiraJQLPreset("work", "in progress"); err != nil {
		t.Fatalf("ApplyJiraJQLPreset: %v", err)
	}
	if got := a.GetJiraJQL("work"); got != "status = 'In Progress'" {
		t.Fatalf("active jql = %q", got)
	}
}

func TestApplyJiraJQLPresetUnknownName(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.ApplyJiraJQLPreset("work", "nope"); err == nil {
		t.Fatal("unknown preset name accepted")
	}
	// The active query must be untouched by a failed apply.
	if got := a.GetJiraJQL("work"); got != defaultJiraJQL {
		t.Fatalf("failed apply changed the active query: %q", got)
	}
}

func TestApplyJiraJQLPresetDropsTicketMemo(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.SetJiraJQLPresets("work", []JiraJQLPreset{{Name: "mine", JQL: "assignee = currentUser()"}}); err != nil {
		t.Fatalf("SetJiraJQLPresets: %v", err)
	}
	// Seed the memo with results that belong to the OLD query.
	storeJiraTickets("work", JiraTicketsResult{Profile: "work", JQL: defaultJiraJQL})
	if _, _, ok := jiraTicketsMemo.get("work"); !ok {
		t.Fatal("memo not seeded")
	}
	t.Cleanup(func() { jiraTicketsMemo.invalidate("work") })

	if err := a.ApplyJiraJQLPreset("work", "mine"); err != nil {
		t.Fatalf("ApplyJiraJQLPreset: %v", err)
	}
	if _, _, ok := jiraTicketsMemo.get("work"); ok {
		t.Fatal("tickets from the previous query survived the preset switch")
	}
}

func TestGetJiraJQLPresetsIgnoresCorruptStoredValue(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.db.SetSetting(settingJiraPresetsPrefix+"work", "{not json"); err != nil {
		t.Fatalf("SetSetting: %v", err)
	}
	// A hand-mangled value must not blank the chip row.
	if got := a.GetJiraJQLPresets("work"); len(got) == 0 {
		t.Fatal("corrupt stored presets produced an empty list, want the seeds")
	}
}
