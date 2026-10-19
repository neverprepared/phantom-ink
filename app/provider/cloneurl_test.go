package provider

import (
	"testing"
	"time"
)

// These bodies existed four times byte-for-byte (provider/ado,
// provider/github, and twice in app_code.go, whose comment admitted the
// mirror while nothing enforced it). Now there is one definition — and one
// place to pin its behaviour, so a future edit cannot silently disagree with
// itself across packages.

func TestNormalizeGitHubCloneURL(t *testing.T) {
	cases := []struct{ in, want string }{
		{"git@github.com:o/r.git", "https://github.com/o/r.git"},
		{"ssh://git@github.com/o/r", "https://github.com/o/r.git"},
		{"https://github.com/o/r", "https://github.com/o/r.git"},
		{"https://github.com/o/r.git", "https://github.com/o/r.git"},
		{"https://github.com/o/r/", "https://github.com/o/r.git"},
		{"https://api.github.com/repos/o/r", "https://github.com/o/r.git"},
		// Enterprise host must be preserved, not rewritten to github.com.
		{"https://ghe.corp.example/o/r", "https://ghe.corp.example/o/r.git"},
		{"git@ghe.corp.example:o/r.git", "https://ghe.corp.example/o/r.git"},
		// Unusable input yields "", never a half-built URL.
		{"", ""},
		{"github.com", ""},
		{"https://github.com/o", ""},
		{"   ", ""},
	}
	for _, c := range cases {
		if got := NormalizeGitHubCloneURL(c.in); got != c.want {
			t.Errorf("NormalizeGitHubCloneURL(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestNormalizeADOCloneURL(t *testing.T) {
	cases := []struct{ in, want string }{
		{"https://dev.azure.com/o/p/_git/r", "https://dev.azure.com/o/p/_git/r"},
		{"https://user@dev.azure.com/o/p/_git/r", "https://dev.azure.com/o/p/_git/r"},
		{"https://o.visualstudio.com/p/_git/r", "https://dev.azure.com/o/p/_git/r"},
		{"https://dev.azure.com/o/p/_git/r/", "https://dev.azure.com/o/p/_git/r"},
		// Project names with spaces are routine in this org's ADO.
		{"https://dev.azure.com/o/MY PROJECT/_git/r", "https://dev.azure.com/o/MY PROJECT/_git/r"},
		// Not a full org/project/_git/repo.
		{"https://dev.azure.com/o/p/r", ""},
		{"https://dev.azure.com/o", ""},
		{"https://dev.azure.com", ""},
		{"https://github.com/o/r", ""},
		{"", ""},
	}
	for _, c := range cases {
		if got := NormalizeADOCloneURL(c.in); got != c.want {
			t.Errorf("NormalizeADOCloneURL(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestIsADOCloneHost(t *testing.T) {
	ado := []string{
		"https://dev.azure.com/o/p/_git/r",
		"https://user@dev.azure.com/o/p/_git/r",
		"https://o.visualstudio.com/p/_git/r",
		"dev.azure.com/o/p/_git/r",
	}
	for _, in := range ado {
		if !IsADOCloneHost(in) {
			t.Errorf("IsADOCloneHost(%q) = false, want true", in)
		}
	}
	notADO := []string{
		"https://github.com/o/r",
		"git@github.com:o/r.git",
		"https://ghe.corp.example/o/r",
		"",
		// A lookalike that is not the ADO host.
		"https://notdev.azure.com.evil.example/o/p/_git/r",
	}
	for _, in := range notADO {
		if IsADOCloneHost(in) {
			t.Errorf("IsADOCloneHost(%q) = true, want false", in)
		}
	}
}

// The router must send ADO through the ADO rules and everything else through
// the GitHub rules.
func TestNormalizeCloneURLRoutesByHost(t *testing.T) {
	cases := []struct{ in, want string }{
		{"https://dev.azure.com/o/p/_git/r", "https://dev.azure.com/o/p/_git/r"},
		{"https://o.visualstudio.com/p/_git/r", "https://dev.azure.com/o/p/_git/r"},
		{"git@github.com:o/r.git", "https://github.com/o/r.git"},
		{"https://api.github.com/repos/o/r", "https://github.com/o/r.git"},
		{"", ""},
	}
	for _, c := range cases {
		if got := NormalizeCloneURL(c.in); got != c.want {
			t.Errorf("NormalizeCloneURL(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestIsAtOrAfter(t *testing.T) {
	cutoff := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)

	if !IsAtOrAfter("2026-06-02T00:00:00Z", cutoff) {
		t.Error("a later timestamp must be included")
	}
	if !IsAtOrAfter("2026-06-01T00:00:00Z", cutoff) {
		t.Error("the cutoff itself must be included (at-or-after)")
	}
	if IsAtOrAfter("2026-05-31T23:59:59Z", cutoff) {
		t.Error("an earlier timestamp must be excluded")
	}
	// An unparseable date is INCLUDED on purpose: dropping it would silently
	// understate the window, which is the worse failure for these metrics.
	if !IsAtOrAfter("not a timestamp", cutoff) {
		t.Error("an unparseable timestamp must be included, not dropped")
	}
	// A zero cutoff means "no lower bound".
	if !IsAtOrAfter("1999-01-01T00:00:00Z", time.Time{}) {
		t.Error("a zero cutoff must include everything")
	}
}
