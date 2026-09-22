# Jira Integration (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show live Jira issue status next to the pull requests already listed in the Code panel, with links derived from PR titles rather than stored anywhere.

**Architecture:** A new `phantom-ink/jira` package parses issue keys out of text (pure, no I/O), filters them against the real project list, and resolves them through one batched JQL call behind a TTL cache. `CodeOverview` gains three fields carrying the resolved issues; a Jira failure sets an error string and never blanks the git rows. Enablement is three gates: global credentials, an Integrations on/off, and an explicit per-profile opt-in stored in SQLite.

**Tech Stack:** Go 1.25 (module `phantom-ink`), stdlib `net/http` + `regexp` + `sync`, SQLite via the existing `DB.GetSetting/SetSetting`, Svelte 5 frontend, Wails v2 binding.

**Spec:** `docs/superpowers/specs/2026-09-22-jira-integration-design.md`

## Global Constraints

- Go module is `phantom-ink`; import the new package as `phantom-ink/jira`.
- Jira REST API v3. Base URL comes from `JIRA_URL`, auth is HTTP Basic with `JIRA_USERNAME` + `JIRA_API_TOKEN`.
- A Jira failure is NEVER fatal to the Code panel: git rows must render exactly as they do today, with the failure reported in `CodeOverview.JiraError`. Same fail-soft idiom as the existing `ReposError` / `PullRequestsError`.
- `provider.Item` MUST NOT be modified. It is the git-provider contract and Jira is not a git provider.
- Issue-status cache TTL: 5 minutes. Project-key cache TTL: 24 hours.
- Per-profile opt-in defaults to OFF. An opted-out profile gets no chips, no panel, no `atlassian` MCP server.
- Jira credentials are stored app-level in SQLite, NOT read from a profile's env. This sidesteps the `profileAzureConfigDir` expansion bug in the spec by construction — there is no env string to expand. Do not add an env fallback.
- The global on/off uses the EXISTING app `integrations` table (`DB.GetIntegration` / `DB.UpsertIntegration`), the same registry every other integration uses. Do not invent a parallel settings key for it.
- `errNoDB` is the single sentinel for "store not ready". Never `errors.New` a new message for that condition.
- Tests run with `cd app && go test ./... -race`.

---

### Task 1: Issue-key parser

**Files:**
- Create: `app/jira/keys.go`
- Test: `app/jira/keys_test.go`

**Interfaces:**
- Consumes: nothing.
- Produces: `func ParseKeys(text string) []string` — returns deduped issue keys in first-appearance order. `func FilterKeys(keys []string, known map[string]bool) []string` — drops keys whose project prefix is not in `known`; a nil/empty `known` map returns nil (fail-closed: never show unverified chips).

- [ ] **Step 1: Write the failing test**

```go
package jira

import (
	"reflect"
	"testing"
)

func TestParseKeys(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want []string
	}{
		{"simple", "ABC-123: add webhook", []string{"ABC-123"}},
		{"branch", "feature/ABC-123-add-webhook", []string{"ABC-123"}},
		{"multiple", "ABC-123 and DEF-9 both", []string{"ABC-123", "DEF-9"}},
		{"dedupe keeps first order", "DEF-9 then ABC-1 then DEF-9", []string{"DEF-9", "ABC-1"}},
		{"digits in project key", "AB2C-7 ships", []string{"AB2C-7"}},
		{"none", "bump deps", nil},
		// False positives the bare regex would otherwise match. These are why
		// FilterKeys exists, but the parser should not invent keys either.
		{"lowercase is not a key", "abc-123 nope", nil},
		{"no leading digit project", "1AB-2 nope", nil},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := ParseKeys(tt.in)
			if !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("ParseKeys(%q) = %v, want %v", tt.in, got, tt.want)
			}
		})
	}
}

func TestParseKeysMatchesKnownFalsePositives(t *testing.T) {
	// The regex DOES match these; FilterKeys is what removes them. Locking the
	// behaviour in so nobody "fixes" the regex and breaks real keys instead.
	for _, s := range []string{"UTF-8", "SHA-256", "ADR-003", "CVE-2024-1234", "PR-1"} {
		if got := ParseKeys(s); len(got) == 0 {
			t.Fatalf("ParseKeys(%q) = empty; expected the regex to match (filtering is FilterKeys' job)", s)
		}
	}
}

func TestFilterKeys(t *testing.T) {
	known := map[string]bool{"ABC": true, "DEF": true}
	got := FilterKeys([]string{"ABC-123", "UTF-8", "DEF-9", "CVE-2024-1234"}, known)
	want := []string{"ABC-123", "DEF-9"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("FilterKeys = %v, want %v", got, want)
	}
}

func TestFilterKeysFailsClosed(t *testing.T) {
	if got := FilterKeys([]string{"ABC-123"}, nil); got != nil {
		t.Fatalf("FilterKeys with nil known = %v, want nil (fail closed)", got)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./jira/ -run TestParseKeys -v`
Expected: FAIL — build error, `undefined: ParseKeys`.

