package provider

import (
	"strings"
	"time"
)

// Clone-URL normalization, shared by the provider clients and by the
// main-package local-clone lane.
//
// These bodies previously existed FOUR times, byte for byte: once in
// provider/ado, once in provider/github, and twice more in app_code.go — whose
// comment admitted the duplication ("mirrors provider/ado.Client
// .NormalizeCloneURL so the main-package free function ... agrees with the ado
// provider client") while nothing enforced it. Two definitions of "what counts
// as a valid clone URL" that must agree, kept in step by hand, is a bug
// waiting for one of them to be edited.
//
// Both provider leaves already import this package, so there is no cycle.

// NormalizeADOCloneURL turns any Azure DevOps reference into an https _git
// clone URL, handling dev.azure.com (with or without a user@ prefix) and
// legacy {org}.visualstudio.com. Returns "" when it cannot parse a full
// {org}/{project}/_git/{repo}.
func NormalizeADOCloneURL(repoURL string) string {
	s := strings.TrimSpace(repoURL)
	s = strings.TrimSuffix(s, "/")
	if s == "" {
		return ""
	}
	if i := strings.Index(s, "://"); i >= 0 {
		s = s[i+3:]
	}
	if at := strings.Index(s, "@"); at >= 0 {
		s = s[at+1:]
	}
	slash := strings.Index(s, "/")
	if slash < 0 {
		return "" // bare host, no path
	}
	host, rest := s[:slash], s[slash+1:]
	var org, tail string
	switch {
	case host == "dev.azure.com":
		i := strings.Index(rest, "/")
		if i < 0 {
			return ""
		}
		org, tail = rest[:i], rest[i+1:]
	case strings.HasSuffix(host, ".visualstudio.com"):
		org, tail = strings.TrimSuffix(host, ".visualstudio.com"), rest
	default:
		return ""
	}
	segs := strings.Split(tail, "/")
	if org == "" || len(segs) < 3 || segs[0] == "" || segs[1] != "_git" || segs[2] == "" {
		return ""
	}
	return "https://dev.azure.com/" + org + "/" + segs[0] + "/_git/" + segs[2]
}

// NormalizeGitHubCloneURL turns any GitHub reference into an https clone URL:
//
//	git@github.com:o/r.git              → https://github.com/o/r.git
//	ssh://git@github.com/o/r            → https://github.com/o/r.git
//	https://github.com/o/r              → https://github.com/o/r.git
//	https://api.github.com/repos/o/r    → https://github.com/o/r.git
//
// The host is PRESERVED (only api.github.com is rewritten to github.com) so a
// GitHub Enterprise remote still clones from its own host. Returns "" when no
// owner/repo pair can be read out.
func NormalizeGitHubCloneURL(repoURL string) string {
	s := strings.TrimSpace(repoURL)
	s = strings.TrimSuffix(s, "/")
	if s == "" {
		return ""
	}
	// Strip the scheme, then any user@ — leaves host/owner/repo for both
	// "https://github.com/..." and "ssh://git@github.com/...".
	if i := strings.Index(s, "://"); i >= 0 {
		s = s[i+3:]
	} else if at := strings.Index(s, "@"); at >= 0 {
		// scp-style ssh (git@github.com:owner/repo.git): the colon separating
		// host from path is a path separator here, not a port.
		s = strings.Replace(s[at+1:], ":", "/", 1)
	}
	if at := strings.Index(s, "@"); at >= 0 {
		s = s[at+1:]
	}

	segs := strings.Split(s, "/")
	if len(segs) < 3 {
		return ""
	}
	host, segs := segs[0], segs[1:]
	if host == "api.github.com" && segs[0] == "repos" {
		host, segs = "github.com", segs[1:]
	}
	if len(segs) < 2 {
		return ""
	}
	owner, repo := segs[0], strings.TrimSuffix(segs[1], ".git")
	if host == "" || owner == "" || repo == "" {
		return ""
	}
	return "https://" + host + "/" + owner + "/" + repo + ".git"
}

// IsADOCloneHost reports whether repoURL's host looks like Azure DevOps, so
// NormalizeCloneURL knows to route it through the ADO rules.
func IsADOCloneHost(repoURL string) bool {
	s := strings.TrimSpace(repoURL)
	if i := strings.Index(s, "://"); i >= 0 {
		s = s[i+3:]
	}
	if at := strings.Index(s, "@"); at >= 0 {
		s = s[at+1:]
	}
	host := s
	if slash := strings.Index(host, "/"); slash >= 0 {
		host = host[:slash]
	}
	return host == "dev.azure.com" || strings.HasSuffix(host, ".visualstudio.com")
}

// NormalizeCloneURL turns any GitHub or Azure DevOps reference into an https
// clone URL, so one helper serves a repo row's clone_url, a search hit's
// html_url, an api.github.com URL, and an ssh remote pasted by hand.
//
// ADO hosts are detected and normalized BEFORE the GitHub rules; GitHub URLs
// fall through. Returns "" when no owner/repo pair (or, for ADO, no
// org/project/_git/repo) can be read out.
func NormalizeCloneURL(repoURL string) string {
	if IsADOCloneHost(repoURL) {
		return NormalizeADOCloneURL(repoURL)
	}
	return NormalizeGitHubCloneURL(repoURL)
}

// IsAtOrAfter reports whether an RFC3339 timestamp is at or after cutoff.
// An unparseable timestamp is INCLUDED: dropping an event because its date
// could not be read would silently understate a window, which is the worse
// failure for the metrics that consume this.
//
// Previously defined verbatim in both provider leaves.
func IsAtOrAfter(ts string, cutoff time.Time) bool {
	if cutoff.IsZero() {
		return true
	}
	t, err := time.Parse(time.RFC3339, ts)
	if err != nil {
		return true
	}
	return !t.Before(cutoff)
}
