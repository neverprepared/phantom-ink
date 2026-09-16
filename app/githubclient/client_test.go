package githubclient

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// stub serves canned JSON per path and records the request it saw. Mirrors the
// httptest style of app/app_github_token_test.go.
func stub(t *testing.T, body map[string]string) (*Client, *http.Request) {
	t.Helper()
	var last http.Request
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		last = *r
		b, ok := body[r.URL.Path]
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(b))
	}))
	t.Cleanup(srv.Close)
	return NewWithBase(srv.URL, srv.Client()), &last
}

func TestListRepos(t *testing.T) {
	c, last := stub(t, map[string]string{
		"/user/repos": `[{
			"name":"phantom-ink","full_name":"acme/phantom-ink",
			"description":"desk app","html_url":"https://github.com/acme/phantom-ink",
			"clone_url":"https://github.com/acme/phantom-ink.git",
			"default_branch":"main","pushed_at":"2026-09-10T12:00:00Z",
			"stargazers_count":7,"open_issues_count":3,
			"owner":{"login":"acme"}
		}]`,
	})
	repos, err := c.ListRepos(context.Background(), "tok")
	if err != nil {
		t.Fatalf("ListRepos: %v", err)
	}
	if len(repos) != 1 {
		t.Fatalf("want 1 repo, got %d", len(repos))
	}
	r := repos[0]
	if r.Owner != "acme" || r.Name != "phantom-ink" || r.FullName != "acme/phantom-ink" {
		t.Errorf("identity wrong: %+v", r)
	}
	if r.DefaultBranch != "main" || r.Stars != 7 || r.OpenIssues != 3 {
		t.Errorf("meta wrong: %+v", r)
	}
	if r.CloneURL != "https://github.com/acme/phantom-ink.git" {
		t.Errorf("clone url = %q", r.CloneURL)
	}
	if got := last.Header.Get("Authorization"); got != "Bearer tok" {
		t.Errorf("auth header = %q", got)
	}
	if got := last.Header.Get("Accept"); got != "application/vnd.github+json" {
		t.Errorf("accept header = %q", got)
	}
	q := last.URL.Query()
	if q.Get("sort") != "pushed" || q.Get("per_page") != "50" {
		t.Errorf("query = %q", last.URL.RawQuery)
	}
	if q.Get("affiliation") != "owner,collaborator,organization_member" {
		t.Errorf("affiliation = %q", q.Get("affiliation"))
	}
}

const searchPRBody = `{"items":[{
	"number":41,"title":"Wire the code page","state":"open",
	"html_url":"https://github.com/acme/phantom-ink/pull/41",
	"updated_at":"2026-09-11T09:00:00Z","draft":true,
	"repository_url":"https://api.github.com/repos/acme/phantom-ink",
	"user":{"login":"octo"},
	"pull_request":{"url":"https://api.github.com/repos/acme/phantom-ink/pulls/41"}
}]}`

func TestSearchPRsAuthored(t *testing.T) {
	c, last := stub(t, map[string]string{"/search/issues": searchPRBody})
	prs, err := c.SearchPRsAuthored(context.Background(), "tok")
	if err != nil {
		t.Fatalf("SearchPRsAuthored: %v", err)
	}
	if len(prs) != 1 {
		t.Fatalf("want 1 pr, got %d", len(prs))
	}
	p := prs[0]
	if p.RepoFullName != "acme/phantom-ink" {
		t.Errorf("repo = %q (must be derived from repository_url)", p.RepoFullName)
	}
	if p.Number != 41 || p.Title != "Wire the code page" || p.State != "open" {
		t.Errorf("fields wrong: %+v", p)
	}
	if !p.Draft || !p.IsPullRequest {
		t.Errorf("draft/pull_request presence wrong: %+v", p)
	}
	if p.User != "octo" {
		t.Errorf("user = %q", p.User)
	}
	if p.Reason != ReasonAuthored {
		t.Errorf("reason = %q, want %q", p.Reason, ReasonAuthored)
	}
	if q := last.URL.Query().Get("q"); q != "is:open is:pr author:@me" {
		t.Errorf("q = %q", q)
	}
}

func TestSearchPRsReviewRequested(t *testing.T) {
	c, last := stub(t, map[string]string{"/search/issues": searchPRBody})
	prs, err := c.SearchPRsReviewRequested(context.Background(), "tok")
	if err != nil {
		t.Fatalf("SearchPRsReviewRequested: %v", err)
	}
	if len(prs) != 1 || prs[0].Reason != ReasonReviewRequested {
		t.Fatalf("want one review-requested pr, got %+v", prs)
	}
	if q := last.URL.Query().Get("q"); q != "is:open is:pr review-requested:@me" {
		t.Errorf("q = %q", q)
	}
}

