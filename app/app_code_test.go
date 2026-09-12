package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"phantom-ink/brainbox"
	"phantom-ink/githubclient"
)

// fakeGitHub is a githubFetcher whose every section can be made to fail
// independently — the case the panel's per-section error fields exist for.
type fakeGitHub struct {
	repos    []githubclient.Repo
	authored []githubclient.Issue
	reviews  []githubclient.Issue
	issues   []githubclient.Issue
	notifs   []githubclient.Notification

	reposErr    error
	authoredErr error
	reviewsErr  error
	issuesErr   error
	notifsErr   error

	gotToken string
}

func (f *fakeGitHub) ListRepos(_ context.Context, token string) ([]githubclient.Repo, error) {
	f.gotToken = token
	return f.repos, f.reposErr
}
func (f *fakeGitHub) SearchPRsAuthored(context.Context, string) ([]githubclient.Issue, error) {
	return f.authored, f.authoredErr
}
func (f *fakeGitHub) SearchPRsReviewRequested(context.Context, string) ([]githubclient.Issue, error) {
	return f.reviews, f.reviewsErr
}
func (f *fakeGitHub) SearchIssuesAssigned(context.Context, string) ([]githubclient.Issue, error) {
	return f.issues, f.issuesErr
}
func (f *fakeGitHub) ListNotifications(context.Context, string) ([]githubclient.Notification, error) {
	return f.notifs, f.notifsErr
}

func pr(url, reason, updated string) githubclient.Issue {
	return githubclient.Issue{HTMLURL: url, Reason: reason, UpdatedAt: updated, IsPullRequest: true}
}

func TestBuildCodeOverview_MergesAndTagsPullRequests(t *testing.T) {
	f := &fakeGitHub{
		authored: []githubclient.Issue{
			pr("https://github.com/a/b/pull/1", githubclient.ReasonAuthored, "2026-09-10T00:00:00Z"),
			pr("https://github.com/a/b/pull/2", githubclient.ReasonAuthored, "2026-09-12T00:00:00Z"),
		},
		reviews: []githubclient.Issue{
			// Same PR as authored #1 — must appear ONCE, tagged with both.
			pr("https://github.com/a/b/pull/1", githubclient.ReasonReviewRequested, "2026-09-10T00:00:00Z"),
			pr("https://github.com/c/d/pull/9", githubclient.ReasonReviewRequested, "2026-09-11T00:00:00Z"),
		},
	}
	got := buildCodeOverview(context.Background(), f, "work", "tok")

	if len(got.PullRequests) != 3 {
		t.Fatalf("want 3 deduped PRs, got %d: %+v", len(got.PullRequests), got.PullRequests)
	}
	// Newest first.
	if got.PullRequests[0].HTMLURL != "https://github.com/a/b/pull/2" {
		t.Errorf("not sorted updated-desc: %+v", got.PullRequests)
	}
	var merged githubclient.Issue
	for _, p := range got.PullRequests {
		if p.HTMLURL == "https://github.com/a/b/pull/1" {
			merged = p
		}
	}
	if !strings.Contains(merged.Reason, githubclient.ReasonAuthored) ||
		!strings.Contains(merged.Reason, githubclient.ReasonReviewRequested) {
		t.Errorf("duplicate PR must carry both reasons, got %q", merged.Reason)
	}
	if got.Profile != "work" {
		t.Errorf("profile not echoed back: %q", got.Profile)
	}
	if f.gotToken != "tok" {
		t.Errorf("token not passed through: %q", f.gotToken)
	}
}

func TestBuildCodeOverview_OneFailingSectionDoesNotBlankThePage(t *testing.T) {
	f := &fakeGitHub{
		repos:     []githubclient.Repo{{FullName: "a/b"}},
		authored:  []githubclient.Issue{pr("https://github.com/a/b/pull/1", githubclient.ReasonAuthored, "2026-09-10T00:00:00Z")},
		issues:    []githubclient.Issue{{RepoFullName: "a/b", Number: 3}},
		notifsErr: &githubclient.StatusError{Code: http.StatusForbidden, Status: "403 Forbidden", Path: "/notifications"},
	}
	got := buildCodeOverview(context.Background(), f, "work", "tok")

	if got.NotificationsError == "" {
		t.Error("failing section must record its error")
	}
	if len(got.Repos) != 1 || len(got.PullRequests) != 1 || len(got.Issues) != 1 {
		t.Errorf("healthy sections must still render: %+v", got)
	}
	if got.TokenInvalid {
		t.Error("a 403 is not a rejected credential")
	}
	if got.ReposError != "" || got.IssuesError != "" || got.PullRequestsError != "" {
		t.Errorf("healthy sections must have no error: %+v", got)
	}
}

