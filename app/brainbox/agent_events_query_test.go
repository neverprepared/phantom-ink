package brainbox

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
)

// These values are data-derived — envelope ids, workspace names, parent ids —
// and the two call sites used to hand-roll `q += k + "=" + v` with no escaping,
// while SearchAgentEvents in the same file used url.Values correctly. A value
// containing & or = therefore broke the query or injected a parameter.

func TestQueryStringEscapesValues(t *testing.T) {
	got := queryString(map[string]string{"id": "a&b=c d"})
	parsed, err := url.ParseQuery(got[1:]) // drop the leading "?"
	if err != nil {
		t.Fatalf("produced an unparseable query %q: %v", got, err)
	}
	if parsed.Get("id") != "a&b=c d" {
		t.Errorf("id round-tripped as %q, want %q", parsed.Get("id"), "a&b=c d")
	}
	if len(parsed) != 1 {
		t.Errorf("value injected extra parameters: %v", parsed)
	}
}

func TestQueryStringSkipsEmptyAndReturnsBlank(t *testing.T) {
	if got := queryString(map[string]string{"a": "", "b": ""}); got != "" {
		t.Errorf("all-empty pairs produced %q, want an empty string", got)
	}
	got := queryString(map[string]string{"a": "1", "b": ""})
	if got != "?a=1" {
		t.Errorf("got %q, want ?a=1", got)
	}
}

// End to end: a workspace name with an ampersand must arrive intact rather
// than as a second parameter.
func TestListAgentStateEscapesWorkspace(t *testing.T) {
	var gotQuery url.Values
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.Query()
		_, _ = w.Write([]byte(`{"items":[]}`))
	}))
	t.Cleanup(srv.Close)

	c := NewClient(srv.URL, "")
	_, err := c.ListAgentState(ListAgentStateOptions{
		Workspace: "team&ops",
		Status:    "needs input",
		Limit:     5,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got := gotQuery.Get("workspace"); got != "team&ops" {
		t.Errorf("workspace = %q, want %q", got, "team&ops")
	}
	if got := gotQuery.Get("status"); got != "needs input" {
		t.Errorf("status = %q, want %q (spaces must survive)", got, "needs input")
	}
	if got := gotQuery.Get("limit"); got != "5" {
		t.Errorf("limit = %q", got)
	}
	// "ops" must not have become its own parameter.
	if _, injected := gotQuery["ops"]; injected {
		t.Error("the & in the workspace injected a parameter")
	}
}

func TestListAgentEventsEscapesIDs(t *testing.T) {
	var gotQuery url.Values
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.Query()
		_, _ = w.Write([]byte(`{"events":[],"count":0}`))
	}))
	t.Cleanup(srv.Close)

	c := NewClient(srv.URL, "")
	if _, err := c.ListAgentEvents("env=1&x", "parent/2", 10); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got := gotQuery.Get("id"); got != "env=1&x" {
		t.Errorf("id = %q, want %q", got, "env=1&x")
	}
	if got := gotQuery.Get("parent_id"); got != "parent/2" {
		t.Errorf("parent_id = %q", got)
	}
}

// No filters must not produce a bare "?" — harmless, but it used to.
func TestListAgentEventsOmitsEmptyQuery(t *testing.T) {
	var rawQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rawQuery = r.URL.RawQuery
		_, _ = w.Write([]byte(`{"events":[],"count":0}`))
	}))
	t.Cleanup(srv.Close)

	c := NewClient(srv.URL, "")
	if _, err := c.ListAgentEvents("", "", 0); err != nil {
		t.Fatal(err)
	}
	if rawQuery != "" {
		t.Errorf("RawQuery = %q, want empty", rawQuery)
	}
}
