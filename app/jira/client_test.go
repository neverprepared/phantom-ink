package jira

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func testServer(t *testing.T) (*httptest.Server, *int) {
	t.Helper()
	searchCalls := 0
	mux := http.NewServeMux()
	mux.HandleFunc("/rest/api/3/myself", func(w http.ResponseWriter, r *http.Request) {
		if u, p, ok := r.BasicAuth(); !ok || u != "me@example.com" || p != "tok" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		json.NewEncoder(w).Encode(map[string]string{"accountId": "1"})
	})
	mux.HandleFunc("/rest/api/3/project", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]map[string]string{{"key": "ABC"}, {"key": "DEF"}})
	})
	mux.HandleFunc("/rest/api/3/search/jql", func(w http.ResponseWriter, r *http.Request) {
		searchCalls++
		var body struct {
			JQL string `json:"jql"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		if !strings.Contains(body.JQL, "ABC-123") {
			t.Errorf("jql missing ABC-123: %q", body.JQL)
		}
		json.NewEncoder(w).Encode(map[string]any{
			"issues": []map[string]any{{
				"key": "ABC-123",
				"fields": map[string]any{
					"summary":  "add webhook receiver",
					"status":   map[string]any{"name": "In Progress", "statusCategory": map[string]any{"key": "indeterminate"}},
					"assignee": map[string]any{"displayName": "Curtis"},
				},
			}},
		})
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv, &searchCalls
}

func newTestClient(t *testing.T, base string) *Client {
	t.Helper()
	return New(Config{BaseURL: base, Username: "me@example.com", Token: "tok"}, http.DefaultClient)
}

func TestVerify(t *testing.T) {
	srv, _ := testServer(t)
	if err := newTestClient(t, srv.URL).Verify(context.Background()); err != nil {
		t.Fatalf("Verify: %v", err)
	}
}

func TestVerifyRejectsBadCredentials(t *testing.T) {
	srv, _ := testServer(t)
	c := New(Config{BaseURL: srv.URL, Username: "wrong", Token: "wrong"}, http.DefaultClient)
	if err := c.Verify(context.Background()); err == nil {
		t.Fatal("Verify with bad credentials returned nil error")
	}
}

func TestResolveFiltersUnknownProjects(t *testing.T) {
	srv, _ := testServer(t)
	c := newTestClient(t, srv.URL)
	got, err := c.Resolve(context.Background(), []string{"ABC-123", "UTF-8", "CVE-2024-1234"})
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("Resolve returned %d issues, want 1: %v", len(got), got)
	}
	iss := got["ABC-123"]
	if iss.Summary != "add webhook receiver" || iss.Status != "In Progress" || iss.Assignee != "Curtis" {
		t.Fatalf("issue not mapped: %+v", iss)
	}
	if iss.URL != srv.URL+"/browse/ABC-123" {
		t.Fatalf("URL = %q", iss.URL)
	}
}

func TestResolveCachesIssues(t *testing.T) {
	srv, calls := testServer(t)
	c := newTestClient(t, srv.URL)
	ctx := context.Background()
	c.Resolve(ctx, []string{"ABC-123"})
	c.Resolve(ctx, []string{"ABC-123"})
	if *calls != 1 {
		t.Fatalf("search called %d times, want 1 (second should hit cache)", *calls)
	}
}

func TestResolveEmptyKeysMakesNoCall(t *testing.T) {
	srv, calls := testServer(t)
	c := newTestClient(t, srv.URL)
	got, err := c.Resolve(context.Background(), nil)
	if err != nil || len(got) != 0 {
		t.Fatalf("Resolve(nil) = (%v, %v)", got, err)
	}
	if *calls != 0 {
		t.Fatalf("search called %d times for empty input, want 0", *calls)
	}
}

func TestSearchRunsTheGivenJQL(t *testing.T) {
	var seen string
	mux := http.NewServeMux()
	mux.HandleFunc("/rest/api/3/search/jql", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			JQL string `json:"jql"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		seen = body.JQL
		json.NewEncoder(w).Encode(map[string]any{
			"issues": []map[string]any{{
				"key": "ABC-130",
				"fields": map[string]any{
					"summary":  "flaky test",
					"status":   map[string]any{"name": "To Do", "statusCategory": map[string]any{"key": "new"}},
					"assignee": map[string]any{"displayName": "Curtis"},
				},
			}},
		})
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	c := New(Config{BaseURL: srv.URL, Username: "u", Token: "t"}, http.DefaultClient)
	got, err := c.Search(context.Background(), "assignee = currentUser()", 50)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if seen != "assignee = currentUser()" {
		t.Fatalf("jql sent = %q", seen)
	}
	if len(got) != 1 || got[0].Key != "ABC-130" || got[0].Status != "To Do" {
		t.Fatalf("issues = %+v", got)
	}
	if got[0].URL != srv.URL+"/browse/ABC-130" {
		t.Fatalf("URL = %q", got[0].URL)
	}
}

func TestSearchSurfacesBadJQL(t *testing.T) {
	// A typo in the user's JQL is a 400. It must surface as an error, never as
	// an empty list that reads like "no tickets".
	mux := http.NewServeMux()
	mux.HandleFunc("/rest/api/3/search/jql", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	c := New(Config{BaseURL: srv.URL, Username: "u", Token: "t"}, http.DefaultClient)
	got, err := c.Search(context.Background(), "assignee = nonsense(", 50)
	if err == nil {
		t.Fatal("bad JQL returned nil error")
	}
	if got != nil {
		t.Fatalf("bad JQL returned issues: %v", got)
	}
}

func TestSearchRejectsEmptyJQL(t *testing.T) {
	c := New(Config{BaseURL: "https://x", Username: "u", Token: "t"}, http.DefaultClient)
	if _, err := c.Search(context.Background(), "   ", 50); err == nil {
		t.Fatal("empty JQL accepted; it would fetch the entire instance")
	}
}
