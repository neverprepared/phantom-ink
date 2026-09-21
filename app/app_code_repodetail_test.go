package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"phantom-ink/brainbox"
	"phantom-ink/provider"
)

// fakeDetailProvider is a provider.Client whose detail methods can each be
// made to fail independently — the case the per-section error fields exist
// for. The overview-only methods are unused here and return nothing.
type fakeDetailProvider struct {
	kind provider.Kind

	branches  []provider.Branch
	commits   []provider.Commit
	readme    string
	readmeURL string
	prs       []provider.Item
	issues    []provider.Item

	branchesErr error
	commitsErr  error
	readmeErr   error
	prsErr      error
	issuesErr   error

	mu       sync.Mutex // guards gotRef/gotLimit; buildRepoDetail fans calls out concurrently
	gotRef   provider.RepoRef
	gotLimit int
}

func (f *fakeDetailProvider) Kind() provider.Kind                                  { return f.kind }
func (f *fakeDetailProvider) ListRepos(context.Context) ([]provider.Repo, error)   { return nil, nil }
func (f *fakeDetailProvider) SearchMyPRs(context.Context) ([]provider.Item, error) { return nil, nil }
func (f *fakeDetailProvider) ListAssignedWork(context.Context) ([]provider.Item, error) {
	return nil, nil
}
func (f *fakeDetailProvider) ListBranches(_ context.Context, ref provider.RepoRef) ([]provider.Branch, error) {
	f.mu.Lock()
	f.gotRef = ref
	f.mu.Unlock()
	return f.branches, f.branchesErr
}
func (f *fakeDetailProvider) ListRecentCommits(_ context.Context, ref provider.RepoRef, limit int) ([]provider.Commit, error) {
	f.mu.Lock()
	f.gotRef, f.gotLimit = ref, limit
	f.mu.Unlock()
	return f.commits, f.commitsErr
}
func (f *fakeDetailProvider) GetReadme(_ context.Context, ref provider.RepoRef) (string, string, error) {
	f.mu.Lock()
	f.gotRef = ref
	f.mu.Unlock()
	return f.readme, f.readmeURL, f.readmeErr
}
func (f *fakeDetailProvider) RepoPRs(_ context.Context, ref provider.RepoRef) ([]provider.Item, error) {
	f.mu.Lock()
	f.gotRef = ref
	f.mu.Unlock()
	return f.prs, f.prsErr
}
func (f *fakeDetailProvider) RepoIssues(_ context.Context, ref provider.RepoRef) ([]provider.Item, error) {
	f.mu.Lock()
	f.gotRef = ref
	f.mu.Unlock()
	return f.issues, f.issuesErr
}
func (f *fakeDetailProvider) NormalizeCloneURL(u string) string { return u }

func testRef() provider.RepoRef {
	return provider.RepoRef{Provider: provider.KindGitHub, Owner: "acme", Name: "ink", DefaultBranch: "main"}
}

