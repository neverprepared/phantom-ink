package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"phantom-ink/brainbox"
	"phantom-ink/githubclient"
)

// fakeRepoGitHub is a githubRepoFetcher whose every section can be made to
// fail independently — the case the per-section error fields exist for.
type fakeRepoGitHub struct {
	branches  []githubclient.Branch
	commits   []githubclient.Commit
	readme    string
	readmeURL string
	prs       []githubclient.Issue
	issues    []githubclient.Issue

	branchesErr error
	commitsErr  error
	readmeErr   error
	prsErr      error
	issuesErr   error

	gotToken string
	gotOwner string
	gotRepo  string
	gotLimit int
}

func (f *fakeRepoGitHub) ListBranches(_ context.Context, token, owner, repo string) ([]githubclient.Branch, error) {
	f.gotToken, f.gotOwner, f.gotRepo = token, owner, repo
	return f.branches, f.branchesErr
}
func (f *fakeRepoGitHub) ListRecentCommits(_ context.Context, _, _, _ string, limit int) ([]githubclient.Commit, error) {
	f.gotLimit = limit
	return f.commits, f.commitsErr
}
func (f *fakeRepoGitHub) GetReadme(context.Context, string, string, string) (string, string, error) {
	return f.readme, f.readmeURL, f.readmeErr
}
func (f *fakeRepoGitHub) SearchPRsByRepo(context.Context, string, string, string) ([]githubclient.Issue, error) {
	return f.prs, f.prsErr
}
func (f *fakeRepoGitHub) SearchIssuesByRepo(context.Context, string, string, string) ([]githubclient.Issue, error) {
	return f.issues, f.issuesErr
}

func TestBuildRepoDetail_FillsEverySection(t *testing.T) {
	f := &fakeRepoGitHub{
		branches: []githubclient.Branch{{Name: "main", SHA: "aaa"}, {Name: "wip", SHA: "bbb"}},
		commits: []githubclient.Commit{
			{SHA: "c1", Message: "feat: thing", Author: "ada", Date: "2026-09-12T10:00:00Z"},
		},
		readme:    "# hi",
		readmeURL: "https://github.com/acme/ink/blob/main/README.md",
		prs: []githubclient.Issue{
			{HTMLURL: "https://github.com/acme/ink/pull/1", UpdatedAt: "2026-09-10T00:00:00Z", IsPullRequest: true},
			{HTMLURL: "https://github.com/acme/ink/pull/2", UpdatedAt: "2026-09-12T00:00:00Z", IsPullRequest: true},
		},
		issues: []githubclient.Issue{{HTMLURL: "https://github.com/acme/ink/issues/7", UpdatedAt: "2026-09-11T00:00:00Z"}},
	}
	got := buildRepoDetail(context.Background(), f, "work", "acme", "ink", "main", "tok")

	if got.Profile != "work" || got.Owner != "acme" || got.Repo != "ink" || got.DefaultBranch != "main" {
		t.Errorf("request not echoed back: %+v", got)
	}
	if f.gotToken != "tok" || f.gotOwner != "acme" || f.gotRepo != "ink" {
		t.Errorf("token/owner/repo not passed through: %q %q %q", f.gotToken, f.gotOwner, f.gotRepo)
	}
	if f.gotLimit != repoDetailCommitLimit {
		t.Errorf("commit limit = %d, want %d", f.gotLimit, repoDetailCommitLimit)
	}
	if len(got.Branches) != 2 || len(got.Commits) != 1 || len(got.PRs) != 2 || len(got.Issues) != 1 {
		t.Errorf("sections not filled: %+v", got)
	}
	if got.Readme != "# hi" || got.ReadmeURL == "" {
		t.Errorf("readme not carried: %q / %q", got.Readme, got.ReadmeURL)
	}
	// PRs come back newest-first, like every other list in the panel.
	if got.PRs[0].HTMLURL != "https://github.com/acme/ink/pull/2" {
		t.Errorf("PRs not sorted updated-desc: %+v", got.PRs)
	}
	if got.TokenInvalid || got.TokenMissing {
		t.Errorf("healthy fetch must not flag the token: %+v", got)
	}
	if got.BranchesError != "" || got.CommitsError != "" || got.ReadmeError != "" ||
		got.PRsError != "" || got.IssuesError != "" {
		t.Errorf("healthy sections must have no error: %+v", got)
	}
}

