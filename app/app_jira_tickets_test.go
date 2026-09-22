package main

import (
	"testing"

	"phantom-ink/jira"
	"phantom-ink/provider"
)

func TestDefaultJQLWhenUnset(t *testing.T) {
	a := newJiraTestApp(t)
	if got := a.GetJiraJQL("work"); got != defaultJiraJQL {
		t.Fatalf("GetJiraJQL = %q, want the default", got)
	}
}

func TestJQLIsPerProfile(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.SetJiraJQL("work", "project = ABC"); err != nil {
		t.Fatalf("SetJiraJQL: %v", err)
	}
	if got := a.GetJiraJQL("work"); got != "project = ABC" {
		t.Fatalf("work jql = %q", got)
	}
	if got := a.GetJiraJQL("personal"); got != defaultJiraJQL {
		t.Fatalf("personal jql leaked work's: %q", got)
	}
}

func TestSetJiraJQLRejectsEmpty(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.SetJiraJQL("work", "   "); err == nil {
		t.Fatal("empty JQL accepted; Jira reads it as the whole instance")
	}
}

func TestBuildTicketsAttachesDerivedAndManualRefs(t *testing.T) {
	prs := []provider.Item{
		{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 412, Title: "ABC-123: add webhook", HTMLURL: "u412"},
		{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 500, Title: "no key here", HTMLURL: "u500"},
	}
	issues := []jira.Issue{{Key: "ABC-123"}, {Key: "ABC-130"}, {Key: "GHI-1"}}
	manual := map[string][]string{
		"GHI-1": {"github:org/repo#500"},
		// a link whose PR is not in the current page — an orphan
		"ABC-130": {"github:org/repo#999"},
	}

	got := buildTickets(issues, prs, manual)

	if len(got) != 3 {
		t.Fatalf("tickets = %d, want 3", len(got))
	}
	byKey := map[string]JiraTicket{}
	for _, tk := range got {
		byKey[tk.Key] = tk
	}

	// derived from the title
	abc := byKey["ABC-123"]
	if len(abc.Refs) != 1 || abc.Refs[0].Manual || abc.Refs[0].Number != 412 {
		t.Fatalf("ABC-123 refs = %+v", abc.Refs)
	}
	// manual override on a PR whose title names nothing
	ghi := byKey["GHI-1"]
	if len(ghi.Refs) != 1 || !ghi.Refs[0].Manual || ghi.Refs[0].Number != 500 {
		t.Fatalf("GHI-1 refs = %+v", ghi.Refs)
	}
	// orphan: the link exists but its PR is not on this page. It must be
	// reported, not silently dropped — this is where you delete a stale link.
	orphan := byKey["ABC-130"]
	if len(orphan.Refs) != 0 {
		t.Fatalf("ABC-130 refs = %+v, want none renderable", orphan.Refs)
	}
	if len(orphan.OrphanRows) != 1 || orphan.OrphanRows[0] != "github:org/repo#999" {
		t.Fatalf("ABC-130 orphans = %v", orphan.OrphanRows)
	}
}

func TestBuildTicketsPreservesJiraOrder(t *testing.T) {
	// The JQL's ORDER BY is the operator's stated priority; a map would lose it.
	issues := []jira.Issue{{Key: "C-3"}, {Key: "A-1"}, {Key: "B-2"}}
	got := buildTickets(issues, nil, nil)
	for i, want := range []string{"C-3", "A-1", "B-2"} {
		if got[i].Key != want {
			t.Fatalf("position %d = %q, want %q", i, got[i].Key, want)
		}
	}
}