- [ ] **Step 3: Write minimal implementation**

```go
// Package jira derives Jira issue links from text the Code panel already has
// (pull-request titles, branch names) and resolves them against the Jira REST
// API. Links are DERIVED, never stored: there is no index to drift.
package jira

import (
	"regexp"
	"strings"
)

// keyRe matches a Jira issue key: an uppercase project key (letter first, then
// letters/digits) then a dash then digits. It deliberately over-matches —
// UTF-8, SHA-256 and ADR-003 all satisfy it. FilterKeys, not this regex, is
// what removes them, because tightening the pattern would also drop real
// project keys.
var keyRe = regexp.MustCompile(`\b[A-Z][A-Z0-9]*-\d+\b`)

// ParseKeys returns the issue keys in text, deduped, in first-appearance order.
func ParseKeys(text string) []string {
	matches := keyRe.FindAllString(text, -1)
	if len(matches) == 0 {
		return nil
	}
	seen := make(map[string]bool, len(matches))
	out := make([]string, 0, len(matches))
	for _, m := range matches {
		if seen[m] {
			continue
		}
		seen[m] = true
		out = append(out, m)
	}
	return out
}

// FilterKeys drops keys whose project prefix is not a real Jira project.
// A nil or empty known set returns nil: without a verified project list we
// show nothing rather than a panel full of chips for issues that don't exist.
func FilterKeys(keys []string, known map[string]bool) []string {
	if len(known) == 0 {
		return nil
	}
	var out []string
	for _, k := range keys {
		if i := strings.LastIndex(k, "-"); i > 0 && known[k[:i]] {
			out = append(out, k)
		}
	}
	return out
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./jira/ -race -v`
Expected: PASS, all four test functions.

- [ ] **Step 5: Commit**

```bash
git add app/jira/keys.go app/jira/keys_test.go
git commit -m "feat(jira): derive issue keys from PR titles and branch names

The regex over-matches (UTF-8, SHA-256, ADR-003 all look like keys); a
project allowlist removes them rather than a tighter pattern, which would
also drop real project keys."
```

---

### Task 2: TTL cache

**Files:**
- Create: `app/jira/cache.go`
- Test: `app/jira/cache_test.go`

**Interfaces:**
- Consumes: nothing.
- Produces: `type cache[T any] struct` with `newCache[T any](ttl time.Duration) *cache[T]`, `func (c *cache[T]) get(key string) (T, bool)` and `func (c *cache[T]) put(key string, v T)`. Unexported — only the Client uses it.

- [ ] **Step 1: Write the failing test**

```go
package jira

import (
	"sync"
	"testing"
	"time"
)

func TestCacheHitAndMiss(t *testing.T) {
	c := newCache[string](time.Minute)
	if _, ok := c.get("a"); ok {
		t.Fatal("empty cache returned a hit")
	}
	c.put("a", "value")
	got, ok := c.get("a")
	if !ok || got != "value" {
		t.Fatalf("get = (%q, %v), want (\"value\", true)", got, ok)
	}
}

func TestCacheExpires(t *testing.T) {
	c := newCache[string](time.Millisecond)
	c.put("a", "value")
	time.Sleep(5 * time.Millisecond)
	if _, ok := c.get("a"); ok {
		t.Fatal("expired entry still returned a hit")
	}
}

func TestCacheConcurrent(t *testing.T) {
	// Guards against a map-write race; meaningful only under -race.
	c := newCache[int](time.Minute)
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			c.put("k", i)
			c.get("k")
		}(i)
	}
	wg.Wait()
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./jira/ -run TestCache -v`
Expected: FAIL — `undefined: newCache`.

- [ ] **Step 3: Write minimal implementation**

```go
package jira

import (
	"sync"
	"time"
)

type entry[T any] struct {
	val T
	exp time.Time
}

// cache is a small TTL map. Deliberately in-process and unexported: this
// serves one panel's lookups and is not a platform cache primitive.
type cache[T any] struct {
	mu  sync.RWMutex
	ttl time.Duration
	m   map[string]entry[T]
}

func newCache[T any](ttl time.Duration) *cache[T] {
	return &cache[T]{ttl: ttl, m: make(map[string]entry[T])}
}

func (c *cache[T]) get(key string) (T, bool) {
	c.mu.RLock()
	e, ok := c.m[key]
	c.mu.RUnlock()
	var zero T
	if !ok || time.Now().After(e.exp) {
		return zero, false
	}
	return e.val, true
}

func (c *cache[T]) put(key string, v T) {
	c.mu.Lock()
	c.m[key] = entry[T]{val: v, exp: time.Now().Add(c.ttl)}
	c.mu.Unlock()
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./jira/ -race -v`
Expected: PASS, including the Task 1 tests.

- [ ] **Step 5: Commit**

```bash
git add app/jira/cache.go app/jira/cache_test.go
git commit -m "feat(jira): in-process TTL cache for issue and project lookups

Scoped to this package on purpose — repeated Jira reads are slow, but one
panel's needs should not design a platform-wide cache primitive."
```

