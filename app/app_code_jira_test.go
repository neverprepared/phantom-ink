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
	decorateJira(&out, nil, nil, errJiraUnavailable)
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

// --- derived + manual merge -------------------------------------------------

func TestMergeLinksMarksManualSeparately(t *testing.T) {
	derived := map[string][]string{"github:org/repo#412": {"ABC-123"}}
	manual := map[string][]string{
		"github:org/repo#412": {"DEF-9"}, // extra ticket on a PR that already derived one
		"github:org/repo#500": {"GHI-1"}, // a PR whose title names nothing
	}
	got := mergeLinks(derived, manual)

	r412 := got["github:org/repo#412"]
	if len(r412) != 2 {
		t.Fatalf("row 412 links = %v, want 2", r412)
	}
	if r412[0].Key != "ABC-123" || r412[0].Manual {
		t.Fatalf("derived link mismarked: %+v", r412[0])
	}
	if r412[1].Key != "DEF-9" || !r412[1].Manual {
		t.Fatalf("manual link mismarked: %+v", r412[1])
	}

	r500 := got["github:org/repo#500"]
	if len(r500) != 1 || !r500[0].Manual {
		t.Fatalf("row 500 links = %v, want one manual", r500)
	}
}

func TestMergeLinksDoesNotDuplicateAManuallyConfirmedDerivedKey(t *testing.T) {
	// Linking by hand something the title already names must not double it.
	derived := map[string][]string{"github:org/repo#412": {"ABC-123"}}
	manual := map[string][]string{"github:org/repo#412": {"ABC-123"}}
	got := mergeLinks(derived, manual)["github:org/repo#412"]
	if len(got) != 1 {
		t.Fatalf("links = %v, want 1", got)
	}
	if got[0].Manual {
		t.Fatal("a key present in the title should stay derived — it cannot be unlinked")
	}
}

func TestAllKeysUnionsDerivedAndManual(t *testing.T) {
	derived := map[string][]string{"github:org/repo#412": {"ABC-123"}}
	manual := map[string][]string{"github:org/repo#500": {"GHI-1"}, "github:org/repo#412": {"ABC-123"}}
	keys := allLinkedKeys(mergeLinks(derived, manual))
	if len(keys) != 2 {
		t.Fatalf("keys = %v, want ABC-123 and GHI-1 exactly once each", keys)
	}
}
