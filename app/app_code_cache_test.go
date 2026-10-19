package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"phantom-ink/brainbox"
)

// The Code panel issues ~6 remote calls per configured ADO project on every
// open, so an auto-discovered org meant ~181 HTTP calls per navigation. The
// memo removes that for plain navigation. Its isolation properties are the
// part that matters, so they are pinned here.

func TestCodeOverviewMemoAvoidsRefetch(t *testing.T) {
	resetCodeOverviewCache()

	var envCalls atomic.Int64
	app := &App{client: brainbox.NewClient(adoOnlyEnvServer(t, &envCalls).URL, ""), ctx: context.Background()}

	if _, err := app.CodeOverview("work", false); err != nil {
		t.Fatalf("first: %v", err)
	}
	after := envCalls.Load()
	if after == 0 {
		t.Fatal("first call made no request at all")
	}

	// Second navigation must be served from the memo.
	if _, err := app.CodeOverview("work", false); err != nil {
		t.Fatalf("second: %v", err)
	}
	if got := envCalls.Load(); got != after {
		t.Errorf("second call issued %d more request(s); the memo did not apply", got-after)
	}
}

// force=true is the refresh button: it must always go to the providers.
func TestCodeOverviewForceBypassesMemo(t *testing.T) {
	resetCodeOverviewCache()

	var envCalls atomic.Int64
	app := &App{client: brainbox.NewClient(adoOnlyEnvServer(t, &envCalls).URL, ""), ctx: context.Background()}

	if _, err := app.CodeOverview("work", false); err != nil {
		t.Fatal(err)
	}
	before := envCalls.Load()
	if _, err := app.CodeOverview("work", true); err != nil {
		t.Fatal(err)
	}
	if envCalls.Load() <= before {
		t.Error("force=true was served from the memo; refresh would show stale data")
	}
}

// The memo is keyed by profile. One profile must never be served another's
// rows — cross-profile leakage is a bug by project rule, and "no cache" was
// the original way this file guaranteed it.
func TestCodeOverviewMemoIsPerProfile(t *testing.T) {
	resetCodeOverviewCache()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Echo back whichever profile was asked for.
		profile := r.URL.Query().Get("profile")
		if profile == "" {
			profile = "unknown"
		}
		_, _ = w.Write([]byte(`{"profile":"` + profile + `","env":{"OTHER":"x"}}`))
	}))
	t.Cleanup(srv.Close)
	app := &App{client: brainbox.NewClient(srv.URL, ""), ctx: context.Background()}

	a, err := app.CodeOverview("alpha", false)
	if err != nil {
		t.Fatal(err)
	}
	b, err := app.CodeOverview("beta", false)
	if err != nil {
		t.Fatal(err)
	}
	if a.Profile == b.Profile {
		t.Errorf("both profiles resolved to %q; the memo is not profile-keyed", a.Profile)
	}
}

// Switching profiles clears everything, so nothing on screen predates the
// switch even within the TTL.
func TestSetActiveProfileClearsCodeOverviewMemo(t *testing.T) {
	resetCodeOverviewCache()
	storeCodeOverview("work", CodeOverview{Profile: "work"})
	if _, ok := cachedCodeOverview("work"); !ok {
		t.Fatal("precondition: entry should be cached")
	}

	app := &App{config: &Config{}}
	if err := app.SetActiveProfile("other"); err != nil {
		t.Fatalf("SetActiveProfile: %v", err)
	}
	if _, ok := cachedCodeOverview("work"); ok {
		t.Error("profile switch left a memoised launchpad behind")
	}
}

// An entry older than the TTL must not be served.
func TestCodeOverviewMemoExpires(t *testing.T) {
	resetCodeOverviewCache()
	storeCodeOverview("work", CodeOverview{Profile: "work"})

	codeOverviewMu.Lock()
	e := codeOverviewCache["work"]
	e.at = time.Now().Add(-2 * codeOverviewTTL)
	codeOverviewCache["work"] = e
	codeOverviewMu.Unlock()

	if _, ok := cachedCodeOverview("work"); ok {
		t.Error("an expired entry was served")
	}
}

// InvalidateCodeOverview with a name drops only that profile.
func TestInvalidateCodeOverviewIsTargeted(t *testing.T) {
	resetCodeOverviewCache()
	storeCodeOverview("alpha", CodeOverview{Profile: "alpha"})
	storeCodeOverview("beta", CodeOverview{Profile: "beta"})

	(&App{}).InvalidateCodeOverview("alpha")

	if _, ok := cachedCodeOverview("alpha"); ok {
		t.Error("alpha should have been dropped")
	}
	if _, ok := cachedCodeOverview("beta"); !ok {
		t.Error("beta should have been left alone")
	}
}

// adoOnlyEnvServer returns a gateway-env server that reports an ADO-only
// profile, counting env reads. ADO calls themselves fail against the fake,
// which is fine: the fan-out records section errors rather than failing.
func adoOnlyEnvServer(t *testing.T, calls *atomic.Int64) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = w.Write([]byte(`{"profile":"work","env":{"ADO_ORG":"o","ADO_PROJECT":"p","ADO_PAT":"secret"}}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func resetCodeOverviewCache() {
	codeOverviewMu.Lock()
	codeOverviewCache = map[string]codeOverviewEntry{}
	codeOverviewMu.Unlock()
}