---

### Task 3: Jira REST client

**Files:**
- Create: `app/jira/client.go`
- Test: `app/jira/client_test.go`

**Interfaces:**
- Consumes: `ParseKeys`, `FilterKeys` (Task 1), `newCache` (Task 2).
- Produces:
  - `type Issue struct { Key, Summary, Status, StatusCategory, Assignee, URL string }`
  - `type Config struct { BaseURL, Username, Token string }`
  - `func New(cfg Config, hc *http.Client) *Client`
  - `func (c *Client) Verify(ctx context.Context) error` — `GET /rest/api/3/myself`, used by the Integrations status check.
  - `func (c *Client) ProjectKeys(ctx context.Context) (map[string]bool, error)` — `GET /rest/api/3/project`, cached 24h.
  - `func (c *Client) Resolve(ctx context.Context, keys []string) (map[string]Issue, error)` — filters against `ProjectKeys`, serves cache hits, fetches the remainder in ONE `POST /rest/api/3/search/jql` with `key in (...)`, caches results 5m.

- [ ] **Step 1: Write the failing test**

```go
package jira

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func testServer(t *testing.T, searchHits int) (*httptest.Server, *int) {
	t.Helper()
	searchCalls := 0
	mux := http.NewServeMux()
	mux.HandleFunc("/rest/api/3/myself", func(w http.ResponseWriter, r *http.Request) {
		if u, p, ok := r.BasicAuth(); !ok || u != "me@example.com" || p != "tok" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		json.NewEncoder(w).Encode(map[string]string{"accountId": "1"})
	})
	mux.HandleFunc("/rest/api/3/project", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]map[string]string{{"key": "ABC"}, {"key": "DEF"}})
	})
	mux.HandleFunc("/rest/api/3/search/jql", func(w http.ResponseWriter, r *http.Request) {
		searchCalls++
		var body struct {
			JQL string `json:"jql"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		if !strings.Contains(body.JQL, "ABC-123") {
			t.Errorf("jql missing ABC-123: %q", body.JQL)
		}
		json.NewEncoder(w).Encode(map[string]any{
			"issues": []map[string]any{{
				"key": "ABC-123",
				"fields": map[string]any{
					"summary":  "add webhook receiver",
					"status":   map[string]any{"name": "In Progress", "statusCategory": map[string]any{"key": "indeterminate"}},
					"assignee": map[string]any{"displayName": "Curtis"},
				},
			}},
		})
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv, &searchCalls
}

func newTestClient(t *testing.T, base string) *Client {
	t.Helper()
	return New(Config{BaseURL: base, Username: "me@example.com", Token: "tok"}, http.DefaultClient)
}

func TestVerify(t *testing.T) {
	srv, _ := testServer(t, 1)
	if err := newTestClient(t, srv.URL).Verify(context.Background()); err != nil {
		t.Fatalf("Verify: %v", err)
	}
}

func TestVerifyRejectsBadCredentials(t *testing.T) {
	srv, _ := testServer(t, 1)
	c := New(Config{BaseURL: srv.URL, Username: "wrong", Token: "wrong"}, http.DefaultClient)
	if err := c.Verify(context.Background()); err == nil {
		t.Fatal("Verify with bad credentials returned nil error")
	}
}

func TestResolveFiltersUnknownProjects(t *testing.T) {
	srv, _ := testServer(t, 1)
	c := newTestClient(t, srv.URL)
	got, err := c.Resolve(context.Background(), []string{"ABC-123", "UTF-8", "CVE-2024-1234"})
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("Resolve returned %d issues, want 1: %v", len(got), got)
	}
	iss := got["ABC-123"]
	if iss.Summary != "add webhook receiver" || iss.Status != "In Progress" || iss.Assignee != "Curtis" {
		t.Fatalf("issue not mapped: %+v", iss)
	}
	if iss.URL != srv.URL+"/browse/ABC-123" {
		t.Fatalf("URL = %q", iss.URL)
	}
}

func TestResolveCachesIssues(t *testing.T) {
	srv, calls := testServer(t, 1)
	c := newTestClient(t, srv.URL)
	ctx := context.Background()
	c.Resolve(ctx, []string{"ABC-123"})
	c.Resolve(ctx, []string{"ABC-123"})
	if *calls != 1 {
		t.Fatalf("search called %d times, want 1 (second should hit cache)", *calls)
	}
}

