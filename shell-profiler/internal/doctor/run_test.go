package doctor

import (
	"path/filepath"
	"testing"
)

func TestRunChecks_Aggregates(t *testing.T) {
	dir := fullProfile(t, "A=1\n")
	p := load(t, dir, newStub(t, map[string]stubResponse{
		"direnv status": {out: "Found RC allowed true"},
	}))

	report := RunChecks(p, DefaultChecks())

	if report.Profile != "demo" || report.Path != dir {
		t.Errorf("report identity = %q / %q", report.Profile, report.Path)
	}
	if len(report.Results) != len(DefaultChecks()) {
		t.Errorf("got %d results, want %d", len(report.Results), len(DefaultChecks()))
	}
	okCount, failCount, skipCount := report.Counts()
	if okCount+failCount+skipCount != len(report.Results) {
		t.Errorf("counts do not sum: %d/%d/%d of %d", okCount, failCount, skipCount, len(report.Results))
	}
	for _, res := range report.Results {
		if res.Name == "" || res.Category == "" {
			t.Errorf("result missing name/category: %+v", res)
		}
		if res.Status == StatusFail && res.Fix == "" {
			t.Errorf("failing check %q has no fix hint", res.Name)
		}
	}
}

// Only failures set a non-zero exit; skips never do.
func TestReport_FailedIgnoresSkips(t *testing.T) {
	skipsOnly := Report{Profiles: []ProfileReport{{Results: []Result{
		{Status: StatusSkip}, {Status: StatusOK}, {Status: StatusSkip},
	}}}}
	if skipsOnly.Failed() {
		t.Error("a report of skips and passes must not be a failure")
	}
	if skipsOnly.FailCount() != 0 {
		t.Errorf("FailCount() = %d, want 0", skipsOnly.FailCount())
	}

	withFail := Report{Profiles: []ProfileReport{
		{Results: []Result{{Status: StatusSkip}}},
		{Results: []Result{{Status: StatusFail}, {Status: StatusFail}}},
	}}
	if !withFail.Failed() {
		t.Error("expected Failed() to be true")
	}
	if withFail.FailCount() != 2 {
		t.Errorf("FailCount() = %d, want 2", withFail.FailCount())
	}
}

func TestListProfileNames(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "beta"), ".envrc", "")
	writeFile(t, filepath.Join(root, "alpha"), ".env", "A=1")
	writeFile(t, filepath.Join(root, ".git"), ".envrc", "")
	writeFile(t, root, "loose-file", "")
	writeFile(t, filepath.Join(root, "not-a-profile"), "README.md", "")

	names, err := ListProfileNames(root)
	if err != nil {
		t.Fatalf("ListProfileNames() error: %v", err)
	}
	want := []string{"alpha", "beta"}
	if len(names) != len(want) {
		t.Fatalf("got %v, want %v", names, want)
	}
	for i := range want {
		if names[i] != want[i] {
			t.Fatalf("got %v, want %v (sorted)", names, want)
		}
	}
}

func TestCurrentProfileName(t *testing.T) {
	cases := map[string]string{
		"/home/dev/workspaces/profiles/work/": "work",
		"/home/dev/workspaces/profiles/work":  "work",
		"":                                    "",
	}
	for in, want := range cases {
		if got := CurrentProfileName(in); got != want {
			t.Errorf("CurrentProfileName(%q) = %q, want %q", in, got, want)
		}
	}
}
