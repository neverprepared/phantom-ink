package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func meshAdminStub(t *testing.T, status int, body string) (string, *http.Client, *string, *string) {
	t.Helper()
	var gotKey, gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotKey = r.Header.Get("X-API-Key")
		gotPath = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv.URL, srv.Client(), &gotKey, &gotPath
}

func TestPostMeshAdmin_SendsKeyAndDecodes(t *testing.T) {
	base, hc, key, path := meshAdminStub(t, http.StatusOK, `{"rows_merged":3,"links_merged":1}`)
	got, err := postMeshAdmin(base, "op-secret", "/api/brain/admin/mesh/peers/p1/retry", hc)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if *key != "op-secret" {
		t.Errorf("X-API-Key = %q, want op-secret", *key)
	}
	if *path != "/api/brain/admin/mesh/peers/p1/retry" {
		t.Errorf("path = %q", *path)
	}
	if got.RowsMerged != 3 || got.LinksMerged != 1 {
		t.Errorf("result = %+v, want rows 3 links 1", got)
	}
}

// A missing key must fail BEFORE the request — a silent 403 from the daemon is
// a worse diagnosis than "you never set a key".
func TestPostMeshAdmin_NoKeyShortCircuits(t *testing.T) {
	base, hc, _, path := meshAdminStub(t, http.StatusOK, `{}`)
	_, err := postMeshAdmin(base, "", "/api/brain/admin/mesh/errors/clear", hc)
	if err == nil {
		t.Fatal("want an error with no admin key")
	}
	if !strings.Contains(err.Error(), "admin key") {
		t.Errorf("error should name the missing key, got %q", err)
	}
	if *path != "" {
		t.Errorf("no request should have been sent, but got path %q", *path)
	}
}

func TestPostMeshAdmin_ForbiddenIsTyped(t *testing.T) {
	base, hc, _, _ := meshAdminStub(t, http.StatusForbidden, `{"error":"nope"}`)
	_, err := postMeshAdmin(base, "wrong", "/api/brain/admin/mesh/errors/clear", hc)
	if err == nil {
		t.Fatal("want an error on 403")
	}
	if !strings.Contains(err.Error(), "rejected") {
		t.Errorf("403 should read as a rejected key, got %q", err)
	}
}

func TestPostMeshAdmin_NotFoundIsTyped(t *testing.T) {
	base, hc, _, _ := meshAdminStub(t, http.StatusNotFound, `{"error":"nope"}`)
	_, err := postMeshAdmin(base, "k", "/api/brain/admin/mesh/peers/gone/retry", hc)
	if err == nil {
		t.Fatal("want an error on 404")
	}
	if !strings.Contains(err.Error(), "unknown peer") {
		t.Errorf("404 should read as an unknown peer, got %q", err)
	}
}

// TestPostMeshAdmin_DecodesErrors proves a retry response carrying a non-empty
// errors array decodes into MeshActionResult.Errors — the one thing the retry
// button exists to surface, so a silent drop here defeats its purpose.
func TestPostMeshAdmin_DecodesErrors(t *testing.T) {
	body := `{"rows_merged":0,"links_merged":0,"blobs_fetched":0,"errors":[` +
		`{"at":"2026-10-18T12:00:00Z","peer":"p1","profile":"personal","phase":"pull","class":"network","err":"dial tcp: timeout"}` +
		`]}`
	base, hc, _, _ := meshAdminStub(t, http.StatusOK, body)
	got, err := postMeshAdmin(base, "k", "/api/brain/admin/mesh/peers/p1/retry", hc)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(got.Errors) != 1 {
		t.Fatalf("want 1 error, got %d: %+v", len(got.Errors), got.Errors)
	}
	want := SyncError{
		At:      "2026-10-18T12:00:00Z",
		Peer:    "p1",
		Profile: "personal",
		Phase:   "pull",
		Class:   "network",
		Err:     "dial tcp: timeout",
	}
	if got.Errors[0] != want {
		t.Errorf("got.Errors[0] = %+v, want %+v", got.Errors[0], want)
	}
}
