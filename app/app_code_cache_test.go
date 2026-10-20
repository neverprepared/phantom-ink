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

// Switching profiles must KEEP each profile's rows.
//
// This used to assert the opposite. The memo is profile-keyed, so it cannot
// serve the wrong profile's rows either way -- the wipe only bought the
// cosmetic property that nothing on screen predates a switch, and it cost the
// thing operators actually notice: bouncing between two profiles re-ran the
// whole provider fan-out every single time.
func TestSetActiveProfileKeepsCodeOverviewMemo(t *testing.T) {
	resetCodeOverviewCache()
	storeCodeOverview("work", CodeOverview{Profile: "work"})
	storeCodeOverview("personal", CodeOverview{Profile: "personal"})

	app := &App{config: &Config{}}
	if err := app.SetActiveProfile("personal"); err != nil {
		t.Fatalf("SetActiveProfile: %v", err)
	}

	ov, fresh, ok := cachedCodeOverview("work")
	if !ok {
		t.Fatal("switching away dropped the previous profile's launchpad")
	}
	if !fresh {
		t.Error("the entry survived but was aged out; switching back would refetch")
	}
	if ov.Profile != "work" {
		t.Errorf("Profile = %q, want \"work\"; the wrong entry survived", ov.Profile)
	}

	// And the profile being switched TO keeps its own rows, which is the whole
	// point: switching back and forth paints instantly in both directions.
	if _, _, ok := cachedCodeOverview("personal"); !ok {
		t.Error("the profile being switched to lost its launchpad")
	}
}

// InvalidateCodeOverview with a name drops only that profile.
func TestInvalidateCodeOverviewIsTargeted(t *testing.T) {
	resetCodeOverviewCache()
	storeCodeOverview("alpha", CodeOverview{Profile: "alpha"})
	storeCodeOverview("beta", CodeOverview{Profile: "beta"})

	(&App{}).InvalidateCodeOverview("alpha")

	if _, _, ok := cachedCodeOverview("alpha"); ok {
		t.Error("alpha should have been dropped")
	}
	if _, _, ok := cachedCodeOverview("beta"); !ok {
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

// ── Stale-while-revalidate ────────────────────────────────────────────────
//
// Past the TTL an entry is still perfectly good to DISPLAY: it is what the
// operator was already looking at. Blocking the panel on a provider fan-out
// before it may show anything is what made returning to Work feel slow. The
// contract below is "serve stale, refresh behind it".

// A stale entry is returned, flagged stale rather than withheld.
func TestCachedCodeOverviewServesStaleFlagged(t *testing.T) {
	resetCodeOverviewCache()
	storeCodeOverview("work", CodeOverview{Profile: "work"})
	ageCodeOverview("work", 2*codeOverviewTTL)

	ov, fresh, ok := cachedCodeOverview("work")
	if !ok {
		t.Fatal("a stale entry was withheld; the panel has nothing to paint")
	}
	if fresh {
		t.Error("a stale entry reported itself fresh")
	}
	if ov.Profile != "work" {
		t.Errorf("Profile = %q, want \"work\"", ov.Profile)
	}
}

// A fresh entry reports fresh, so no background refresh is started.
func TestCachedCodeOverviewFreshEntryIsFresh(t *testing.T) {
	resetCodeOverviewCache()
	storeCodeOverview("work", CodeOverview{Profile: "work"})

	if _, fresh, ok := cachedCodeOverview("work"); !ok || !fresh {
		t.Errorf("ok = %v, fresh = %v; want both true", ok, fresh)
	}
}

// A profile with nothing cached reports ok=false — the cold path.
func TestCachedCodeOverviewMissIsNotOK(t *testing.T) {
	resetCodeOverviewCache()
	if _, _, ok := cachedCodeOverview("work"); ok {
		t.Error("an empty cache reported a hit")
	}
}

// Serving stale must return IMMEDIATELY (no provider round-trip on the
// calling goroutine) and must refresh the entry behind the caller.
func TestCodeOverviewServesStaleThenRevalidates(t *testing.T) {
	resetCodeOverviewCache()

	var envCalls atomic.Int64
	app := &App{client: brainbox.NewClient(adoOnlyEnvServer(t, &envCalls).URL, ""), ctx: context.Background()}

	// Prime with a marker we can recognise, then age it past the TTL.
	storeCodeOverview("work", CodeOverview{Profile: "work", ReposError: "stale-marker"})
	ageCodeOverview("work", 2*codeOverviewTTL)

	ov, err := app.CodeOverview("work", false)
	if err != nil {
		t.Fatalf("CodeOverview: %v", err)
	}
	if ov.ReposError != "stale-marker" {
		t.Fatalf("ReposError = %q; the stale entry was not served synchronously", ov.ReposError)
	}
	if !ov.Stale {
		t.Error("the served copy was not marked Stale; the UI cannot show it is refreshing")
	}

	// The revalidate runs behind the caller, so wait for it to land.
	waitFor(t, func() bool {
		_, fresh, ok := cachedCodeOverview("work")
		return ok && fresh
	}, "background revalidate never refreshed the entry")

	if envCalls.Load() == 0 {
		t.Error("the background revalidate issued no provider request")
	}
}

// Concurrent mounts of a stale profile must collapse into ONE revalidate.
// Without single-flight, opening the panel while a tab switch is in flight
// fans the whole provider set out twice.
func TestCodeOverviewRevalidateIsSingleFlight(t *testing.T) {
	resetCodeOverviewCache()

	// Block the env read until released, so every caller piles up inside the
	// same revalidate window.
	release := make(chan struct{})
	var envCalls atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		envCalls.Add(1)
		<-release
		_, _ = w.Write([]byte(`{"profile":"work","env":{"ADO_ORG":"o","ADO_PROJECT":"p","ADO_PAT":"secret"}}`))
	}))
	t.Cleanup(srv.Close)

	app := &App{client: brainbox.NewClient(srv.URL, ""), ctx: context.Background()}
	storeCodeOverview("work", CodeOverview{Profile: "work"})
	ageCodeOverview("work", 2*codeOverviewTTL)

	for i := 0; i < 8; i++ {
		if _, err := app.CodeOverview("work", false); err != nil {
			t.Fatalf("call %d: %v", i, err)
		}
	}
	// All eight were served stale, so at most one revalidate may be in flight.
	waitFor(t, func() bool { return envCalls.Load() >= 1 }, "no revalidate started")
	close(release)

	waitFor(t, func() bool {
		_, fresh, ok := cachedCodeOverview("work")
		return ok && fresh
	}, "revalidate never completed")

	if got := envCalls.Load(); got != 1 {
		t.Errorf("%d revalidates ran; single-flight did not collapse them", got)
	}
}

