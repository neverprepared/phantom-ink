package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"phantom-ink/brainbox"
)

// fakeBroker is a stand-in for the gateway's env routes that COUNTS reads, so
// a test can assert how many round trips a call actually made -- the whole
// point of the memo being the 1.6s each one costs.
type fakeBroker struct {
	srv *httptest.Server

	mu      sync.Mutex
	env     map[string]map[string]string // profile -> env
	gets    map[string]*int32            // profile -> GET count
	gate    chan struct{}                // when non-nil, GETs block on it
	missing map[string]bool              // profiles that 404
}

func newFakeBroker(t *testing.T) *fakeBroker {
	t.Helper()
	b := &fakeBroker{
		env:     map[string]map[string]string{},
		gets:    map[string]*int32{},
		missing: map[string]bool{},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/gateway/profiles/", func(w http.ResponseWriter, r *http.Request) {
		profile, rest := splitEnvPath(r.URL.Path)
		b.mu.Lock()
		gate := b.gate
		missing := b.missing[profile]
		b.mu.Unlock()

		switch {
		case r.Method == http.MethodGet && rest == "env":
			if gate != nil {
				<-gate // hold the read open so concurrent callers overlap
			}
			b.mu.Lock()
			c, ok := b.gets[profile]
			if !ok {
				var n int32
				c = &n
				b.gets[profile] = c
			}
			env := b.env[profile]
			b.mu.Unlock()
			atomic.AddInt32(c, 1)
			if missing {
				w.WriteHeader(http.StatusNotFound)
				_, _ = w.Write([]byte(`{"detail":"no env"}`))
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"profile": profile, "env": env})
		case r.Method == http.MethodPut && rest == "env":
			var body struct{ Env map[string]string }
			_ = json.NewDecoder(r.Body).Decode(&body)
			b.mu.Lock()
			b.env[profile] = body.Env
			b.mu.Unlock()
			_, _ = w.Write([]byte(`{"ok":true}`))
		case r.Method == http.MethodPut: // /env/<KEY>
			var body struct{ Value string }
			_ = json.NewDecoder(r.Body).Decode(&body)
			key := rest[len("env/"):]
			b.mu.Lock()
			if b.env[profile] == nil {
				b.env[profile] = map[string]string{}
			}
			b.env[profile][key] = body.Value
			b.mu.Unlock()
			_, _ = w.Write([]byte(`{"ok":true}`))
		case r.Method == http.MethodDelete:
			b.mu.Lock()
			delete(b.env, profile)
			b.mu.Unlock()
			_, _ = w.Write([]byte(`{"ok":true}`))
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	})
	b.srv = httptest.NewServer(mux)
	t.Cleanup(b.srv.Close)
	return b
}

// splitEnvPath turns /api/gateway/profiles/<p>/env[/<KEY>] into (p, rest).
func splitEnvPath(path string) (profile, rest string) {
	const prefix = "/api/gateway/profiles/"
	s := path[len(prefix):]
	for i := 0; i < len(s); i++ {
		if s[i] == '/' {
			return s[:i], s[i+1:]
		}
	}
	return s, ""
}

func (b *fakeBroker) setEnv(profile string, env map[string]string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.env[profile] = env
}

func (b *fakeBroker) getCount(profile string) int {
	b.mu.Lock()
	c, ok := b.gets[profile]
	b.mu.Unlock()
	if !ok {
		return 0
	}
	return int(atomic.LoadInt32(c))
}

// hold makes every env GET block until the returned release func is called.
func (b *fakeBroker) hold() (release func()) {
	gate := make(chan struct{})
	b.mu.Lock()
	b.gate = gate
	b.mu.Unlock()
	return func() {
		b.mu.Lock()
		b.gate = nil
		b.mu.Unlock()
		close(gate)
	}
}

// newEnvTestApp wires an App to the fake broker and clears the process-wide
// env memo, which would otherwise leak between tests.
func newEnvTestApp(t *testing.T) (*App, *fakeBroker) {
	t.Helper()
	b := newFakeBroker(t)
	// No memo reset needed: the memo belongs to this App, not the package.
	a := &App{client: brainbox.NewClient(b.srv.URL, "test-key")}
	return a, b
}

func TestProfileEnvServesASecondReadFromTheMemo(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"GITHUB_TOKEN": "t1"})

	first, err := a.profileEnv("work")
	if err != nil {
		t.Fatalf("first read: %v", err)
	}
	second, err := a.profileEnv("work")
	if err != nil {
		t.Fatalf("second read: %v", err)
	}

	if first["GITHUB_TOKEN"] != "t1" || second["GITHUB_TOKEN"] != "t1" {
		t.Fatalf("env not served: %v / %v", first, second)
	}
	if n := b.getCount("work"); n != 1 {
		t.Fatalf("broker reads = %d, want 1 (the second read must come from the memo)", n)
	}
}

