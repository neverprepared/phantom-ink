package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func githubStub(t *testing.T, status int) (string, *http.Client, *string) {
	t.Helper()
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		if r.URL.Path != "/rate_limit" {
			t.Errorf("expected /rate_limit, got %s", r.URL.Path)
		}
		w.WriteHeader(status)
	}))
	t.Cleanup(srv.Close)
	return srv.URL, srv.Client(), &gotAuth
}

func TestCheckGitHubToken_Valid(t *testing.T) {
	base, hc, auth := githubStub(t, http.StatusOK)
	got := checkGitHubToken(base, "ghs_good", hc)
	if !got.Valid || !got.Checked {
		t.Fatalf("200 → valid+checked, got %+v", got)
	}
	if *auth != "Bearer ghs_good" {
		t.Errorf("auth header = %q", *auth)
	}
}

func TestCheckGitHubToken_Rejected(t *testing.T) {
	base, hc, _ := githubStub(t, http.StatusUnauthorized)
	got := checkGitHubToken(base, "bad", hc)
	if got.Valid || !got.Checked {
		t.Fatalf("401 → invalid but checked, got %+v", got)
	}
}

func TestCheckGitHubToken_Inconclusive(t *testing.T) {
	base, hc, _ := githubStub(t, http.StatusForbidden)
	got := checkGitHubToken(base, "x", hc)
	if got.Valid || got.Checked {
		t.Fatalf("403 → not valid, not a clean check, got %+v", got)
	}
}

func TestCheckGitHubToken_Empty(t *testing.T) {
	got := checkGitHubToken("http://unused", "", nil)
	if got.Valid || !got.Checked {
		t.Fatalf("empty token → invalid+checked (no network), got %+v", got)
	}
}

func TestCheckGitHubToken_Unreachable(t *testing.T) {
	// closed server → transport error → Checked=false (neutral, not "invalid")
	base, hc, _ := githubStub(t, http.StatusOK)
	// force failure by pointing at an unroutable path on a closed client:
	got := checkGitHubToken("http://127.0.0.1:0", "tok", hc)
	_ = base
	if got.Checked {
		t.Fatalf("transport error must be Checked=false (neutral), got %+v", got)
	}
	if got.Valid {
		t.Fatalf("unreachable must not report valid, got %+v", got)
	}
}