func TestResolveEmptyKeysMakesNoCall(t *testing.T) {
	srv, calls := testServer(t, 1)
	c := newTestClient(t, srv.URL)
	got, err := c.Resolve(context.Background(), nil)
	if err != nil || len(got) != 0 {
		t.Fatalf("Resolve(nil) = (%v, %v)", got, err)
	}
	if *calls != 0 {
		t.Fatalf("search called %d times for empty input, want 0", *calls)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./jira/ -run TestResolve -v`
Expected: FAIL — `undefined: New`, `undefined: Client`, `undefined: Config`.

- [ ] **Step 3: Write minimal implementation**

```go
package jira

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// Issue is the flattened subset of a Jira issue the Code panel renders.
type Issue struct {
	Key            string `json:"key"`
	Summary        string `json:"summary"`
	Status         string `json:"status"`
	StatusCategory string `json:"status_category"` // new | indeterminate | done
	Assignee       string `json:"assignee"`
	URL            string `json:"url"`
}

// Config is the one app-level credential set. Not per profile: profiles opt in
// to using it, they do not each carry their own copy.
type Config struct {
	BaseURL  string
	Username string
	Token    string
}

type Client struct {
	cfg      Config
	hc       *http.Client
	issues   *cache[Issue]
	projects *cache[map[string]bool]
}

func New(cfg Config, hc *http.Client) *Client {
	if hc == nil {
		hc = &http.Client{Timeout: 15 * time.Second}
	}
	cfg.BaseURL = strings.TrimRight(strings.TrimSpace(cfg.BaseURL), "/")
	return &Client{
		cfg:      cfg,
		hc:       hc,
		issues:   newCache[Issue](5 * time.Minute),
		projects: newCache[map[string]bool](24 * time.Hour),
	}
}

func (c *Client) do(ctx context.Context, method, path string, body any, out any) error {
	var r *bytes.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return err
		}
		r = bytes.NewReader(b)
	} else {
		r = bytes.NewReader(nil)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.cfg.BaseURL+path, r)
	if err != nil {
		return err
	}
	req.SetBasicAuth(c.cfg.Username, c.cfg.Token)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return fmt.Errorf("jira %s %s: %s", method, path, resp.Status)
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// Verify checks the credentials. Used by the Integrations status probe in place
// of the compose-ps check a hosted integration would run.
func (c *Client) Verify(ctx context.Context) error {
	return c.do(ctx, http.MethodGet, "/rest/api/3/myself", nil, nil)
}

// ProjectKeys returns the set of real project keys, cached for 24h.
func (c *Client) ProjectKeys(ctx context.Context) (map[string]bool, error) {
	if v, ok := c.projects.get("all"); ok {
		return v, nil
	}
	var raw []struct {
		Key string `json:"key"`
	}
	if err := c.do(ctx, http.MethodGet, "/rest/api/3/project", nil, &raw); err != nil {
		return nil, err
	}
	set := make(map[string]bool, len(raw))
	for _, p := range raw {
		set[p.Key] = true
	}
	c.projects.put("all", set)
	return set, nil
}

// Resolve maps issue keys to issues. Keys from unknown projects are dropped,
// cached issues are served locally, and whatever remains is fetched in ONE
// batched JQL call.
func (c *Client) Resolve(ctx context.Context, keys []string) (map[string]Issue, error) {
	out := make(map[string]Issue)
	if len(keys) == 0 {
		return out, nil
	}
	known, err := c.ProjectKeys(ctx)
	if err != nil {
		return nil, err
	}
	var want []string
	for _, k := range FilterKeys(keys, known) {
		if iss, ok := c.issues.get(k); ok {
			out[k] = iss
			continue
		}
		want = append(want, k)
	}
	if len(want) == 0 {
		return out, nil
	}
	body := map[string]any{
		"jql":        fmt.Sprintf("key in (%s)", strings.Join(want, ",")),
		"fields":     []string{"summary", "status", "assignee"},
		"maxResults": len(want),
	}
	var raw struct {
		Issues []struct {
			Key    string `json:"key"`
			Fields struct {
				Summary string `json:"summary"`
				Status  struct {
					Name           string `json:"name"`
					StatusCategory struct {
						Key string `json:"key"`
					} `json:"statusCategory"`
				} `json:"status"`
				Assignee struct {
					DisplayName string `json:"displayName"`
				} `json:"assignee"`
			} `json:"fields"`
		} `json:"issues"`
	}
	if err := c.do(ctx, http.MethodPost, "/rest/api/3/search/jql", body, &raw); err != nil {
		return nil, err
	}
	for _, i := range raw.Issues {
		iss := Issue{
			Key:            i.Key,
			Summary:        i.Fields.Summary,
			Status:         i.Fields.Status.Name,
			StatusCategory: i.Fields.Status.StatusCategory.Key,
			Assignee:       i.Fields.Assignee.DisplayName,
			URL:            c.cfg.BaseURL + "/browse/" + i.Key,
		}
		c.issues.put(iss.Key, iss)
		out[iss.Key] = iss
	}
	return out, nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./jira/ -race -v`
Expected: PASS, all tests in the package.

- [ ] **Step 5: Commit**

```bash
git add app/jira/client.go app/jira/client_test.go
git commit -m "feat(jira): REST client resolving keys in one batched JQL call

Per-key lookups would be N round trips against a slow API; one key in (...)
query per page keeps the Code panel's cost flat as PR count grows."
```

---

### Task 4: Enablement gates

**Files:**
- Modify: `app/config.go:10-33` (setting key constants)
- Create: `app/app_jira.go`
- Test: `app/app_jira_test.go`

**Why the integrations table:** `DB.GetIntegration(name)` already backs every other integration's on/off. Jira is a `saas` integration — no compose stack to place — so `enabled` means "operator turned it on", `remote_url` holds the site URL, and the credential pair lives in `settings`. Three gates: integration enabled, credentials complete, profile opted in.

**Interfaces:**
- Consumes: `jira.Config`, `jira.New`, `jira.Client.Verify` (Task 3).
- Produces:
  - `func (a *App) JiraEnabledForProfile(profile string) bool` — true only when global credentials are complete AND the profile opted in.
  - `func (a *App) SetJiraEnabledForProfile(profile string, enabled bool) error` — Wails-bound toggle.
  - `func (a *App) GetJiraSettings() map[string]string` — returns `url` and `username`; NEVER the token (same shape as `GetRegistrySettings`).
  - `func (a *App) SetJiraSettings(url, username, token string) error`
  - `func (a *App) jiraClientFor(profile string) *jira.Client` — nil when the profile is not enabled.
  - `func (a *App) VerifyJira() error` — Integrations status probe.
- Setting keys: `settingJiraUsername = "jira_username"`, `settingJiraToken = "jira_token"`, `settingJiraProfilePrefix = "jira_enabled:"`. The Jira site URL is NOT a setting key — it is the `remote_url` of the `jira` row in the existing `integrations` table.

- [ ] **Step 1: Write the failing test**

```go
package main

import (
	"database/sql"
	"testing"
)

// newJiraTestApp builds an App over an in-memory SQLite with just the two
// tables these gates touch. Mirrors the &App{db: &DB{conn: ...}} construction
// already used by app_conformance_test.go — there is no NewDB constructor.
func newJiraTestApp(t *testing.T) *App {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	conn.SetMaxOpenConns(1)
	t.Cleanup(func() { conn.Close() })
	if _, err := conn.Exec(`
		CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
		CREATE TABLE integrations (
			name       TEXT PRIMARY KEY,
			enabled    INTEGER NOT NULL DEFAULT 0,
			remote     INTEGER NOT NULL DEFAULT 0,
			local_url  TEXT NOT NULL DEFAULT '',
			remote_url TEXT NOT NULL DEFAULT ''
		);`); err != nil {
		t.Fatalf("schema: %v", err)
	}
	return &App{db: &DB{conn: conn}}
}

func TestJiraDisabledWhenIntegrationOff(t *testing.T) {
	a := newJiraTestApp(t)
	// Credentials present, profile opted in, but the operator has NOT enabled
	// the integration: the global gate alone must keep it off.
	if err := a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "tok", false); err != nil {
		t.Fatalf("SetJiraSettings: %v", err)
	}
	if err := a.SetJiraEnabledForProfile("work", true); err != nil {
		t.Fatalf("SetJiraEnabledForProfile: %v", err)
	}
	if a.JiraEnabledForProfile("work") {
		t.Fatal("enabled while the integration itself is off")
	}
}

func TestJiraDisabledWhenCredentialsIncomplete(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "", true); err != nil {
		t.Fatalf("SetJiraSettings: %v", err)
	}
	a.SetJiraEnabledForProfile("work", true)
	if a.JiraEnabledForProfile("work") {
		t.Fatal("enabled with an empty token")
	}
}