// A cold miss still blocks — there is nothing to paint, so the caller must
// get real data rather than an empty frame.
func TestCodeOverviewColdMissFetchesSynchronously(t *testing.T) {
	resetCodeOverviewCache()

	var envCalls atomic.Int64
	app := &App{client: brainbox.NewClient(adoOnlyEnvServer(t, &envCalls).URL, ""), ctx: context.Background()}

	ov, err := app.CodeOverview("work", false)
	if err != nil {
		t.Fatalf("CodeOverview: %v", err)
	}
	if envCalls.Load() == 0 {
		t.Error("a cold miss returned without asking any provider")
	}
	if ov.Stale {
		t.Error("a freshly fetched overview was marked Stale")
	}
	if ov.FetchedAt == "" {
		t.Error("FetchedAt is unset; the panel cannot show the data's age")
	}
}

// ageCodeOverview backdates a cached entry so it reads as stale.
func ageCodeOverview(profile string, by time.Duration) {
	codeOverviewMu.Lock()
	defer codeOverviewMu.Unlock()
	e := codeOverviewCache[profile]
	e.at = e.at.Add(-by)
	codeOverviewCache[profile] = e
}

// waitFor polls cond for up to two seconds. The revalidate is deliberately
// off the caller's goroutine, so there is no handle to join on.
func waitFor(t *testing.T, cond func() bool, msg string) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal(msg)
}

// Curating a profile's credentials must drop ITS memo and nobody else's. The
// memo caches the credential verdict (token_missing / token_invalid) next to
// the rows, so a memoised failure would outlive the token that fixed it.
func TestSetGatewayEnvInvalidatesOnlyThatProfile(t *testing.T) {
	resetCodeOverviewCache()
	storeCodeOverview("work", CodeOverview{Profile: "work", TokenInvalid: true})
	storeCodeOverview("personal", CodeOverview{Profile: "personal"})

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{}`))
	}))
	t.Cleanup(srv.Close)
	app := &App{client: brainbox.NewClient(srv.URL, ""), ctx: context.Background()}

	if err := app.SetGatewayEnv("work", map[string]string{"GITHUB_TOKEN": "fresh"}); err != nil {
		t.Fatalf("SetGatewayEnv: %v", err)
	}

	if _, _, ok := cachedCodeOverview("work"); ok {
		t.Error("the edited profile kept a memo describing its OLD credential")
	}
	if _, _, ok := cachedCodeOverview("personal"); !ok {
		t.Error("an unrelated profile lost its memo")
	}
}

// Same contract for clearing a profile's env outright.
func TestDeleteGatewayEnvInvalidatesOnlyThatProfile(t *testing.T) {
	resetCodeOverviewCache()
	storeCodeOverview("work", CodeOverview{Profile: "work"})
	storeCodeOverview("personal", CodeOverview{Profile: "personal"})

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{}`))
	}))
	t.Cleanup(srv.Close)
	app := &App{client: brainbox.NewClient(srv.URL, ""), ctx: context.Background()}

	if err := app.DeleteGatewayEnv("work"); err != nil {
		t.Fatalf("DeleteGatewayEnv: %v", err)
	}

	if _, _, ok := cachedCodeOverview("work"); ok {
		t.Error("the cleared profile kept its memo")
	}
	if _, _, ok := cachedCodeOverview("personal"); !ok {
		t.Error("an unrelated profile lost its memo")
	}
}
