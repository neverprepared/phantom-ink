package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
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

// --- Begin-work lanes -------------------------------------------------------

func TestNormalizeCloneURL(t *testing.T) {
	cases := []struct{ in, want string }{
		{"https://github.com/o/r.git", "https://github.com/o/r.git"},
		{"https://github.com/o/r", "https://github.com/o/r.git"},
		{"https://github.com/o/r/", "https://github.com/o/r.git"},
		{"git@github.com:o/r.git", "https://github.com/o/r.git"},
		{"git@github.com:o/r", "https://github.com/o/r.git"},
		{"ssh://git@github.com/o/r.git", "https://github.com/o/r.git"},
		{"https://api.github.com/repos/o/r", "https://github.com/o/r.git"},
		// A PR/issue html_url still identifies the repo it belongs to.
		{"https://github.com/o/r/pull/7", "https://github.com/o/r.git"},
		// Enterprise host is preserved, not rewritten to github.com.
		{"https://git.acme.io/o/r.git", "https://git.acme.io/o/r.git"},
		// Nothing to clone from.
		{"", ""},
		{"   ", ""},
		{"https://github.com/o", ""},
		{"not-a-url", ""},
	}
	for _, c := range cases {
		if got := normalizeCloneURL(c.in); got != c.want {
			t.Errorf("normalizeCloneURL(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestDeriveCloneDest(t *testing.T) {
	cases := []struct{ home, url, want string }{
		{"/ws/work", "https://github.com/o/phantom-ink.git", "/ws/work/code/phantom-ink"},
		{"/ws/work", "git@github.com:o/phantom-ink", "/ws/work/code/phantom-ink"},
		{"/ws/work", "https://github.com/o/r/pull/3", "/ws/work/code/r"},
		// No workspace home, or no derivable repo name → no destination.
		{"", "https://github.com/o/r.git", ""},
		{"/ws/work", "", ""},
		{"/ws/work", "https://github.com/o", ""},
	}
	for _, c := range cases {
		if got := deriveCloneDest(c.home, c.url); got != c.want {
			t.Errorf("deriveCloneDest(%q, %q) = %q, want %q", c.home, c.url, got, c.want)
		}
	}
}

// stubLanes replaces the clone + terminal seams for the duration of a test and
// records what they were asked to do.
type stubLanes struct {
	clones   [][2]string // (url, dest) per call
	opened   []string    // dirs the terminal opener saw
	cloneErr error
	openErr  error
}

func (s *stubLanes) install(t *testing.T) {
	t.Helper()
	origClone, origOpen := runGitClone, openTerminalAt
	runGitClone = func(cloneURL, dest string) error {
		s.clones = append(s.clones, [2]string{cloneURL, dest})
		if s.cloneErr != nil {
			return s.cloneErr
		}
		// A real clone leaves the directory behind; so must the fake, or a
		// second call would look like a fresh checkout.
		return os.MkdirAll(dest, 0o755)
	}
	openTerminalAt = func(dir string) error {
		s.opened = append(s.opened, dir)
		return s.openErr
	}
	t.Cleanup(func() { runGitClone, openTerminalAt = origClone, origOpen })
}

// laneApp builds an App whose profile scan finds exactly one profile, rooted in
// a temp dir, so the lanes resolve a real (throwaway) workspace home.
func laneApp(t *testing.T, profile string) (*App, string) {
	t.Helper()
	root := t.TempDir()
	home := filepath.Join(root, profile)
	if err := os.MkdirAll(home, 0o755); err != nil {
		t.Fatal(err)
	}
	// scanProfiles only counts a directory as a workspace when it has .envrc.
	if err := os.WriteFile(filepath.Join(home, ".envrc"), []byte("# test\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return &App{config: &Config{WorkspacesRoot: root}, ctx: context.Background()}, home
}

// A repo not yet on disk is cloned, then opened.
func TestOpenRepoLocally_ClonesWhenMissing(t *testing.T) {
	app, home := laneApp(t, "work")
	stub := &stubLanes{}
	stub.install(t)

	dest, err := app.OpenRepoLocally("work", "https://github.com/o/r")
	if err != nil {
		t.Fatalf("OpenRepoLocally: %v", err)
	}
	want := filepath.Join(home, "code", "r")
	if dest != want {
		t.Errorf("dest = %q, want %q", dest, want)
	}
	if len(stub.clones) != 1 {
		t.Fatalf("want 1 clone, got %v", stub.clones)
	}
	// The html_url must be normalized before it reaches git, and the token must
	// never be embedded in the URL.
	if stub.clones[0][0] != "https://github.com/o/r.git" {
		t.Errorf("clone url = %q", stub.clones[0][0])
	}
	if stub.clones[0][1] != want {
		t.Errorf("clone dest = %q, want %q", stub.clones[0][1], want)
	}
	if len(stub.opened) != 1 || stub.opened[0] != want {
		t.Errorf("terminal opened at %v, want [%s]", stub.opened, want)
	}
}

// An existing checkout is opened AS-IS: no clone and no pull, so a dirty
// working tree is never touched.
func TestOpenRepoLocally_ExistingCheckoutIsNotReCloned(t *testing.T) {
	app, home := laneApp(t, "work")
	dest := filepath.Join(home, "code", "r")
	if err := os.MkdirAll(dest, 0o755); err != nil {
		t.Fatal(err)
	}
	dirty := filepath.Join(dest, "WIP.txt")
	if err := os.WriteFile(dirty, []byte("uncommitted"), 0o644); err != nil {
		t.Fatal(err)
	}
	stub := &stubLanes{}
	stub.install(t)

	got, err := app.OpenRepoLocally("work", "git@github.com:o/r.git")
	if err != nil {
		t.Fatalf("OpenRepoLocally: %v", err)
	}
	if got != dest {
		t.Errorf("dest = %q, want %q", got, dest)
	}
	if len(stub.clones) != 0 {
		t.Errorf("existing checkout must not be cloned over, got %v", stub.clones)
	}
	if b, err := os.ReadFile(dirty); err != nil || string(b) != "uncommitted" {
		t.Errorf("working tree was disturbed: %q %v", b, err)
	}
	if len(stub.opened) != 1 || stub.opened[0] != dest {
		t.Errorf("terminal opened at %v, want [%s]", stub.opened, dest)
	}
}

// git's own stderr is the useful part of an auth failure — it must survive.
func TestOpenRepoLocally_ReturnsGitError(t *testing.T) {
	app, _ := laneApp(t, "work")
	stub := &stubLanes{cloneErr: errors.New("git clone: Repository not found.")}
	stub.install(t)

	if _, err := app.OpenRepoLocally("work", "https://github.com/o/private"); err == nil {
		t.Fatal("want an error")
	} else if !strings.Contains(err.Error(), "Repository not found") {
		t.Errorf("git stderr was swallowed: %v", err)
	}
	if len(stub.opened) != 0 {
		t.Errorf("a failed clone must not open a terminal, got %v", stub.opened)
	}
}

func TestOpenRepoLocally_UnknownProfile(t *testing.T) {
	app, _ := laneApp(t, "work")
	stub := &stubLanes{}
	stub.install(t)

	if _, err := app.OpenRepoLocally("other", "https://github.com/o/r"); err == nil {
		t.Fatal("a cross-profile call must fail, not fall back to another profile")
	}
	if len(stub.clones) != 0 || len(stub.opened) != 0 {
		t.Error("nothing should run for an unknown profile")
	}
}

// The container lane must reach CreateSession with exec_mode=interactive, the
// profile threaded through, and the seeded task verbatim.
func TestLaunchInteractiveSession_BuildsRequest(t *testing.T) {
	var body map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/create" {
			t.Errorf("want POST /api/create, got %s %s", r.Method, r.URL.Path)
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		_, _ = w.Write([]byte(`{"success":true,"url":"http://localhost:8080/s/code-r"}`))
	}))
	defer srv.Close()
	app, home := laneApp(t, "work")
	app.client = brainbox.NewClient(srv.URL, "")

	res, err := app.LaunchInteractiveSession(InteractiveSessionRequest{
		Profile: "work",
		RepoURL: "https://github.com/o/r",
		Task:    "Clone https://github.com/o/r.git (GITHUB_TOKEN available) and start work.",
	})
	if err != nil {
		t.Fatalf("LaunchInteractiveSession: %v", err)
	}
	if !res.Success || res.URL == "" {
		t.Errorf("response not passed through: %+v", res)
	}
	if body["exec_mode"] != "interactive" {
		t.Errorf("exec_mode = %v (a headless session has no REPL to attach to)", body["exec_mode"])
	}
	if body["workspace_profile"] != "work" {
		t.Errorf("workspace_profile = %v (cross-profile leakage would be a bug)", body["workspace_profile"])
	}
	if body["workspace_home"] != home {
		t.Errorf("workspace_home = %v, want %s", body["workspace_home"], home)
	}
	if body["task"] != "Clone https://github.com/o/r.git (GITHUB_TOKEN available) and start work." {
		t.Errorf("seeded task must reach the session verbatim, got %v", body["task"])
	}
	name, _ := body["name"].(string)
	if !regexp.MustCompile(`^code-r-[0-9a-f]{6}$`).MatchString(name) {
		t.Errorf("session name = %q, want code-<repo>-<suffix>", name)
	}
}

// Two launches against the same repo must not collide on the session name.
func TestSessionNameFor_IsUniqueAndSafe(t *testing.T) {
	a := sessionNameFor("https://github.com/o/My_Repo.Name")
	b := sessionNameFor("https://github.com/o/My_Repo.Name")
	if a == b {
		t.Errorf("two launches produced the same name %q", a)
	}
	if !regexp.MustCompile(`^code-my-repo-name-[0-9a-f]{6}$`).MatchString(a) {
		t.Errorf("name = %q, want a lowercase [a-z0-9-] name", a)
	}
	// An unusable repo URL still yields a legal name rather than "code--<id>".
	if got := sessionNameFor(""); !regexp.MustCompile(`^code-repo-[0-9a-f]{6}$`).MatchString(got) {
		t.Errorf("fallback name = %q", got)
	}
}

func TestLaunchInteractiveSession_UnknownProfile(t *testing.T) {
	app, _ := laneApp(t, "work")
	if _, err := app.LaunchInteractiveSession(InteractiveSessionRequest{Profile: "other"}); err == nil {
		t.Fatal("want an error for a profile that does not exist")
	}
}