func TestJiraDisabledUntilProfileOptsIn(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "tok", true); err != nil {
		t.Fatalf("SetJiraSettings: %v", err)
	}
	if a.JiraEnabledForProfile("work") {
		t.Fatal("profile enabled by default; opt-in must default off")
	}
	if err := a.SetJiraEnabledForProfile("work", true); err != nil {
		t.Fatalf("SetJiraEnabledForProfile: %v", err)
	}
	if !a.JiraEnabledForProfile("work") {
		t.Fatal("profile not enabled after opting in")
	}
	if a.JiraEnabledForProfile("personal") {
		t.Fatal("opting in one profile enabled another")
	}
}

func TestJiraOptOutIsReversible(t *testing.T) {
	a := newJiraTestApp(t)
	a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "tok", true)
	a.SetJiraEnabledForProfile("work", true)
	if err := a.SetJiraEnabledForProfile("work", false); err != nil {
		t.Fatalf("opt out: %v", err)
	}
	if a.JiraEnabledForProfile("work") {
		t.Fatal("still enabled after opting out")
	}
}

func TestGetJiraSettingsNeverReturnsToken(t *testing.T) {
	a := newJiraTestApp(t)
	a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "supersecret", true)
	for k, v := range a.GetJiraSettings() {
		if v == "supersecret" {
			t.Fatalf("GetJiraSettings leaked the token in key %q", k)
		}
	}
}