func TestBuildRepoDetail_FillsEverySection(t *testing.T) {
	f := &fakeDetailProvider{
		kind:     provider.KindGitHub,
		branches: []provider.Branch{{Name: "main", SHA: "aaa"}, {Name: "wip", SHA: "bbb"}},
		commits: []provider.Commit{
			{SHA: "c1", Message: "feat: thing", Author: "ada", Date: "2026-09-12T10:00:00Z"},
		},
		readme:    "# hi",
		readmeURL: "https://github.com/acme/ink/blob/main/README.md",
		prs: []provider.Item{
			{HTMLURL: "https://github.com/acme/ink/pull/1", UpdatedAt: "2026-09-10T00:00:00Z", IsPullRequest: true},
			{HTMLURL: "https://github.com/acme/ink/pull/2", UpdatedAt: "2026-09-12T00:00:00Z", IsPullRequest: true},
		},
		issues: []provider.Item{{HTMLURL: "https://github.com/acme/ink/issues/7", UpdatedAt: "2026-09-11T00:00:00Z"}},
	}
	ref := testRef()
	got := buildRepoDetail(context.Background(), f, "work", ref)

	if got.Profile != "work" || got.Owner != "acme" || got.Repo != "ink" || got.DefaultBranch != "main" {
		t.Errorf("request not echoed back: %+v", got)
	}
	if got.Provider != provider.KindGitHub {
		t.Errorf("provider not echoed back: %q", got.Provider)
	}
	if f.gotRef != ref {
		t.Errorf("ref not passed through: %+v", f.gotRef)
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
	f := &fakeDetailProvider{
		kind:      provider.KindGitHub,
		branches:  []provider.Branch{{Name: "main"}},
		commits:   []provider.Commit{{SHA: "c1"}},
		readme:    "# hi",
		issuesErr: &provider.StatusError{Code: http.StatusForbidden, Status: "403 Forbidden", Path: "/search/issues"},
	}
	got := buildRepoDetail(context.Background(), f, "work", testRef())

	if got.IssuesError == "" {
		t.Error("failing section must record its error")
	}
	if !strings.Contains(got.IssuesError, "github:") {
		t.Errorf("failing section's error must be provider-prefixed: %q", got.IssuesError)
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
	f := &fakeDetailProvider{
		kind:        provider.KindGitHub,
		branchesErr: &provider.StatusError{Code: http.StatusUnauthorized, Status: "401 Unauthorized", Path: "/repos/acme/ink/branches"},
	}
	got := buildRepoDetail(context.Background(), f, "work", testRef())
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
	f := &fakeDetailProvider{kind: provider.KindGitHub, branches: []provider.Branch{{Name: "main"}}}
	got := buildRepoDetail(context.Background(), f, "work", testRef())
	if got.ReadmeError != "" {
		t.Errorf("an absent README must not be an error: %q", got.ReadmeError)
	}
	if got.Readme != "" {
		t.Errorf("readme = %q, want empty", got.Readme)
	}
}

func TestRepoDetail_NoProviderIsNotAnError(t *testing.T) {
	// A profile that hasn't curated any provider is an actionable state, not a
	// failure — the panel shows the "connect a provider" banner, same as the
	// overview does. Nothing must reach a provider either.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/env") {
			t.Errorf("unexpected call to %s", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"profile":"work","env":{"OTHER":"x"}}`))
	}))
	defer srv.Close()
	app := &App{client: brainbox.NewClient(srv.URL, ""), ctx: context.Background()}

	ref := testRef()
	got, err := app.RepoDetail("work", ref)
	if err != nil {
		t.Fatalf("RepoDetail with no provider must not error: %v", err)
	}
	if !got.TokenMissing {
		t.Error("TokenMissing must be set when the profile has no configured provider")
	}
	if got.TokenInvalid {
		t.Error("a missing provider is not a rejected one")
	}
	// The request is echoed back even in the no-provider case so a response
	// that lands after the operator navigated away can still be discarded.
	if got.Profile != "work" || got.Owner != "acme" || got.Repo != "ink" || got.DefaultBranch != "main" {
		t.Errorf("request must be echoed back even with no provider: %+v", got)
	}
	if len(got.Branches) != 0 || len(got.Commits) != 0 {
		t.Errorf("nothing must be fetched without a provider: %+v", got)
	}
}

func TestRepoDetail_UnconfiguredProviderIsTokenMissing(t *testing.T) {
	// GitHub is configured but the ref names ADO — routing must not silently
	// fall back to the wrong client.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"profile":"work","env":{"GITHUB_TOKEN":"tok"}}`))
	}))
	defer srv.Close()
	app := &App{client: brainbox.NewClient(srv.URL, ""), ctx: context.Background()}

	ref := provider.RepoRef{Provider: provider.KindADO, Owner: "proj", Name: "r"}
	got, err := app.RepoDetail("work", ref)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !got.TokenMissing {
		t.Errorf("a ref for an unconfigured provider must be TokenMissing: %+v", got)
	}
}
