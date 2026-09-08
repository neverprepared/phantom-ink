package main

// Post-save validation for a curated GITHUB_TOKEN. Saving to the gateway env
// store delivers a token; it does not prove the token is valid. After a save
// the editor calls ValidateGitHubToken so an expired/typo'd token surfaces
// immediately, not three worker-runs later when a private clone 401s.

import (
	"context"
	"net/http"
	"time"
)

// githubAPIBase is the GitHub REST base; overridable in tests.
const githubAPIBase = "https://api.github.com"

// GitHubTokenStatus is the verdict returned to the UI.
type GitHubTokenStatus struct {
	// Valid is true only when GitHub positively accepted the token.
	Valid bool `json:"valid"`
	// Checked is false when we couldn't reach GitHub (network/timeout) — the
	// UI should stay neutral rather than claim the token is bad.
	Checked bool   `json:"checked"`
	Message string `json:"message"`
}

// ValidateGitHubToken checks whether GitHub accepts the token. Bound to the UI.
func (a *App) ValidateGitHubToken(token string) GitHubTokenStatus {
	return checkGitHubToken(githubAPIBase, token, &http.Client{Timeout: 10 * time.Second})
}

// checkGitHubToken hits GET <base>/rate_limit with the token. /rate_limit is
// used deliberately over /user: it returns 200 for ANY valid credential —
// classic/fine-grained PATs AND GitHub App installation tokens (the
// x-access-token pattern a private clone uses) — and 401 "Bad credentials" for
// a rejected one. /user would 403 for installation tokens and false-negative.
func checkGitHubToken(base, token string, hc *http.Client) GitHubTokenStatus {
	if token == "" {
		return GitHubTokenStatus{Valid: false, Checked: true, Message: "no token"}
	}
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, base+"/rate_limit", nil)
	if err != nil {
		return GitHubTokenStatus{Checked: false, Message: "could not build request"}
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/vnd.github+json")
	resp, err := hc.Do(req)
	if err != nil {
		return GitHubTokenStatus{Checked: false, Message: "could not reach GitHub: " + err.Error()}
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusOK:
		return GitHubTokenStatus{Valid: true, Checked: true, Message: "GitHub accepted the token"}
	case resp.StatusCode == http.StatusUnauthorized:
		return GitHubTokenStatus{Valid: false, Checked: true, Message: "GitHub rejected the token (401 Bad credentials) — expired or wrong value"}
	default:
		// A non-401 error (403 rate-limited, 5xx, etc.) is not a clean verdict.
		return GitHubTokenStatus{Checked: false, Message: "inconclusive (GitHub returned " + resp.Status + ")"}
	}
}
