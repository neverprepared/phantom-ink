package ado

import (
	"context"
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"phantom-ink/provider"
)

const (
	testOrg     = "acme"
	testProject = "widgets"
	testPAT     = "pat-123"
	meID        = "11111111-1111-1111-1111-111111111111"
)

// adoServer routes the handful of endpoints the client hits.
func adoServer(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()

	// Identity resolution.
	mux.HandleFunc("/"+testOrg+"/_apis/connectionData", func(w http.ResponseWriter, r *http.Request) {
		wantAuth := "Basic " + base64.StdEncoding.EncodeToString([]byte(":"+testPAT))
		if r.Header.Get("Authorization") != wantAuth {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(`{"authenticatedUser":{"id":"` + meID + `","providerDisplayName":"Me"}}`))
	})

	// Repos.
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/git/repositories", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"count":1,"value":[{"id":"repo-guid","name":"widget-api","defaultBranch":"refs/heads/main","webUrl":"https://dev.azure.com/acme/widgets/_git/widget-api","remoteUrl":"https://dev.azure.com/acme/widgets/_git/widget-api"}]}`))
	})

	// Org-level PRs (creator or reviewer). Return one PR for the creator query.
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/git/pullrequests", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("searchCriteria.creatorId") == meID {
			_, _ = w.Write([]byte(`{"value":[{"pullRequestId":7,"title":"Add caching","status":"active","isDraft":false,"creationDate":"2026-09-10T00:00:00Z","createdBy":{"displayName":"Me"},"repository":{"id":"repo-guid","name":"widget-api"}}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"value":[]}`))
	})

	// WIQL → ids.
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/wit/wiql", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"workItems":[{"id":42}]}`))
	})
	// Work item hydrate.
	mux.HandleFunc("/"+testOrg+"/_apis/wit/workitems", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"value":[{"id":42,"fields":{"System.Title":"Fix flaky test","System.State":"Active","System.WorkItemType":"Bug","System.ChangedDate":"2026-09-12T00:00:00Z"}}]}`))
	})

	// Branches, commits, readme (repo-scoped).
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/git/repositories/repo-guid/refs", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"value":[{"name":"refs/heads/main","objectId":"abc123"}]}`))
	})
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/git/repositories/repo-guid/commits", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"value":[{"commitId":"deadbeef","comment":"Initial commit","author":{"name":"Dev","date":"2026-09-01T00:00:00Z"},"remoteUrl":"https://dev.azure.com/acme/widgets/_git/widget-api/commit/deadbeef"}]}`))
	})
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/git/repositories/repo-guid/items", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("path") == "/README.md" {
			_, _ = w.Write([]byte(`{"content":"# Widget API\nHello","path":"/README.md"}`))
			return
		}
		w.WriteHeader(http.StatusNotFound)
	})

	return httptest.NewServer(mux)
}

func newTestClient(base string) *Client {
	return NewWithBase(base, testOrg, testProject, testPAT, nil)
}

func TestListRepos(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	repos, err := newTestClient(srv.URL).ListRepos(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(repos) != 1 {
		t.Fatalf("want 1 repo, got %d", len(repos))
	}
	r := repos[0]
	if r.Provider != provider.KindADO || r.Name != "widget-api" || r.ID != "repo-guid" {
		t.Fatalf("unexpected repo: %+v", r)
	}
	if r.DefaultBranch != "main" {
		t.Fatalf("want default branch main, got %q", r.DefaultBranch)
	}
	if r.Owner != testProject {
		t.Fatalf("want owner=project %q, got %q", testProject, r.Owner)
	}
	if !strings.Contains(r.CloneURL, "/_git/widget-api") {
		t.Fatalf("unexpected clone url: %q", r.CloneURL)
	}
}

func TestSearchMyPRs(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	prs, err := newTestClient(srv.URL).SearchMyPRs(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(prs) != 1 {
		t.Fatalf("want 1 PR, got %d", len(prs))
	}
	if prs[0].Number != 7 || !prs[0].IsPullRequest || prs[0].Provider != provider.KindADO {
		t.Fatalf("unexpected PR: %+v", prs[0])
	}
	if !strings.Contains(prs[0].HTMLURL, "/pullrequest/7") {
		t.Fatalf("unexpected PR url: %q", prs[0].HTMLURL)
	}
}

func TestListAssignedWork(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	work, err := newTestClient(srv.URL).ListAssignedWork(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(work) != 1 {
		t.Fatalf("want 1 work item, got %d", len(work))
	}
	w := work[0]
	if w.Number != 42 || w.IsPullRequest || w.Title != "Fix flaky test" {
		t.Fatalf("unexpected work item: %+v", w)
	}
	if !strings.Contains(w.HTMLURL, "/_workitems/edit/42") {
		t.Fatalf("unexpected work item url: %q", w.HTMLURL)
	}
}

func TestRepoDetailReads(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	c := newTestClient(srv.URL)
	ref := provider.RepoRef{Provider: provider.KindADO, Owner: testProject, Name: "widget-api", ID: "repo-guid"}

	branches, err := c.ListBranches(context.Background(), ref)
	if err != nil || len(branches) != 1 || branches[0].Name != "main" {
		t.Fatalf("branches: %+v err=%v", branches, err)
	}
	commits, err := c.ListRecentCommits(context.Background(), ref, 20)
	if err != nil || len(commits) != 1 || commits[0].SHA != "deadbeef" {
		t.Fatalf("commits: %+v err=%v", commits, err)
	}
	md, _, err := c.GetReadme(context.Background(), ref)
	if err != nil || !strings.Contains(md, "Widget API") {
		t.Fatalf("readme: %q err=%v", md, err)
	}
	issues, err := c.RepoIssues(context.Background(), ref)
	if err != nil || len(issues) != 0 {
		t.Fatalf("RepoIssues must be empty in v1: %+v err=%v", issues, err)
	}
}

func TestReadmeAbsentIsNotError(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	c := newTestClient(srv.URL)
	ref := provider.RepoRef{Provider: provider.KindADO, Owner: testProject, Name: "no-readme", ID: "missing-guid"}
	md, _, err := c.GetReadme(context.Background(), ref)
	if err != nil {
		t.Fatalf("absent README must be nil error, got %v", err)
	}
	if md != "" {
		t.Fatalf("absent README must be empty, got %q", md)
	}
}

func TestUnauthorizedSurfaces(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	c := NewWithBase(srv.URL, testOrg, testProject, "wrong-pat", nil)
	_, err := c.SearchMyPRs(context.Background())
	if !provider.IsUnauthorized(err) {
		t.Fatalf("want unauthorized, got %v", err)
	}
}

func TestNormalizeCloneURL(t *testing.T) {
	c := newTestClient("https://dev.azure.com")
	cases := map[string]string{
		"https://dev.azure.com/acme/widgets/_git/widget-api":     "https://dev.azure.com/acme/widgets/_git/widget-api",
		"https://acme@dev.azure.com/acme/widgets/_git/widget-api": "https://dev.azure.com/acme/widgets/_git/widget-api",
		"https://acme.visualstudio.com/widgets/_git/widget-api":   "https://dev.azure.com/acme/widgets/_git/widget-api",
	}
	for in, want := range cases {
		if got := c.NormalizeCloneURL(in); got != want {
			t.Errorf("NormalizeCloneURL(%q) = %q, want %q", in, got, want)
		}
	}
}