func TestJiraClientNilWhenProfileDisabled(t *testing.T) {
	a := newJiraTestApp(t)
	a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "tok", true)
	if a.jiraClientFor("work") != nil {
		t.Fatal("client returned for a profile that has not opted in")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test . -run TestJira -v`
Expected: FAIL — `undefined: SetJiraEnabledForProfile`.

- [ ] **Step 3: Write minimal implementation**

Add to `app/config.go`'s const block:

```go
	// Jira credentials are app-level (one set) and each profile opts in
	// separately via settingJiraProfilePrefix+<profile>. The site URL is NOT
	// here — it is the remote_url of the "jira" row in the integrations table,
	// so the global on/off and the URL travel together like every other
	// integration.
	settingJiraUsername      = "jira_username"
	settingJiraToken         = "jira_token"
	settingJiraProfilePrefix = "jira_enabled:"
)
```

Create `app/app_jira.go`:

```go
package main

import (
	"context"
	"errors"
	"strings"

	"phantom-ink/jira"
)

// jiraIntegrationName is the row key in the integrations table.
const jiraIntegrationName = "jira"

// jiraConfig returns the app-level credential set and whether it is usable.
// Usable means BOTH gates that are not profile-specific: the operator enabled
// the integration, and all three credential parts are present.
func (a *App) jiraConfig() (jira.Config, bool) {
	if a.db == nil {
		return jira.Config{}, false
	}
	row, ok := a.db.GetIntegration(jiraIntegrationName)
	if !ok || !row.Enabled {
		return jira.Config{}, false
	}
	cfg := jira.Config{
		BaseURL:  strings.TrimSpace(row.RemoteURL),
		Username: strings.TrimSpace(a.db.GetSetting(settingJiraUsername, "")),
		Token:    strings.TrimSpace(a.db.GetSetting(settingJiraToken, "")),
	}
	return cfg, cfg.BaseURL != "" && cfg.Username != "" && cfg.Token != ""
}

// JiraEnabledForProfile reports whether this profile should see Jira data.
// All three gates must pass. The opt-in defaults off, so a profile never gains
// a new surface merely because the operator configured credentials.
func (a *App) JiraEnabledForProfile(profile string) bool {
	if _, ok := a.jiraConfig(); !ok {
		return false
	}
	if a.db == nil || strings.TrimSpace(profile) == "" {
		return false
	}
	return a.db.GetSetting(settingJiraProfilePrefix+profile, "") == "true"
}

// SetJiraEnabledForProfile records one profile's opt-in.
func (a *App) SetJiraEnabledForProfile(profile string, enabled bool) error {
	if a.db == nil {
		return errNoDB
	}
	if strings.TrimSpace(profile) == "" {
		return errors.New("profile required")
	}
	v := "false"
	if enabled {
		v = "true"
	}
	return a.db.SetSetting(settingJiraProfilePrefix+profile, v)
}

// GetJiraSettings returns the non-secret settings for the Integrations UI.
// The token is deliberately absent: same contract as GetRegistrySettings.
func (a *App) GetJiraSettings() map[string]string {
	if a.db == nil {
		return map[string]string{"url": "", "username": "", "enabled": "false"}
	}
	row, _ := a.db.GetIntegration(jiraIntegrationName)
	enabled := "false"
	if row.Enabled {
		enabled = "true"
	}
	return map[string]string{
		"url":      strings.TrimSpace(row.RemoteURL),
		"username": strings.TrimSpace(a.db.GetSetting(settingJiraUsername, "")),
		"enabled":  enabled,
	}
}

// SetJiraSettings stores the app-level credential set and the integration row.
// Enabling here is the operator's global switch; profiles still opt in.
func (a *App) SetJiraSettings(url, username, token string, enabled bool) error {
	if a.db == nil {
		return errNoDB
	}
	if err := a.db.UpsertIntegration(IntegrationRow{
		Name:      jiraIntegrationName,
		Enabled:   enabled,
		Remote:    true, // saas: always remote, never a placed compose stack
		RemoteURL: strings.TrimRight(strings.TrimSpace(url), "/"),
	}); err != nil {
		return err
	}
	if err := a.db.SetSetting(settingJiraUsername, strings.TrimSpace(username)); err != nil {
		return err
	}
	return a.db.SetSetting(settingJiraToken, strings.TrimSpace(token))
}

// jiraClientFor returns a client, or nil when this profile must see no Jira
// data. Callers treat nil as "skip Jira entirely" — not as an error.
func (a *App) jiraClientFor(profile string) *jira.Client {
	if !a.JiraEnabledForProfile(profile) {
		return nil
	}
	cfg, ok := a.jiraConfig()
	if !ok {
		return nil
	}
	return jira.New(cfg, nil)
}