func TestProfileEnvRefetchesOnceStale(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"K": "v1"})

	if _, err := a.profileEnv("work"); err != nil {
		t.Fatalf("first read: %v", err)
	}
	// An env changed out of band (ansible, pbrainctl, another node) is
	// invisible to the app, so the entry must age out rather than pin forever.
	b.setEnv("work", map[string]string{"K": "v2"})
	a.envMemo().age("work", profileEnvTTL+time.Second)

	got, err := a.profileEnv("work")
	if err != nil {
		t.Fatalf("stale read: %v", err)
	}
	if got["K"] != "v2" {
		t.Fatalf("K = %q, want v2 from the refetch", got["K"])
	}
	if n := b.getCount("work"); n != 2 {
		t.Fatalf("broker reads = %d, want 2", n)
	}
}

func TestProfileEnvCoalescesConcurrentColdReads(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"K": "v"})
	release := b.hold()

	const readers = 8
	var wg sync.WaitGroup
	got := make([]map[string]string, readers)
	errs := make([]error, readers)
	for i := 0; i < readers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			got[i], errs[i] = a.profileEnv("work")
		}(i)
	}
	// Let every reader reach the memo before the broker answers anyone.
	time.Sleep(50 * time.Millisecond)
	release()
	wg.Wait()

	for i := range got {
		if errs[i] != nil {
			t.Fatalf("reader %d: %v", i, errs[i])
		}
		if got[i]["K"] != "v" {
			t.Fatalf("reader %d got %v", i, got[i])
		}
	}
	// This is the behaviour the change exists for: a panel mount fires several
	// of these at once, and each uncoalesced miss is a 1.6s round trip.
	if n := b.getCount("work"); n != 1 {
		t.Fatalf("broker reads = %d, want 1 for %d concurrent cold readers", n, readers)
	}
}

func TestProfileEnvIsPerProfile(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"TOKEN": "work-token"})
	b.setEnv("home", map[string]string{"TOKEN": "home-token"})

	work, err := a.profileEnv("work")
	if err != nil {
		t.Fatalf("work: %v", err)
	}
	home, err := a.profileEnv("home")
	if err != nil {
		t.Fatalf("home: %v", err)
	}

	if work["TOKEN"] != "work-token" || home["TOKEN"] != "home-token" {
		t.Fatalf("profiles crossed: work=%v home=%v", work, home)
	}
}

func TestProfileEnvReturnsACopyCallersCannotPoison(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"GITHUB_TOKEN": "t1"})

	first, err := a.profileEnv("work")
	if err != nil {
		t.Fatalf("first: %v", err)
	}
	// SetGatewayEnv deletes keys from the map it is handed, and refreshProfileEnv
	// builds its write from one, so a shared map would corrupt the cache.
	delete(first, "GITHUB_TOKEN")
	first["INJECTED"] = "x"

	second, err := a.profileEnv("work")
	if err != nil {
		t.Fatalf("second: %v", err)
	}
	if second["GITHUB_TOKEN"] != "t1" {
		t.Fatal("a caller's delete reached the memo")
	}
	if _, ok := second["INJECTED"]; ok {
		t.Fatal("a caller's write reached the memo")
	}
}

func TestProfileEnvNormalizesAMissingStoreToEmpty(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.mu.Lock()
	b.missing["fresh"] = true
	b.mu.Unlock()

	env, err := a.profileEnv("fresh")
	if err != nil {
		t.Fatalf("missing store should not error: %v", err)
	}
	if len(env) != 0 {
		t.Fatalf("env = %v, want empty", env)
	}
}

func TestGetGatewayEnvAlwaysReadsLive(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"K": "v1"})

	if _, err := a.GetGatewayEnv("work"); err != nil {
		t.Fatalf("first: %v", err)
	}
	b.setEnv("work", map[string]string{"K": "v2"})
	got, err := a.GetGatewayEnv("work")
	if err != nil {
		t.Fatalf("second: %v", err)
	}

	// The editor saves a FULL OVERWRITE of what it was shown, so showing it a
	// memoised snapshot would let it clobber a value written in between.
	if got["K"] != "v2" {
		t.Fatalf("K = %q, want v2: the bound reader must not be memoised", got["K"])
	}
	if n := b.getCount("work"); n != 2 {
		t.Fatalf("broker reads = %d, want 2", n)
	}
}

func TestSetGatewayEnvDropsTheMemoForThatProfileOnly(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"K": "v1"})
	b.setEnv("home", map[string]string{"K": "home"})
	if _, err := a.profileEnv("work"); err != nil {
		t.Fatalf("warm work: %v", err)
	}
	if _, err := a.profileEnv("home"); err != nil {
		t.Fatalf("warm home: %v", err)
	}

	if err := a.SetGatewayEnv("work", map[string]string{"K": "v2"}); err != nil {
		t.Fatalf("SetGatewayEnv: %v", err)
	}

	got, err := a.profileEnv("work")
	if err != nil {
		t.Fatalf("reread work: %v", err)
	}
	if got["K"] != "v2" {
		t.Fatalf("K = %q, want v2: the write must drop the memo", got["K"])
	}
	if _, err := a.profileEnv("home"); err != nil {
		t.Fatalf("reread home: %v", err)
	}
	if n := b.getCount("home"); n != 1 {
		t.Fatalf("home broker reads = %d, want 1: another profile's write must not drop it", n)
	}
}