func TestSearchIssuesAssigned(t *testing.T) {
	c, last := stub(t, map[string]string{"/search/issues": `{"items":[{
		"number":9,"title":"Panel blanks on 401","state":"open",
		"html_url":"https://github.com/acme/phantom-ink/issues/9",
		"updated_at":"2026-09-12T08:00:00Z",
		"repository_url":"https://api.github.com/repos/acme/phantom-ink",
		"user":{"login":"neo"}
	}]}`})
	issues, err := c.SearchIssuesAssigned(context.Background(), "tok")
	if err != nil {
		t.Fatalf("SearchIssuesAssigned: %v", err)
	}
	if len(issues) != 1 {
		t.Fatalf("want 1 issue, got %d", len(issues))
	}
	i := issues[0]
	if i.IsPullRequest {
		t.Errorf("no pull_request key → IsPullRequest must be false: %+v", i)
	}
	if i.Reason != ReasonAssigned {
		t.Errorf("reason = %q", i.Reason)
	}
	if q := last.URL.Query().Get("q"); q != "is:open is:issue assignee:@me" {
		t.Errorf("q = %q", q)
	}
}

func TestListNotifications(t *testing.T) {
	c, last := stub(t, map[string]string{"/notifications": `[{
		"id":"1001","reason":"review_requested","updated_at":"2026-09-12T07:00:00Z",
		"repository":{"full_name":"acme/phantom-ink"},
		"subject":{"title":"Wire the code page","type":"PullRequest",
		           "url":"https://api.github.com/repos/acme/phantom-ink/pulls/41"}
	},{
		"id":"1002","reason":"assign","updated_at":"2026-09-12T07:30:00Z",
		"repository":{"full_name":"acme/phantom-ink"},
		"subject":{"title":"Panel blanks on 401","type":"Issue",
		           "url":"https://api.github.com/repos/acme/phantom-ink/issues/9"}
	}]`})
	ns, err := c.ListNotifications(context.Background(), "tok")
	if err != nil {
		t.Fatalf("ListNotifications: %v", err)
	}
	if len(ns) != 2 {
		t.Fatalf("want 2 notifications, got %d", len(ns))
	}
	if ns[0].RepoFullName != "acme/phantom-ink" || ns[0].SubjectTitle != "Wire the code page" {
		t.Errorf("fields wrong: %+v", ns[0])
	}
	if ns[0].Reason != "review_requested" || ns[0].SubjectType != "PullRequest" {
		t.Errorf("reason/type wrong: %+v", ns[0])
	}
	// A subject API URL must become something a browser can open.
	if ns[0].URL != "https://github.com/acme/phantom-ink/pull/41" {
		t.Errorf("pr browser url = %q", ns[0].URL)
	}
	if ns[1].URL != "https://github.com/acme/phantom-ink/issues/9" {
		t.Errorf("issue browser url = %q", ns[1].URL)
	}
	if last.URL.Path != "/notifications" {
		t.Errorf("path = %q", last.URL.Path)
	}
}

func TestSubjectBrowserURL_Unmappable(t *testing.T) {
	// A Discussion/Release subject has no api→html rewrite; falling back to the
	// repo page beats handing the UI a dead api.github.com link.
	got := subjectBrowserURL("https://api.github.com/repos/acme/ink/releases/5", "acme/ink")
	if got != "https://github.com/acme/ink" {
		t.Errorf("fallback = %q", got)
	}
	if got := subjectBrowserURL("", "acme/ink"); got != "https://github.com/acme/ink" {
		t.Errorf("empty subject url fallback = %q", got)
	}
	if got := subjectBrowserURL("", ""); got != "" {
		t.Errorf("nothing to link to should stay empty, got %q", got)
	}
}

func TestUnauthorizedIsTyped(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"message":"Bad credentials"}`))
	}))
	t.Cleanup(srv.Close)
	c := NewWithBase(srv.URL, srv.Client())
	_, err := c.ListRepos(context.Background(), "bad")
	if err == nil {
		t.Fatal("401 must be an error")
	}
	if !IsUnauthorized(err) {
		t.Fatalf("401 must be detectable as unauthorized, got %v", err)
	}
}

func TestNonOKIsError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	t.Cleanup(srv.Close)
	c := NewWithBase(srv.URL, srv.Client())
	if _, err := c.ListNotifications(context.Background(), "tok"); err == nil {
		t.Fatal("500 must be an error")
	} else if IsUnauthorized(err) {
		t.Fatal("500 is not an auth failure")
	}
}

func TestEmptyTokenRejectedWithoutNetwork(t *testing.T) {
	c := NewWithBase("http://127.0.0.1:0", http.DefaultClient)
	if _, err := c.ListRepos(context.Background(), ""); err == nil {
		t.Fatal("empty token must fail fast")
	}
}

func TestNewDefaultsToPublicAPI(t *testing.T) {
	if got := New().base; got != DefaultBase {
		t.Errorf("New() base = %q, want %q", got, DefaultBase)
	}
	if New().hc == nil {
		t.Error("New() must carry an http client with a timeout")
	}
}