func TestBuildRepoDetail_OneFailingSectionDoesNotBlankTheView(t *testing.T) {
	// A fine-grained PAT without the issues scope is the real-world version of
	// this: branches, commits, and the README must still render.
	f := &fakeRepoGitHub{
		branches:  []githubclient.Branch{{Name: "main"}},
		commits:   []githubclient.Commit{{SHA: "c1"}},
		readme:    "# hi",
		issuesErr: &githubclient.StatusError{Code: http.StatusForbidden, Status: "403 Forbidden", Path: "/search/issues"},
	}
	got := buildRepoDetail(context.Background(), f, "work", "acme", "ink", "main", "tok")

	if got.IssuesError == "" {
		t.Error("failing section must record its error")
	}
	if len(got.Branches) != 1 || len(got.Commits) != 1 || got.Readme != "# hi" {
		t.Errorf("healthy sections must still render: %+v", got)
	}
	if got.BranchesError != "" || got.CommitsError != "" || got.ReadmeError != "" {
		t.Errorf("healthy sections must have no error: %+v", got)
	}
	if got.TokenInvalid {
		t.Error("a 403 is not a rejected credential")
	}
}

func TestBuildRepoDetail_401AnywhereFlagsTheToken(t *testing.T) {
	f := &fakeRepoGitHub{
		branchesErr: &githubclient.StatusError{Code: http.StatusUnauthorized, Status: "401 Unauthorized", Path: "/repos/acme/ink/branches"},
	}
	got := buildRepoDetail(context.Background(), f, "work", "acme", "ink", "main", "tok")
	if !got.TokenInvalid {
		t.Error("a 401 in any section must flag the credential as rejected")
	}
	if !strings.Contains(got.BranchesError, "401") {
		t.Errorf("the section error must still name the failure: %q", got.BranchesError)
	}
}

func TestBuildRepoDetail_MissingReadmeIsNotAnError(t *testing.T) {
	// GetReadme turns a 404 into empty markdown + nil error, so a repo with no
	// README renders "No README", not a red box.
	f := &fakeRepoGitHub{branches: []githubclient.Branch{{Name: "main"}}}
	got := buildRepoDetail(context.Background(), f, "work", "acme", "bare", "main", "tok")
	if got.ReadmeError != "" {
		t.Errorf("an absent README must not be an error: %q", got.ReadmeError)
	}
	if got.Readme != "" {
		t.Errorf("readme = %q, want empty", got.Readme)
	}
}

func TestRepoDetail_NoTokenIsNotAnError(t *testing.T) {
	// A profile that hasn't curated a GITHUB_TOKEN is an actionable state, not
	// a failure — the panel shows the "connect a token" banner, same as the
	// overview does. Nothing must reach GitHub either.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/env") {
			t.Errorf("unexpected call to %s", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"profile":"work","env":{"OTHER":"x"}}`))
	}))
	defer srv.Close()
	app := &App{client: brainbox.NewClient(srv.URL, ""), ctx: context.Background()}

	got, err := app.RepoDetail("work", "acme", "ink", "main")
	if err != nil {
		t.Fatalf("RepoDetail with no token must not error: %v", err)
	}
	if !got.TokenMissing {
		t.Error("TokenMissing must be set when the profile has no GITHUB_TOKEN")
	}
	if got.TokenInvalid {
		t.Error("a missing token is not a rejected one")
	}
	// The request is echoed back even in the no-token case so a response that
	// lands after the operator navigated away can still be discarded.
	if got.Profile != "work" || got.Owner != "acme" || got.Repo != "ink" || got.DefaultBranch != "main" {
		t.Errorf("request must be echoed back even with no token: %+v", got)
	}
	if len(got.Branches) != 0 || len(got.Commits) != 0 {
		t.Errorf("nothing must be fetched without a token: %+v", got)
	}
}
