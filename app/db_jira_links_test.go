package main

import "testing"

func TestJiraLinkRoundTrip(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.db.AddJiraLink("work", "ABC-123", "github:org/repo#412"); err != nil {
		t.Fatalf("AddJiraLink: %v", err)
	}
	links, err := a.db.JiraLinks("work")
	if err != nil {
		t.Fatalf("JiraLinks: %v", err)
	}
	if got := links["github:org/repo#412"]; len(got) != 1 || got[0] != "ABC-123" {
		t.Fatalf("links = %v", links)
	}
}

func TestJiraLinkIsIdempotent(t *testing.T) {
	a := newJiraTestApp(t)
	a.db.AddJiraLink("work", "ABC-123", "github:org/repo#412")
	if err := a.db.AddJiraLink("work", "ABC-123", "github:org/repo#412"); err != nil {
		t.Fatalf("second add: %v", err)
	}
	links, _ := a.db.JiraLinks("work")
	if len(links["github:org/repo#412"]) != 1 {
		t.Fatalf("duplicate stored: %v", links)
	}
}

func TestJiraLinkIsPerProfile(t *testing.T) {
	a := newJiraTestApp(t)
	a.db.AddJiraLink("work", "ABC-123", "github:org/repo#412")
	links, err := a.db.JiraLinks("personal")
	if err != nil {
		t.Fatalf("JiraLinks: %v", err)
	}
	if len(links) != 0 {
		t.Fatalf("another profile sees work's links: %v", links)
	}
}

func TestJiraLinkManyToMany(t *testing.T) {
	a := newJiraTestApp(t)
	// One PR covering two tickets, and one ticket spanning two PRs.
	a.db.AddJiraLink("work", "ABC-123", "github:org/repo#412")
	a.db.AddJiraLink("work", "DEF-9", "github:org/repo#412")
	a.db.AddJiraLink("work", "ABC-123", "github:org/repo#500")
	links, _ := a.db.JiraLinks("work")
	if len(links["github:org/repo#412"]) != 2 {
		t.Fatalf("PR 412 keys = %v", links["github:org/repo#412"])
	}
	if len(links["github:org/repo#500"]) != 1 {
		t.Fatalf("PR 500 keys = %v", links["github:org/repo#500"])
	}
}

func TestRemoveJiraLink(t *testing.T) {
	a := newJiraTestApp(t)
	a.db.AddJiraLink("work", "ABC-123", "github:org/repo#412")
	a.db.AddJiraLink("work", "DEF-9", "github:org/repo#412")
	if err := a.db.RemoveJiraLink("work", "ABC-123", "github:org/repo#412"); err != nil {
		t.Fatalf("RemoveJiraLink: %v", err)
	}
	links, _ := a.db.JiraLinks("work")
	got := links["github:org/repo#412"]
	if len(got) != 1 || got[0] != "DEF-9" {
		t.Fatalf("after remove, keys = %v", got)
	}
}

func TestJiraLinksByIssue(t *testing.T) {
	a := newJiraTestApp(t)
	a.db.AddJiraLink("work", "ABC-123", "github:org/repo#412")
	a.db.AddJiraLink("work", "ABC-123", "github:org/repo#500")
	byIssue, err := a.db.JiraLinksByIssue("work")
	if err != nil {
		t.Fatalf("JiraLinksByIssue: %v", err)
	}
	if len(byIssue["ABC-123"]) != 2 {
		t.Fatalf("ABC-123 rows = %v", byIssue["ABC-123"])
	}
}