func TestBuildCodeOverview_HalfFailedPRSectionKeepsTheOtherHalf(t *testing.T) {
	f := &fakeGitHub{
		authored:   []githubclient.Issue{pr("https://github.com/a/b/pull/1", githubclient.ReasonAuthored, "2026-09-10T00:00:00Z")},
		reviewsErr: errors.New("boom"),
	}
	got := buildCodeOverview(context.Background(), f, "work", "tok")
	if len(got.PullRequests) != 1 {
		t.Errorf("authored half must survive a review-requested failure: %+v", got.PullRequests)
	}
	if !strings.Contains(got.PullRequestsError, "boom") {
		t.Errorf("failure must be named: %q", got.PullRequestsError)
	}
}

func TestBuildCodeOverview_401SetsTokenInvalid(t *testing.T) {
	unauth := &githubclient.StatusError{Code: http.StatusUnauthorized, Status: "401 Unauthorized", Path: "/user/repos"}
	f := &fakeGitHub{reposErr: unauth, authoredErr: unauth, reviewsErr: unauth, issuesErr: unauth, notifsErr: unauth}
	got := buildCodeOverview(context.Background(), f, "work", "bad")
	if !got.TokenInvalid {
		t.Fatal("401 must set TokenInvalid so the panel shows a reconnect banner")
	}
	if got.TokenMissing {
		t.Error("a rejected token is present, not missing")
	}
}

func TestBuildCodeOverview_401OnOneSectionOnly(t *testing.T) {
	// Fine-grained PATs scope notifications separately from repos; a 401 there
	// still means the credential was rejected for that read.
	f := &fakeGitHub{
		repos:     []githubclient.Repo{{FullName: "a/b"}},
		notifsErr: &githubclient.StatusError{Code: http.StatusUnauthorized, Status: "401 Unauthorized", Path: "/notifications"},
	}
	got := buildCodeOverview(context.Background(), f, "work", "tok")
	if !got.TokenInvalid {
		t.Error("a 401 on any section marks the token invalid")
	}
	if len(got.Repos) != 1 {
		t.Error("the section that worked must still render")
	}
}

func TestMergePullRequests_KeepsRowsWithNoURL(t *testing.T) {
	got := mergePullRequests(
		[]githubclient.Issue{{Title: "no url", Reason: githubclient.ReasonAuthored}},
		[]githubclient.Issue{{Title: "also no url", Reason: githubclient.ReasonReviewRequested}},
	)
	if len(got) != 2 {
		t.Fatalf("rows without an html_url must not be deduped away: %+v", got)
	}
}

// GitHubOverview must return the token-missing state as a VALUE, not an
// error, so the panel renders a "connect a token" banner instead of a toast.
func TestGitHubOverview_NoTokenIsNotAnError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/env") {
			t.Errorf("unexpected call to %s", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"profile":"work","env":{"OTHER":"x"}}`))
	}))
	defer srv.Close()
	app := &App{client: brainbox.NewClient(srv.URL, ""), ctx: context.Background()}

	got, err := app.GitHubOverview("work")
	if err != nil {
		t.Fatalf("missing token must not be an error: %v", err)
	}
	if !got.TokenMissing {
		t.Errorf("want TokenMissing, got %+v", got)
	}
	if got.TokenInvalid {
		t.Error("a missing token is not a rejected one")
	}
	if got.Profile != "work" {
		t.Errorf("profile = %q", got.Profile)
	}
}

// A profile with no stored env at all (the gateway 404s) is the same
// actionable state, not a page-breaking error.
func TestGitHubOverview_NoStoredEnvIsTokenMissing(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()
	app := &App{client: brainbox.NewClient(srv.URL, ""), ctx: context.Background()}

	got, err := app.GitHubOverview("fresh")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !got.TokenMissing {
		t.Errorf("want TokenMissing, got %+v", got)
	}
}

// DispatchRepoTask must carry the profile through to the hub — a task
// submitted without workspace_profile would run with the wrong credentials.
func TestDispatchRepoTask_SubmitsThroughTheHub(t *testing.T) {
	var body map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/hub/tasks" {
			t.Errorf("want POST /api/hub/tasks, got %s %s", r.Method, r.URL.Path)
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		_, _ = w.Write([]byte(`{"id":"task-7","status":"pending","agent_name":"worker"}`))
	}))
	defer srv.Close()
	app := &App{client: brainbox.NewClient(srv.URL, ""), ctx: context.Background()}

	task, err := app.DispatchRepoTask(DispatchRepoRequest{
		Profile:     "work",
		RepoURL:     "https://github.com/a/b.git",
		AgentName:   "worker",
		Description: "Review PR #1",
	})
	if err != nil {
		t.Fatalf("DispatchRepoTask: %v", err)
	}
	if task.ID != "task-7" {
		t.Errorf("task id = %q", task.ID)
	}
	if body["workspace_profile"] != "work" {
		t.Errorf("workspace_profile = %v (cross-profile dispatch would be a bug)", body["workspace_profile"])
	}
	if body["repo_url"] != "https://github.com/a/b.git" || body["agent_name"] != "worker" {
		t.Errorf("payload wrong: %+v", body)
	}
	if body["description"] != "Review PR #1" {
		t.Errorf("edited prompt must reach the hub verbatim, got %v", body["description"])
	}
}
