package github

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"phantom-ink/provider"
)

const mergedPRsBody = `[
  {"number":12,"title":"Add thing","user":{"login":"me"},
   "created_at":"2026-09-01T10:00:00Z","updated_at":"2026-09-01T12:00:00Z",
   "merged_at":"2026-09-01T12:00:00Z",
   "merge_commit_sha":"deadbeef","base":{"ref":"main","repo":{"full_name":"o/r"}},
   "html_url":"https://example.test/pr/12"},
  {"number":13,"title":"Other branch","user":{"login":"me"},
   "created_at":"2026-09-02T10:00:00Z","updated_at":"2026-09-02T12:00:00Z",
   "merged_at":"2026-09-02T12:00:00Z",
   "merge_commit_sha":"cafe","base":{"ref":"release","repo":{"full_name":"o/r"}},
   "html_url":"https://example.test/pr/13"},
  {"number":14,"title":"Abandoned","user":{"login":"me"},
   "created_at":"2026-09-03T10:00:00Z","updated_at":"2026-09-03T12:00:00Z",
   "merged_at":null,
   "base":{"ref":"main","repo":{"full_name":"o/r"}},"html_url":"https://example.test/pr/14"}
]`

func TestListMergedPRsMapsFieldsAndFiltersUnmerged(t *testing.T) {
	var last *http.Request
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		last = r.Clone(r.Context())
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(mergedPRsBody))
	}))
	defer srv.Close()

	c := NewWithBase(srv.URL, "test-token", srv.Client())
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "o", Name: "r"},
		time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("both merged PRs are returned; base-branch filtering is the CALLER's job: got %d (%+v)", len(got), got)
	}
	if got[0].Number != 12 || got[0].MergedAt != "2026-09-01T12:00:00Z" ||
		got[0].MergeSHA != "deadbeef" || got[0].BaseRef != "main" || got[0].Author != "me" {
		t.Fatalf("field mapping wrong: %+v", got[0])
	}
	if got[0].CreatedAt != "2026-09-01T10:00:00Z" || got[0].Title != "Add thing" ||
		got[0].HTMLURL != "https://example.test/pr/12" || got[0].RepoFullName != "o/r" ||
		got[0].Provider != provider.KindGitHub {
		t.Fatalf("field mapping wrong: %+v", got[0])
	}
	// The non-default-branch merge is returned WITH its base, so the caller can
	// make the deployment decision it owns.
	if got[1].Number != 13 || got[1].BaseRef != "release" {
		t.Fatalf("BaseRef must be mapped, not filtered: %+v", got[1])
	}
	if last == nil {
		t.Fatal("no request reached the server")
	}
	if got := last.URL.Query().Get("state"); got != "closed" {
		t.Errorf("merged PRs live behind state=closed, got %q", got)
	}
}

func TestListMergedPRsDropsMergesOlderThanSince(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(mergedPRsBody))
	}))
	defer srv.Close()

	c := NewWithBase(srv.URL, "test-token", srv.Client())
	// A watermark after PR 12's merge: only PR 13 is new work.
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "o", Name: "r"},
		time.Date(2026, 9, 2, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	if len(got) != 1 || got[0].Number != 13 {
		t.Fatalf("since must exclude merges at or before the watermark: %+v", got)
	}
}

// RepoFullName has to be usable as the store's key even when a payload omits
// base.repo — the ref the caller passed already identifies the repo.
func TestListMergedPRsFallsBackToTheRefForRepoFullName(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`[{"number":1,"title":"t","merged_at":"2026-09-01T12:00:00Z",
		  "created_at":"2026-09-01T10:00:00Z","base":{"ref":"main"}}]`))
	}))
	defer srv.Close()

	c := NewWithBase(srv.URL, "test-token", srv.Client())
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "acme", Name: "widgets"},
		time.Time{})
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	if len(got) != 1 || got[0].RepoFullName != "acme/widgets" {
		t.Fatalf("want the ref-derived full name: %+v", got)
	}
}

// A repo with more merges than one page must be followed, but a server that
// keeps answering must not spin forever.
func TestListMergedPRsPaginatesAndTerminates(t *testing.T) {
	var pages int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		pages++
		w.Header().Set("Content-Type", "application/json")
		// A FULL page every time: only the page cap or the since-cutoff can
		// stop this, which is exactly the runaway being guarded against.
		out := "["
		for i := 0; i < mergedPRsPerPage; i++ {
			if i > 0 {
				out += ","
			}
			out += fmt.Sprintf(`{"number":%d,"title":"t","created_at":"2026-09-01T10:00:00Z",
			  "updated_at":"2026-09-01T12:00:00Z","merged_at":"2026-09-01T12:00:00Z",
			  "base":{"ref":"main"}}`, pages*1000+i)
		}
		out += "]"
		_, _ = w.Write([]byte(out))
	}))
	defer srv.Close()

	c := NewWithBase(srv.URL, "test-token", srv.Client())
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "o", Name: "r"}, time.Time{})
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	if pages < 2 {
		t.Fatalf("a full page must be followed by another fetch, saw %d", pages)
	}
	if pages > mergedPRsMaxPages {
		t.Fatalf("pagination must be capped at %d pages, made %d requests", mergedPRsMaxPages, pages)
	}
	if len(got) != pages*mergedPRsPerPage {
		t.Fatalf("every page's merges must be kept: got %d over %d pages", len(got), pages)
	}
}

// Once a page's merges all predate the watermark there is nothing older worth
// fetching — that is what makes the sync incremental rather than a full
// re-crawl on every run.
func TestListMergedPRsStopsPagingOnceResultsPredateSince(t *testing.T) {
	var pages int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		pages++
		w.Header().Set("Content-Type", "application/json")
		out := "["
		for i := 0; i < mergedPRsPerPage; i++ {
			if i > 0 {
				out += ","
			}
			out += fmt.Sprintf(`{"number":%d,"title":"t","created_at":"2020-01-01T10:00:00Z",
			  "updated_at":"2020-01-01T12:00:00Z","merged_at":"2020-01-01T12:00:00Z",
			  "base":{"ref":"main"}}`, i)
		}
		out += "]"
		_, _ = w.Write([]byte(out))
	}))
	defer srv.Close()

	c := NewWithBase(srv.URL, "test-token", srv.Client())
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "o", Name: "r"},
		time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("everything predates the watermark: %+v", got)
	}
	if pages != 1 {
		t.Fatalf("a fully-stale page must end the walk, made %d requests", pages)
	}
}

func TestListMergedPRsSurfacesProviderErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer srv.Close()

	c := NewWithBase(srv.URL, "test-token", srv.Client())
	if _, err := c.ListMergedPRs(context.Background(), provider.RepoRef{Owner: "o", Name: "r"}, time.Time{}); err == nil {
		t.Fatal("a 403 must be returned so the caller can report it per repo")
	}
}