// VerifyJira is the Integrations status probe: a saas integration has no
// container to inspect, so "up" means the credentials work.
func (a *App) VerifyJira() error {
	cfg, ok := a.jiraConfig()
	if !ok {
		return errors.New("jira: not enabled or credentials incomplete")
	}
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	return jira.New(cfg, nil).Verify(ctx)
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test . -run TestJira -race -v`
Expected: PASS, four test functions.

- [ ] **Step 5: Commit**

```bash
git add app/config.go app/app_jira.go app/app_jira_test.go
git commit -m "feat(jira): global credentials with per-profile opt-in

Diverges from the Code panel's presence-is-selection idiom on purpose: a
token sitting in an env file should not silently add a surface to a profile."
```

---

### Task 5: Wire Jira into CodeOverview

**Files:**
- Modify: `app/app_code.go:42-65` (add fields), `app/app_code.go:162-193` (decorate in `CodeOverview`)
- Test: `app/app_code_jira_test.go`

**Interfaces:**
- Consumes: `jiraClientFor` (Task 4), `jira.ParseKeys` (Task 1), `jira.Issue` (Task 3).
- Produces:
  - `CodeOverview.IssueKeys map[string][]string` — row key -> issue keys
  - `CodeOverview.Issues map[string]jira.Issue` — issue key -> issue
  - `CodeOverview.JiraError string`
  - `func rowKey(it provider.Item) string` — `<provider>:<repo_full_name>#<number>`; the ONE helper both Go and the frontend key on.

- [ ] **Step 1: Write the failing test**

```go
package main

import (
	"testing"

	"phantom-ink/provider"
)

func TestRowKeyIsStable(t *testing.T) {
	it := provider.Item{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 412}
	if got, want := rowKey(it), "github:org/repo#412"; got != want {
		t.Fatalf("rowKey = %q, want %q", got, want)
	}
}

func TestCollectIssueKeysFromTitles(t *testing.T) {
	items := []provider.Item{
		{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 412, Title: "ABC-123: add webhook"},
		{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 408, Title: "bump deps"},
		{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 400, Title: "ABC-123 and DEF-9"},
	}
	byRow, all := collectIssueKeys(items)
	if got := byRow["github:org/repo#412"]; len(got) != 1 || got[0] != "ABC-123" {
		t.Fatalf("row 412 keys = %v", got)
	}
	if _, ok := byRow["github:org/repo#408"]; ok {
		t.Fatal("row with no key should not appear in the map")
	}
	if len(all) != 2 {
		t.Fatalf("deduped key set = %v, want ABC-123 and DEF-9", all)
	}
}

func TestJiraFailureDoesNotBlankGitRows(t *testing.T) {
	// decorateJira must leave rows untouched and report the failure instead.
	out := CodeOverview{
		Profile:      "work",
		PullRequests: []provider.Item{{Provider: provider.KindGitHub, RepoFullName: "org/repo", Number: 1, Title: "ABC-1: x"}},
	}
	decorateJira(&out, nil, errJiraUnavailable)
	if len(out.PullRequests) != 1 {
		t.Fatal("git rows were blanked by a Jira failure")
	}
	if out.JiraError == "" {
		t.Fatal("JiraError not set")
	}
	if len(out.Issues) != 0 {
		t.Fatal("issues populated despite failure")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test . -run "TestRowKey|TestCollectIssueKeys|TestJiraFailure" -v`
Expected: FAIL — `undefined: rowKey`, `undefined: collectIssueKeys`, `undefined: decorateJira`.

- [ ] **Step 3: Write minimal implementation**

Add to the `CodeOverview` struct in `app/app_code.go`, after `NotificationsError`:

```go
	// IssueKeys maps a row key (see rowKey) to the Jira issue keys found in
	// that row's title. Rows with no key are absent, not present-and-empty.
	IssueKeys map[string][]string `json:"issue_keys"`
	// Issues maps an issue key to the resolved issue. Derived per request;
	// nothing is persisted, so there is no index to drift from Jira.
	Issues map[string]jira.Issue `json:"issues"`
	// JiraError is non-fatal, exactly like the per-section errors above: a
	// Jira outage costs the chips, never the git rows.
	JiraError string `json:"jira_error"`
```

Add to the same file (import `"errors"` and `"phantom-ink/jira"`):

```go
var errJiraUnavailable = errors.New("jira unavailable")

// rowKey identifies one PR row. provider.Item has no id field, so the
// composite is the identity — and it is built HERE only, so the Go producer
// and the Svelte consumer cannot disagree about its shape.
func rowKey(it provider.Item) string {
	return fmt.Sprintf("%s:%s#%d", it.Provider, it.RepoFullName, it.Number)
}

// collectIssueKeys parses every row's title once, returning the per-row keys
// and the deduped set to resolve.
func collectIssueKeys(items []provider.Item) (map[string][]string, []string) {
	byRow := make(map[string][]string)
	seen := make(map[string]bool)
	var all []string
	for _, it := range items {
		keys := jira.ParseKeys(it.Title)
		if len(keys) == 0 {
			continue
		}
		byRow[rowKey(it)] = keys
		for _, k := range keys {
			if !seen[k] {
				seen[k] = true
				all = append(all, k)
			}
		}
	}
	return byRow, all
}

// decorateJira folds resolved issues into the overview. A non-nil err records
// the failure and leaves every git row exactly as it was.
func decorateJira(out *CodeOverview, issues map[string]jira.Issue, err error) {
	if err != nil {
		out.JiraError = err.Error()
		return
	}
	if len(issues) == 0 {
		return
	}
	byRow, _ := collectIssueKeys(out.PullRequests)
	out.IssueKeys = byRow
	out.Issues = issues
}
```

In `CodeOverview`, immediately before the final `return out, nil`:

```go
	if c := a.jiraClientFor(profile); c != nil {
		_, keys := collectIssueKeys(out.PullRequests)
		if len(keys) > 0 {
			issues, jerr := c.Resolve(ctx, keys)
			decorateJira(&out, issues, jerr)
		}
	}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./... -race`
Expected: PASS — the new tests plus every existing `app_code_test.go` and `app_code_repodetail_test.go` test.

- [ ] **Step 5: Commit**

```bash
git add app/app_code.go app/app_code_jira_test.go
git commit -m "feat(code): resolve Jira issues for PR rows

provider.Item stays untouched — Jira is not a git provider, so the resolved
issues ride on CodeOverview instead of contaminating the provider contract."
```

---

### Task 6: Chips in the Code panel

**Files:**
- Modify: `app/frontend/src/lib/panels/CodePanel.svelte`
- Verify: `cd app/frontend && npm run check`

**Interfaces:**
- Consumes: `CodeOverview.issue_keys`, `CodeOverview.issues`, `CodeOverview.jira_error` (Task 5).
- Produces: no new exports; presentation only.

- [ ] **Step 1: Regenerate the Wails bindings**

Run: `cd app && wails generate module`
Expected: `app/frontend/wailsjs/go/main/App.d.ts` gains `SetJiraEnabledForProfile`, `GetJiraSettings`, `SetJiraSettings`, `VerifyJira`, and the `CodeOverview` model gains `issue_keys`, `issues`, `jira_error`.

- [ ] **Step 2: Add the row-key helper and chip markup**

Mirror `rowKey` from Task 5 EXACTLY — the two must agree or every chip silently vanishes:

```svelte
<script lang="ts">
  // Mirrors rowKey() in app/app_code.go. Keep the two in lockstep.
  function rowKey(it: any): string {
    return `${it.provider}:${it.repo_full_name}#${it.number}`;
  }

  function issuesFor(overview: any, it: any): any[] {
    const keys = overview?.issue_keys?.[rowKey(it)] ?? [];
    return keys.map((k: string) => overview?.issues?.[k]).filter(Boolean);
  }

  // Jira's statusCategory is a 3-value enum, so the colour mapping is total
  // and needs no fallback branch beyond the default.
  function chipClass(cat: string): string {
    if (cat === "done") return "chip chip-done";
    if (cat === "indeterminate") return "chip chip-progress";
    return "chip chip-todo";
  }
