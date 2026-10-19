package brainbox

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// The whole point of these tests: "no work available" and "the request failed"
// must be DISTINGUISHABLE to the caller. They were not -- GetPendingWork
// returned (nil, nil) for both, because it decided by inspecting item.ID, which
// a failed request never populates. The runner loop only sleeps when it gets an
// error, so every transport failure became a hot loop against a dead API.

func newTestClient(t *testing.T, h http.HandlerFunc) *Client {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	return NewClient(srv.URL, "")
}

func TestGetPendingWork_NoContentMeansNoWork(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})
	item, err := c.GetPendingWork("runner-1", http.DefaultClient)
	if err != nil {
		t.Fatalf("204 must not be an error, got %v", err)
	}
	if item != nil {
		t.Fatalf("204 must yield no item, got %+v", item)
	}
}

func TestGetPendingWork_ServerErrorPropagates(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "boom", http.StatusInternalServerError)
	})
	item, err := c.GetPendingWork("runner-1", http.DefaultClient)
	if err == nil {
		t.Fatal("a 500 must return an error, or the caller cannot back off")
	}
	if item != nil {
		t.Fatalf("failed request must yield no item, got %+v", item)
	}
}

func TestGetPendingWork_UnauthorizedPropagates(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
	})
	if _, err := c.GetPendingWork("runner-1", http.DefaultClient); err == nil {
		t.Fatal("a 401 must return an error, not read as 'no work'")
	}
}

// A dead API is the case that actually burned: nothing is listening, so the
// request never completes, and that MUST surface as an error.
func TestGetPendingWork_UnreachableAPIPropagates(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := srv.URL
	srv.Close() // nothing is listening now
	c := NewClient(url, "")

	if _, err := c.GetPendingWork("runner-1", http.DefaultClient); err == nil {
		t.Fatal("an unreachable API must return an error, not read as 'no work'")
	}
}

func TestGetPendingWork_MalformedBodyPropagates(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("{not json"))
	})
	if _, err := c.GetPendingWork("runner-1", http.DefaultClient); err == nil {
		t.Fatal("a malformed body must return an error, not read as 'no work'")
	}
}

func TestGetPendingWork_ReturnsItem(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"work-42","type":"session.create"}`))
	})
	item, err := c.GetPendingWork("runner-1", http.DefaultClient)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if item == nil || item.ID != "work-42" {
		t.Fatalf("want work-42, got %+v", item)
	}
}

// An empty JSON object is a 200 with a body: still "no work", but it must not be
// confused with the empty-body case the sentinel covers.
func TestGetPendingWork_EmptyObjectMeansNoWork(t *testing.T) {
	c := newTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{}`))
	})
	item, err := c.GetPendingWork("runner-1", http.DefaultClient)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if item != nil {
		t.Fatalf("an id-less item must yield no work, got %+v", item)
	}
}
