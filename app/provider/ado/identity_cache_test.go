package ado

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

// buildProviders constructs ONE Client per ADO project for the same org and
// credential, so a per-client identity cache still meant one connectionData
// call per project on every Code-panel open — 30 projects, 30 identical
// identity lookups for the same user.
func TestMeIsSharedAcrossClientsForSameOrgAndCredential(t *testing.T) {
	resetIdentityCache()

	var calls atomic.Int64
	srv := identityServer(t, &calls, "user-guid-1")

	// Three clients, as if for three projects in one org.
	for _, project := range []string{"alpha", "beta", "gamma"} {
		c := NewWithBase(srv.URL, "myorg", project, "the-pat", nil)
		id, err := c.me(context.Background())
		if err != nil {
			t.Fatalf("project %s: %v", project, err)
		}
		if id != "user-guid-1" {
			t.Fatalf("project %s: id = %q", project, id)
		}
	}

	if got := calls.Load(); got != 1 {
		t.Errorf("connectionData called %d times across 3 project clients, want 1", got)
	}
}

// Two profiles may target the same org with DIFFERENT credentials. They must
// never share an identity answer — that would be a cross-profile leak.
func TestMeIsNotSharedAcrossDifferentCredentials(t *testing.T) {
	resetIdentityCache()

	var calls atomic.Int64
	srv := identityServerPerAuth(t, &calls)

	a := NewWithBase(srv.URL, "myorg", "proj", "pat-A", nil)
	idA, err := a.me(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	b := NewWithBase(srv.URL, "myorg", "proj", "pat-B", nil)
	idB, err := b.me(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if idA == idB {
		t.Errorf("two different PATs resolved to the same identity %q — the cache key ignores the credential", idA)
	}
	if got := calls.Load(); got != 2 {
		t.Errorf("connectionData called %d times for 2 distinct credentials, want 2", got)
	}
}

// A different org with the same credential is also a different scope.
func TestMeIsNotSharedAcrossOrgs(t *testing.T) {
	resetIdentityCache()

	var calls atomic.Int64
	srv := identityServer(t, &calls, "whoever")

	for _, org := range []string{"org-one", "org-two"} {
		c := NewWithBase(srv.URL, org, "proj", "same-pat", nil)
		if _, err := c.me(context.Background()); err != nil {
			t.Fatalf("org %s: %v", org, err)
		}
	}
	if got := calls.Load(); got != 2 {
		t.Errorf("connectionData called %d times across 2 orgs, want 2", got)
	}
}

// An az-login client is scoped by its session directory, not by a PAT.
func TestMeScopesAzLoginBySessionDir(t *testing.T) {
	resetIdentityCache()
	a := NewAzLoginWithBase("https://example.invalid", "myorg", "proj", "/profiles/gsa/.azure", nil)
	b := NewAzLoginWithBase("https://example.invalid", "myorg", "proj", "/profiles/lakeview/.azure", nil)
	if a.identityKey == b.identityKey {
		t.Error("two az session dirs share an identity key; one profile could serve another's identity")
	}
}

// identityServer answers connectionData with a fixed GUID, counting calls.
func identityServer(t *testing.T, calls *atomic.Int64, guid string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.URL.Path, "connectionData") {
			http.NotFound(w, r)
			return
		}
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"authenticatedUser":{"id":"` + guid + `"}}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

// identityServerPerAuth derives the GUID from the Authorization header, so a
// shared cache across credentials shows up as an identical id.
func identityServerPerAuth(t *testing.T, calls *atomic.Int64) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.URL.Path, "connectionData") {
			http.NotFound(w, r)
			return
		}
		calls.Add(1)
		// Distinct per credential, without echoing the credential itself.
		guid := "guid-for-auth-len-" + itoaLen(r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"authenticatedUser":{"id":"` + guid + `"}}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

// itoaLen returns a stable token derived from the header without revealing it.
func itoaLen(s string) string {
	var sum int
	for _, r := range s {
		sum += int(r)
	}
	return string(rune('a' + sum%26))
}

func resetIdentityCache() {
	identityMu.Lock()
	identityCache = map[string]identityEntry{}
	identityMu.Unlock()
}
