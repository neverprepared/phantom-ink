package ado

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"phantom-ink/provider"
)

// stubAzToken swaps the az-token command for the duration of a test.
func stubAzToken(t *testing.T, raw []byte, err error) {
	t.Helper()
	orig := azTokenCmd
	azTokenCmd = func(context.Context, string) ([]byte, error) { return raw, err }
	t.Cleanup(func() { azTokenCmd = orig })
}

// far-future epoch so the cached token never looks expired during a test.
const azTokenJSON = `{"accessToken":"tok-abc","expires_on":9999999999}`

func TestAzLogin_SendsBearerAndCaches(t *testing.T) {
	calls := 0
	orig := azTokenCmd
	azTokenCmd = func(context.Context, string) ([]byte, error) {
		calls++
		return []byte(azTokenJSON), nil
	}
	t.Cleanup(func() { azTokenCmd = orig })

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer tok-abc" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(`{"value":[]}`))
	}))
	defer srv.Close()

	c := NewAzLoginWithBase(srv.URL, "acme", "widgets", "", nil)
	if _, err := c.ListRepos(context.Background()); err != nil {
		t.Fatalf("first read: %v", err)
	}
	if _, err := c.ListRepos(context.Background()); err != nil {
		t.Fatalf("second read: %v", err)
	}
	if calls != 1 {
		t.Fatalf("token should be minted once and cached, az called %d times", calls)
	}
}

// The per-profile az config dir must reach the az command (that's what selects
// the right identity when sessions are per-profile).
func TestAzLogin_PassesConfigDir(t *testing.T) {
	var gotDir string
	orig := azTokenCmd
	azTokenCmd = func(_ context.Context, dir string) ([]byte, error) {
		gotDir = dir
		return []byte(azTokenJSON), nil
	}
	t.Cleanup(func() { azTokenCmd = orig })

	c := NewAzLoginWithBase("http://unused.invalid", "acme", "widgets", "/ws/lakeview/.azure", nil)
	_, _ = c.SearchMyPRs(context.Background()) // triggers a mint (will fail on http, that's fine)
	if gotDir != "/ws/lakeview/.azure" {
		t.Fatalf("azConfigDir not threaded to az command: got %q", gotDir)
	}
}

func TestAzLogin_NotLoggedInSurfaces(t *testing.T) {
	stubAzToken(t, nil, errors.New("Please run 'az login' to setup account."))
	c := NewAzLoginWithBase("http://unused.invalid", "acme", "widgets", "", nil)
	_, err := c.ListRepos(context.Background())
	if err == nil || !strings.Contains(err.Error(), "az login") {
		t.Fatalf("want an az login error, got %v", err)
	}
	if provider.IsUnauthorized(err) {
		t.Fatalf("az-not-logged-in is a config error, not a 401")
	}
}

func TestAzAuthHeader(t *testing.T) {
	stubAzToken(t, []byte(`{"accessToken":"tok-xyz","expires_on":9999999999}`), nil)
	h, err := AzAuthHeader(context.Background(), "")
	if err != nil {
		t.Fatal(err)
	}
	if h != "Bearer tok-xyz" {
		t.Fatalf("got %q, want Bearer tok-xyz", h)
	}
}

func TestAzAuthHeader_PropagatesError(t *testing.T) {
	stubAzToken(t, nil, errors.New("az missing"))
	if _, err := AzAuthHeader(context.Background(), ""); err == nil {
		t.Fatal("want error when az fails")
	}
}

// Project names with spaces must be URL-encoded in request paths.
func TestProjectNameEncodedInPath(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.EscapedPath()
		_, _ = w.Write([]byte(`{"value":[]}`))
	}))
	defer srv.Close()

	c := NewWithBase(srv.URL, "acme", "LAKEVIEW ENTERPRISE AUTOMATION", "pat", nil)
	if _, err := c.ListRepos(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(gotPath, "LAKEVIEW%20ENTERPRISE%20AUTOMATION") {
		t.Fatalf("project name not URL-encoded in path: %q", gotPath)
	}
}

func TestListProjects(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/acme/_apis/projects" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_, _ = w.Write([]byte(`{"count":2,"value":[{"name":"cd-originations"},{"name":"LAKEVIEW ENTERPRISE AUTOMATION"}]}`))
	}))
	defer srv.Close()
	// Empty project is fine for the org-level projects call.
	c := NewWithBase(srv.URL, "acme", "", "pat", nil)
	got, err := c.ListProjects(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0] != "cd-originations" || got[1] != "LAKEVIEW ENTERPRISE AUTOMATION" {
		t.Fatalf("unexpected projects: %v", got)
	}
}