</script>

{#each issuesFor(overview, item) as issue}
  <a class={chipClass(issue.status_category)} href={issue.url} target="_blank" rel="noreferrer"
     title={issue.summary}>
    {issue.key} · {issue.status}{issue.assignee ? ` · ${issue.assignee}` : ""}
  </a>
{/each}
```

```css
.chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 1px 6px; margin-right: 4px;
  border-radius: 10px; border: 1px solid var(--border);
  font-size: 11px; text-decoration: none; color: var(--text);
}
.chip-done { border-color: var(--ok, #3fb950); }
.chip-progress { border-color: var(--warn, #d29922); }
.chip-todo { opacity: 0.75; }
```

- [ ] **Step 3: Show the Jira error without hiding git rows**

Place beside the existing section-error banners, never in place of the rows:

```svelte
{#if overview?.jira_error}
  <div class="banner banner-warn">Jira unavailable — issue status hidden. {overview.jira_error}</div>
{/if}
```

- [ ] **Step 4: Verify the frontend typechecks**

Run: `cd app/frontend && npm run check`
Expected: no new errors attributable to CodePanel.svelte.

- [ ] **Step 5: Commit**

```bash
git add app/frontend/src/lib/panels/CodePanel.svelte app/frontend/wailsjs
git commit -m "feat(code): show Jira issue chips on pull-request rows

A Jira outage degrades to a banner rather than an empty panel, so the git
rows people rely on stay readable when the tracker is down."
```

---

### Task 7: Full verification

**Files:** none modified.

- [ ] **Step 1: Go tests with race detection**

Run: `cd app && go test ./... -race`
Expected: PASS, no failures.

- [ ] **Step 2: Svelte typecheck**

Run: `cd app/frontend && npm run check`
Expected: no new errors.

- [ ] **Step 3: Confirm the opted-out path**

Run: `cd app && go test . -run TestJira -race -v`
Expected: PASS — proving a profile that has not opted in gets a nil client and therefore no chips, no calls, no panel.

- [ ] **Step 4: Commit any fixes and push the branch**

```bash
git push -u origin feat/jira-integration
```

## Deferred to later slices (NOT in this plan)

- Jira detail view (spec Section 3)
- Dispatch an agent at an issue (spec Section 4.1)
- Drift report (spec Section 4.2)
- Transition on merge (spec Section 4.3) — needs write scope and an unresolved trigger
- Branch-derived keys from `RepoDetail` — the overview has no branch names
- The `saas` kind in the ADR-003 router catalog; Task 4 gives the app-side gate that a router catalog entry would later call
