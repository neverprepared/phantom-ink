package main

import (
	"context"
	"encoding/json"
	"errors"
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

// The three actions must target the exact daemon paths the server mounts. A
// typo here is a 404 at runtime with no compiler help, so pin them.
func TestMeshActionPaths(t *testing.T) {
	cases := []struct {
		name string
		call func(base, key string, hc *http.Client) error
		want string
	}{
		{"retry", func(b, k string, hc *http.Client) error {
			_, err := postMeshAdmin(b, k, meshRetryPath("p1"), hc)
			return err
		}, "/api/brain/admin/mesh/peers/p1/retry"},
		{"reset", func(b, k string, hc *http.Client) error {
			_, err := postMeshAdmin(b, k, meshResetCursorPath("p1"), hc)
			return err
		}, "/api/brain/admin/mesh/peers/p1/reset-cursor"},
		{"clear", func(b, k string, hc *http.Client) error {
			_, err := postMeshAdmin(b, k, meshClearErrorsPath, hc)
			return err
		}, "/api/brain/admin/mesh/errors/clear"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			base, hc, _, path := meshAdminStub(t, http.StatusOK, `{}`)
			if err := tc.call(base, "k", hc); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if *path != tc.want {
				t.Fatalf("path = %q, want %q", *path, tc.want)
			}
		})
	}
}

// url.PathEscape neutralises traversal by escaping the separators, so the
// literal dots survive and a ".." substring check would false-fail on a path
// that is actually safe. The property that matters is that nothing from the
// id escapes its own path segment, so pin the whole string.
func TestMeshRetryPath_EscapesID(t *testing.T) {
	got := meshRetryPath("a/../b")
	want := "/api/brain/admin/mesh/peers/a%2F..%2Fb/retry"
	if got != want {
		t.Fatalf("meshRetryPath(%q) = %q, want %q", "a/../b", got, want)
	}
}

// An OLDER daemon sends no recent_errors / errors_15m / errors_24h. The panel
// must keep working against it, so decoding must leave the new fields zero
// rather than failing. This is the degradation contract in test form.
func TestMeshStatus_DecodesOldDaemonPayload(t *testing.T) {
	const old = `{"node_id":"ncm5","sync_enabled":true,
	  "peers":[{"id":"p1","base_url":"http://x:9998","profile":"personal",
	            "live":true,"db_lag":"0","links_lag":"0","cursor_age":"10h"}],
	  "metrics":{"pb_sync_errors_total":"996"}}`
	var got MeshStatus
	if err := json.Unmarshal([]byte(old), &got); err != nil {
		t.Fatalf("old payload must still decode: %v", err)
	}
	if got.Errors15m != 0 || got.RecentErrors != nil {
		t.Errorf("new fields should be zero, got %d / %v", got.Errors15m, got.RecentErrors)
	}
	if len(got.Peers) != 1 || got.Peers[0].LastError != nil || got.Peers[0].Errors24h != 0 {
		t.Errorf("peer new fields should be zero, got %+v", got.Peers)
	}
	if got.Metrics.ErrorsTotal != "996" {
		t.Errorf("existing fields must still decode, got %q", got.Metrics.ErrorsTotal)
	}
}

// The daemon sends an actionable envelope on every non-2xx; discarding it left
// the operator with a bare status code. Each typed case must carry it through.
func TestPostMeshAdmin_SurfacesDaemonDetail(t *testing.T) {
	cases := []struct {
		name   string
		status int
		body   string
		want   string
	}{
		{"404 envelope", http.StatusNotFound,
			`{"error":{"code":"NOT_FOUND","message":"no configured peer with id bogus"}}`,
			"no configured peer with id bogus"},
		{"403 envelope", http.StatusForbidden,
			`{"error":{"code":"INVALID_TOKEN","message":"admin key mismatch"}}`,
			"admin key mismatch"},
		{"503 envelope", http.StatusServiceUnavailable,
			`{"error":{"code":"UNAVAILABLE","message":"no database for profile gsa"}}`,
			"no database for profile gsa"},
		{"unclassified status", http.StatusBadGateway,
			`{"error":{"code":"BAD_GATEWAY","message":"upstream died"}}`,
			"upstream died"},
		{"non-envelope body falls back to raw", http.StatusBadGateway,
			`plain text failure`, "plain text failure"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			base, hc, _, _ := meshAdminStub(t, tc.status, tc.body)
			_, err := postMeshAdmin(base, "k", "/api/brain/admin/mesh/errors/clear", hc)
			if err == nil {
				t.Fatalf("want an error on %d", tc.status)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error must carry the daemon's detail %q, got %q", tc.want, err)
			}
		})
	}
}

// An empty body must not leave a dangling separator.
func TestPostMeshAdmin_EmptyBodyHasNoDanglingColon(t *testing.T) {
	base, hc, _, _ := meshAdminStub(t, http.StatusForbidden, "")
	_, err := postMeshAdmin(base, "k", "/api/brain/admin/mesh/errors/clear", hc)
	if err == nil {
		t.Fatal("want an error on 403")
	}
	if strings.HasSuffix(err.Error(), ": ") || strings.HasSuffix(err.Error(), ":") {
		t.Errorf("empty body must not leave a dangling separator, got %q", err)
	}
}

// Reporting a slow-but-alive daemon as "unreachable" sends the operator off to
// restart a healthy container, and hides that the action may still be running.
func TestMeshReach_DistinguishesTimeoutFromUnreachable(t *testing.T) {
	to := meshReach(context.DeadlineExceeded, "http://x")
	if !strings.Contains(to, "did not respond in time") {
		t.Errorf("a timeout must read as a timeout, got %q", to)
	}
	if strings.Contains(to, "unreachable") {
		t.Errorf("a timeout must not claim the daemon is unreachable, got %q", to)
	}
	down := meshReach(errors.New("connection refused"), "http://x")
	if !strings.Contains(down, "unreachable") {
		t.Errorf("a non-timeout must read as unreachable, got %q", down)
	}
}
