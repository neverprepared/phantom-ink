package ado

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"phantom-ink/provider"
)

const completedPRsBody = `{"value":[
  {"pullRequestId":12,"title":"Add thing","status":"completed",
   "createdBy":{"uniqueName":"me@example.test"},
   "creationDate":"2026-09-01T10:00:00Z","closedDate":"2026-09-01T12:00:00Z",
   "lastMergeCommit":{"commitId":"deadbeef"},
   "repository":{"id":"repo-guid","name":"widget-api"},
   "targetRefName":"refs/heads/main"},
  {"pullRequestId":13,"title":"Other branch","status":"completed",
   "createdBy":{"uniqueName":"me@example.test"},
   "creationDate":"2026-09-02T10:00:00Z","closedDate":"2026-09-02T12:00:00Z",
   "lastMergeCommit":{"commitId":"cafe"},
   "repository":{"id":"repo-guid","name":"widget-api"},
   "targetRefName":"refs/heads/release"}
]}`

func mergedPRServer(t *testing.T, body string) (*httptest.Server, func() *http.Request) {
	t.Helper()
	var last *http.Request
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		last = r.Clone(r.Context())
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv, func() *http.Request { return last }
}

func TestADOListMergedPRsFiltersByTargetBranch(t *testing.T) {
	srv, lastReq := mergedPRServer(t, completedPRsBody)

	c := newTestClient(srv.URL)
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "org", Name: "repo", ID: "repo-guid"},
		time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("both completed PRs returned; branch filtering is the caller's job: %+v", got)
	}
	if got[0].MergedAt != "2026-09-01T12:00:00Z" || got[0].MergeSHA != "deadbeef" {
		t.Fatalf("field mapping wrong: %+v", got[0])
	}
	if got[0].Number != 12 || got[0].Title != "Add thing" ||
		got[0].Author != "me@example.test" || got[0].CreatedAt != "2026-09-01T10:00:00Z" ||
		got[0].Provider != provider.KindADO {
		t.Fatalf("field mapping wrong: %+v", got[0])
	}
	// refs/heads/ is stripped so BaseRef compares equal to Repo.DefaultBranch,
	// which ListRepos already strips the same way.
	if got[0].BaseRef != "main" || got[1].BaseRef != "release" {
		t.Fatalf("targetRefName must be stripped to a bare branch: %q / %q", got[0].BaseRef, got[1].BaseRef)
	}
	if r := lastReq(); r == nil {
		t.Fatal("no request reached the server")
	} else if q := r.URL.Query().Get("searchCriteria.status"); q != "completed" {
		t.Errorf("merged PRs live behind status=completed, got %q", q)
	}
}

func TestADOListMergedPRsDropsPRsClosedBeforeSince(t *testing.T) {
	srv, _ := mergedPRServer(t, completedPRsBody)

	c := newTestClient(srv.URL)
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "org", Name: "repo", ID: "repo-guid"},
		time.Date(2026, 9, 2, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	if len(got) != 1 || got[0].Number != 13 {
		t.Fatalf("since must exclude PRs completed before the watermark: %+v", got)
	}
}

// A "completed" PR with no merge commit was completed by cherry-pick or was
// abandoned-then-completed oddly; without a merge there is no deployment to
// record. A missing closedDate has no deploy timestamp at all.
func TestADOListMergedPRsSkipsRowsWithNoMergeEvidence(t *testing.T) {
	srv, _ := mergedPRServer(t, `{"value":[
	  {"pullRequestId":1,"title":"no close date","status":"completed",
	   "creationDate":"2026-09-01T10:00:00Z","targetRefName":"refs/heads/main",
	   "lastMergeCommit":{"commitId":"aaa"}},
	  {"pullRequestId":2,"title":"abandoned","status":"abandoned",
	   "creationDate":"2026-09-01T10:00:00Z","closedDate":"2026-09-01T12:00:00Z",
	   "targetRefName":"refs/heads/main"},
	  {"pullRequestId":3,"title":"good","status":"completed",
	   "creationDate":"2026-09-01T10:00:00Z","closedDate":"2026-09-01T12:00:00Z",
	   "lastMergeCommit":{"commitId":"bbb"},"targetRefName":"refs/heads/main"}
	]}`)

	c := newTestClient(srv.URL)
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "org", Name: "repo", ID: "repo-guid"}, time.Time{})
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	if len(got) != 1 || got[0].Number != 3 {
		t.Fatalf("only the genuinely-merged PR is a deployment: %+v", got)
	}
}

func TestADOListMergedPRsRepoFullNameFallsBackToTheRef(t *testing.T) {
	srv, _ := mergedPRServer(t, `{"value":[{"pullRequestId":5,"title":"t","status":"completed",
	  "creationDate":"2026-09-01T10:00:00Z","closedDate":"2026-09-01T12:00:00Z",
	  "lastMergeCommit":{"commitId":"ccc"},"targetRefName":"refs/heads/main"}]}`)

	c := newTestClient(srv.URL)
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: testProject, Name: "widget-api", ID: "repo-guid"}, time.Time{})
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	// The project is the owner on ADO, so the full name matches what ListRepos
	// builds — the store keys deploy events on it.
	if len(got) != 1 || got[0].RepoFullName != testProject+"/widget-api" {
		t.Fatalf("want %s/widget-api: %+v", testProject, got)
	}
}

func TestADOListMergedPRsSurfacesProviderErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer srv.Close()

	c := newTestClient(srv.URL)
	_, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "org", Name: "repo", ID: "repo-guid"}, time.Time{})
	if err == nil {
		t.Fatal("a 403 must be returned so the caller can report it per repo")
	}
}