func TestDeleteGatewayEnvDropsTheMemo(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"K": "v1"})
	if _, err := a.profileEnv("work"); err != nil {
		t.Fatalf("warm: %v", err)
	}

	if err := a.DeleteGatewayEnv("work"); err != nil {
		t.Fatalf("DeleteGatewayEnv: %v", err)
	}

	got, err := a.profileEnv("work")
	if err != nil {
		t.Fatalf("reread: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("env = %v, want empty after a delete", got)
	}
}

func TestRefreshProfileEnvDropsTheMemo(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"K": "old"})
	if _, err := a.profileEnv("work"); err != nil {
		t.Fatalf("warm: %v", err)
	}

	// The background watcher rewrites values with no operator action at all,
	// so a stale memo here serves a token the app itself just replaced.
	home := t.TempDir()
	if err := os.WriteFile(filepath.Join(home, ".env"), []byte("K=new\n"), 0o600); err != nil {
		t.Fatalf("write host env: %v", err)
	}
	changed, err := a.refreshProfileEnv("work", home)
	if err != nil {
		t.Fatalf("refreshProfileEnv: %v", err)
	}
	if len(changed) != 1 || changed[0] != "K" {
		t.Fatalf("changed = %v, want [K]", changed)
	}

	got, err := a.profileEnv("work")
	if err != nil {
		t.Fatalf("reread: %v", err)
	}
	if got["K"] != "new" {
		t.Fatalf("K = %q, want new", got["K"])
	}
}

func TestPublishProfileEnvKeyDropsTheMemo(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"K": "v"})
	if _, err := a.profileEnv("work"); err != nil {
		t.Fatalf("warm: %v", err)
	}

	// The image build publishes a fresh PROFILE_ENV_KEY on every rebuild; a
	// memo that kept the old one hands sessions a key that cannot decrypt.
	if err := a.publishGatewayEnvKey("work", "PROFILE_ENV_KEY", "k2"); err != nil {
		t.Fatalf("publishGatewayEnvKey: %v", err)
	}

	got, err := a.profileEnv("work")
	if err != nil {
		t.Fatalf("reread: %v", err)
	}
	if got["PROFILE_ENV_KEY"] != "k2" {
		t.Fatalf("PROFILE_ENV_KEY = %q, want k2", got["PROFILE_ENV_KEY"])
	}
}

// TestJiraTabReadsShareOneEnvRoundTrip is the behaviour the memo exists for.
// Opening the Jira tab fires three reads that each used to start with its own
// 1.6s env round trip: the settings card's status, the ticket list, and the
// code fan-out the ticket rows join against.
func TestJiraTabReadsShareOneEnvRoundTrip(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"K": "v"})
	codeOverviewMemo.invalidate("")
	jiraTicketsMemo.invalidate("")
	t.Cleanup(func() {
		codeOverviewMemo.invalidate("")
		jiraTicketsMemo.invalidate("")
	})

	if _, err := a.JiraStatus("work"); err != nil {
		t.Fatalf("JiraStatus: %v", err)
	}
	if _, err := a.jiraTicketsUncached("work"); err != nil {
		t.Fatalf("jiraTicketsUncached: %v", err)
	}
	// force=false is what the panel actually does on mount (codeState.refresh);
	// only the refresh button forces, and that deliberately re-reads the env.
	if _, err := a.CodeOverview("work", false); err != nil {
		t.Fatalf("CodeOverview: %v", err)
	}

	if n := b.getCount("work"); n != 1 {
		t.Fatalf("env round trips = %d, want 1 across the tab's three reads", n)
	}
}

// The refresh button is also how an operator says "I just fixed my token".
// Serving it a memoised env would make the fix look like it did not work.
func TestForcedRefreshRereadsTheEnv(t *testing.T) {
	a, b := newEnvTestApp(t)
	b.setEnv("work", map[string]string{"K": "v1"})
	jiraTicketsMemo.invalidate("")
	codeOverviewMemo.invalidate("")
	t.Cleanup(func() {
		jiraTicketsMemo.invalidate("")
		codeOverviewMemo.invalidate("")
	})

	if _, err := a.JiraTickets("work", false); err != nil {
		t.Fatalf("warm: %v", err)
	}
	if n := b.getCount("work"); n != 1 {
		t.Fatalf("warm reads = %d, want 1", n)
	}

	if _, err := a.JiraTickets("work", true); err != nil {
		t.Fatalf("forced: %v", err)
	}
	if n := b.getCount("work"); n != 2 {
		t.Fatalf("env reads = %d, want 2: force must re-read the env", n)
	}

	if _, err := a.CodeOverview("work", true); err != nil {
		t.Fatalf("forced overview: %v", err)
	}
	if n := b.getCount("work"); n != 3 {
		t.Fatalf("env reads = %d, want 3: force must re-read the env", n)
	}
}
