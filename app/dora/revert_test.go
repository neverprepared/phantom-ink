package dora

import "testing"

func TestParseRevertSubject(t *testing.T) {
	cases := []struct {
		msg    string
		want   string
		wantOK bool
	}{
		{"Revert \"Add thing\"", "Add thing", true},
		{"Revert \"Add thing\"\n\nThis reverts commit abc123.", "Add thing", true},
		{"Revert \"Add thing (#12)\"", "Add thing (#12)", true},
		{"Add thing", "", false},
		{"Reverting the thing", "", false},
		// A revert of a revert: the outer subject is itself a quoted revert,
		// and what it reverted is that inner string verbatim.
		{"Revert \"Revert \\\"Add thing\\\"\"", "Revert \\\"Add thing\\\"", true},
		// No closing quote is malformed, not a subject of "Add thing.
		{"Revert \"Add thing", "", false},
		{"Revert \"\"", "", false},
		{"", "", false},
		// Leading/trailing whitespace on the line is incidental.
		{"  Revert \"Add thing\"  ", "Add thing", true},
	}
	for _, c := range cases {
		got, ok := ParseRevertSubject(c.msg)
		if ok != c.wantOK || got != c.want {
			t.Errorf("ParseRevertSubject(%q) = (%q,%v) want (%q,%v)", c.msg, got, ok, c.want, c.wantOK)
		}
	}
}

func TestMatchRevertPrefersPRNumberInSubject(t *testing.T) {
	deploys := []DeployEvent{
		{PRNumber: 12, Title: "Add thing"},
		{PRNumber: 99, Title: "Add thing"},
	}
	if got, ok := MatchRevert("Add thing (#99)", deploys); !ok || got != 99 {
		t.Fatalf("an explicit (#99) must win over title ambiguity: got %d ok=%v", got, ok)
	}
}

func TestMatchRevertFallsBackToTitle(t *testing.T) {
	deploys := []DeployEvent{{PRNumber: 7, Title: "Fix the bug"}}
	if got, ok := MatchRevert("Fix the bug", deploys); !ok || got != 7 {
		t.Fatalf("title match: got %d ok=%v", got, ok)
	}
	if _, ok := MatchRevert("Unrelated", deploys); ok {
		t.Fatal("no match must report ok=false, not a bogus PR number")
	}
}

// A squash-merge title carries its own (#N); the revert subject repeats it. The
// comparison has to trim the suffix from BOTH sides or nothing ever matches.
func TestMatchRevertTrimsPRSuffixFromTheTitleToo(t *testing.T) {
	deploys := []DeployEvent{{PRNumber: 41, Title: "Fix the bug (#41)"}}
	if got, ok := MatchRevert("Fix the bug", deploys); !ok || got != 41 {
		t.Fatalf("subject without a suffix must match a title that has one: got %d ok=%v", got, ok)
	}
	if got, ok := MatchRevert("Fix the bug (#41)", deploys); !ok || got != 41 {
		t.Fatalf("suffix on both sides must still match: got %d ok=%v", got, ok)
	}
}

// An explicit (#N) that names no known deploy must NOT silently fall through to
// a title match on a different PR — that would attribute a failure to the wrong
// change. Reporting the number is still right: the deploy may predate the
// stored window.
func TestMatchRevertWithUnknownPRNumberDoesNotMisattribute(t *testing.T) {
	deploys := []DeployEvent{{PRNumber: 7, Title: "Fix the bug"}}
	got, ok := MatchRevert("Fix the bug (#404)", deploys)
	if ok {
		t.Fatalf("an unknown explicit number must not match a same-titled other PR: got %d", got)
	}
}

func TestMatchRevertWithNoDeploysIsUnmatched(t *testing.T) {
	if _, ok := MatchRevert("anything", nil); ok {
		t.Fatal("no deploys to match against must report ok=false")
	}
	if _, ok := MatchRevert("", []DeployEvent{{PRNumber: 1, Title: ""}}); ok {
		t.Fatal("an empty subject must not match an empty title — that is not evidence")
	}
}
