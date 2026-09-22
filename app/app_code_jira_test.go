package main

import (
	"testing"

	"phantom-ink/provider"
)

func TestRowKeyIsStable(t *testing.T) {
	it := provider.Item{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 412}
	if got, want := rowKey(it), "github:org/repo#412"; got != want {
		t.Fatalf("rowKey = %q, want %q", got, want)
	}
}

func TestCollectIssueKeysFromTitles(t *testing.T) {
	items := []provider.Item{
		{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 412, Title: "ABC-123: add webhook"},
		{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 408, Title: "bump deps"},
		{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 400, Title: "ABC-123 and DEF-9"},
	}
	byRow, all := collectIssueKeys(items)
	if got := byRow["github:org/repo#412"]; len(got) != 1 || got[0] != "ABC-123" {
		t.Fatalf("row 412 keys = %v", got)
	}
	if _, ok := byRow["github:org/repo#408"]; ok {
		t.Fatal("row with no key should not appear in the map")
	}
	if len(all) != 2 {
		t.Fatalf("deduped key set = %v, want ABC-123 and DEF-9", all)
	}
}

func TestJiraFailureDoesNotBlankGitRows(t *testing.T) {
	out := CodeOverview{
		Profile:      "work",
		PullRequests: []provider.Item{{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 1, Title: "ABC-1: x"}},
	}
	decorateJira(&out, nil, errJiraUnavailable)
	if len(out.PullRequests) != 1 {
		t.Fatal("git rows were blanked by a Jira failure")
	}
	if out.JiraError == "" {
		t.Fatal("JiraError not set")
	}
	if len(out.JiraIssues) != 0 {
		t.Fatal("issues populated despite failure")
	}
}
