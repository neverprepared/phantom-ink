# ADO Code-Panel Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Azure DevOps as a second git provider in the app's Code panel, aggregated per-profile alongside GitHub.

**Architecture:** Generalize the GitHub-only `app/githubclient` package into an `app/provider` package with a provider-agnostic `Client` interface whose config binds at construction. Ship two implementations (`provider/github`, `provider/ado`). `app_code.go` reads the profile's gateway env, constructs the set of *configured* providers (presence = selection), fans reads out across all of them, and merges the results with a provider tag on every row. The frontend renders the merged lists with per-row provider badges.

**Tech Stack:** Go 1.25 (app module `phantom-ink`), Svelte 5 + TypeScript frontend, Wails 2 bindings, `go test ./... -race`, `npm run check` (svelte-check).

**Spec:** `docs/superpowers/specs/2026-09-20-ado-code-provider-design.md`

## Global Constraints

- **No new secret storage.** All per-profile provider config lives in the existing encrypted gateway env store. GitHub: `GITHUB_TOKEN`. ADO: `ADO_ORG`, `ADO_PROJECT`, `ADO_PAT` (all three required for ADO to be considered configured).
- **Presence = selection.** A provider is enabled for a profile iff its credentials are present. A missing/partial provider is silently absent, never a hard error.
- **ADO API version pinned to `7.1`** — literal `api-version=7.1` on every ADO request.
- **ADO auth:** `Authorization: Basic base64(":" + PAT)`.
- **ADO scope:** exactly one org + one project per profile. No project enumeration.
- **Fail-soft per-section, per-provider.** One provider's 401 or one section's error must never blank another provider's or section's results. Section error strings are provider-prefixed (`"github: …; ado: …"`).
- **Read-only.** Every provider method is a GET/WIQL read; nothing mutates a repo.
- **Base URL injectable** in both provider impls so tests run against `httptest.Server` (mirrors the existing `githubclient.NewWithBase`).
- **Notifications inbox stays GitHub-only.** ADO contributes nothing to that section in v1.
- **Wails binding regen is a gate:** after Go method signatures change, regenerate `app/frontend/wailsjs` and the frontend must compile against them (`npm run check`).
- Run all Go work from `app/` (`cd app`). The frontend lives in `app/frontend/`.

---

## File structure

- **Create** `app/provider/provider.go` — `Kind`, `RepoRef`, shared result types (`Repo`, `Item`, `Branch`, `Commit`, `Notification`), `StatusError`, `IsUnauthorized`, the `Client` interface, and the optional `Notifier` interface.
- **Create** `app/provider/github/github.go` — `github.Client` implementing `provider.Client` + `Notifier` (ported from `githubclient`, token bound at construction).
- **Create** `app/provider/github/github_test.go` + `app/provider/github/github_repodetail_test.go` — ported from the two existing `githubclient` test files.
- **Create** `app/provider/ado/ado.go` — `ado.Client` implementing `provider.Client`.
- **Create** `app/provider/ado/ado_test.go` — table-driven httptest tests.
- **Delete** `app/githubclient/` (client.go + its two test files) once `app_code.go` no longer imports it (Task 5).
- **Modify** `app/app_code.go` — `providersFor`, fan-out cutover, `CodeOverview`/`RepoDetailResult` field types, `RepoDetail` signature, ADO-aware clone.
- **Modify** `app/app_code_test.go` + `app/app_code_repodetail_test.go` — retarget fakes/types.
- **Create** `app/app_ado_token.go` + `app/app_ado_token_test.go` — `ValidateADOConnection`.
- **Modify** `app/frontend/src/lib/stores/code.svelte.ts` — provider fields, `RepoRef` detail call, provider-aware dispatch URL.
- **Modify** `app/frontend/src/lib/panels/CodePanel.svelte` — provider badges, work-item labelling.
- **Modify** `app/frontend/src/lib/components/GatewayEnvEditor.svelte` — "+ ADO connection" seeder + ADO validation on save.
- **Regenerate** `app/frontend/wailsjs/go/main/App.{d.ts,js}` (and `models.ts` if present).

---

## Task 1: `provider` package — shared types + interface

**Files:**
- Create: `app/provider/provider.go`
- Test: `app/provider/provider_test.go`

**Interfaces:**
- Produces: `provider.Kind` (`KindGitHub="github"`, `KindADO="ado"`); `provider.RepoRef{Provider Kind; Owner, Name, ID, CloneURL, DefaultBranch string}`; result structs `Repo`, `Item`, `Branch`, `Commit`, `Notification`; `provider.StatusError{Code int; Status, Path string}` with `Error()`; `provider.IsUnauthorized(error) bool`; reason consts `ReasonAuthored`, `ReasonReviewRequested`, `ReasonAssigned`; the `Client` interface; the `Notifier` interface.

- [ ] **Step 1: Write the failing test**

`app/provider/provider_test.go`:

```go
package provider

import (
	"errors"
	"net/http"
	"testing"
)

func TestIsUnauthorized(t *testing.T) {
	if !IsUnauthorized(&StatusError{Code: http.StatusUnauthorized, Status: "401", Path: "/x"}) {
		t.Fatal("401 StatusError should be unauthorized")
	}
	if IsUnauthorized(&StatusError{Code: http.StatusNotFound}) {
		t.Fatal("404 is not unauthorized")
	}
	if IsUnauthorized(errors.New("plain")) {
		t.Fatal("non-StatusError is not unauthorized")
	}
}

func TestStatusErrorMessage(t *testing.T) {
	e := &StatusError{Code: 500, Status: "500 Internal Server Error", Path: "/repos"}
	if got := e.Error(); got != "provider /repos: 500 Internal Server Error" {
		t.Fatalf("unexpected message: %q", got)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./provider/ -run TestIsUnauthorized -v`
Expected: FAIL — package/symbols do not exist.

- [ ] **Step 3: Write minimal implementation**

`app/provider/provider.go`:

```go
// Package provider is the app's git-host abstraction for the Code panel. A
// Client is a read-only surface over one host (GitHub, Azure DevOps) whose
// credentials/config are bound at construction, so app_code.go can hold a set
// of configured providers for a profile and fan the same reads across them.
package provider

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

// Kind identifies which host a Client (and every row it returns) belongs to.
type Kind string

const (
	KindGitHub Kind = "github"
	KindADO    Kind = "ado"
)

// Reasons a PR/work-item made the "needs attention" list. Carried per row so
// the UI can say WHY it is listed.
const (
	ReasonAuthored        = "authored"
	ReasonReviewRequested = "review-requested"
	ReasonAssigned        = "assigned"
)

// RepoRef identifies a repo across providers. The frontend receives it on each
// repo row and echoes it back on a detail call, so RepoDetail routing needs no
// server-side lookup.
type RepoRef struct {
	Provider      Kind   `json:"provider"`
	Owner         string `json:"owner"` // GitHub: org/user;  ADO: project
	Name          string `json:"name"`
	ID            string `json:"id"` // ADO repo GUID; empty for GitHub
	CloneURL      string `json:"clone_url"`
	DefaultBranch string `json:"default_branch"`
}

// Repo is one repository row on the launchpad.
type Repo struct {
	Provider      Kind   `json:"provider"`
	Owner         string `json:"owner"`
	Name          string `json:"name"`
	FullName      string `json:"full_name"`
	Description   string `json:"description"`
	HTMLURL       string `json:"html_url"`
	CloneURL      string `json:"clone_url"`
	DefaultBranch string `json:"default_branch"`
	PushedAt      string `json:"pushed_at"`
	Stars         int    `json:"stars"`
	OpenIssues    int    `json:"open_issues"`
	// ID is the ADO repo GUID; empty for GitHub. Needed to build a RepoRef.
	ID string `json:"id"`
}

// Item is one PR / issue / work-item row. The three collapse into one shape so
// the panel renders a merged list; IsPullRequest distinguishes a PR from an
// issue/work-item.
type Item struct {
	Provider      Kind   `json:"provider"`
	RepoFullName  string `json:"repo_full_name"`
	Number        int    `json:"number"`
	Title         string `json:"title"`
	State         string `json:"state"`
	HTMLURL       string `json:"html_url"`
	UpdatedAt     string `json:"updated_at"`
	User          string `json:"user"`
	Draft         bool   `json:"draft"`
	IsPullRequest bool   `json:"is_pull_request"`
	// Reason is why this row is listed (authored/review-requested/assigned);
	// not a host field.
	Reason string `json:"reason"`
	// RepoID carries the ADO repo GUID for a PR row so a follow-up detail call
	// can build a RepoRef; empty for GitHub.
	RepoID string `json:"repo_id"`
}

// Branch is one branch head.
type Branch struct {
	Name string `json:"name"`
	SHA  string `json:"sha"`
}

// Commit is one entry from a repo's log. Message is the FULL message; the panel
// renders only its first line.
type Commit struct {
	SHA     string `json:"sha"`
	Message string `json:"message"`
	Author  string `json:"author"`
	Date    string `json:"date"`
	HTMLURL string `json:"html_url"`
}

// Notification is one GitHub inbox row. GitHub-only in v1.
type Notification struct {
	ID           string `json:"id"`
	RepoFullName string `json:"repo_full_name"`
	SubjectTitle string `json:"subject_title"`
	SubjectType  string `json:"subject_type"`
	Reason       string `json:"reason"`
	UpdatedAt    string `json:"updated_at"`
	URL          string `json:"url"`
}

// StatusError is a non-2xx response from a provider. It keeps the code so
// callers can tell "credential rejected" (401) from "host having a day" (5xx).
type StatusError struct {
	Code   int
	Status string
	Path   string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("provider %s: %s", e.Path, e.Status)
}

// IsUnauthorized reports whether err is a provider rejecting the credential.
func IsUnauthorized(err error) bool {
	var se *StatusError
	if errors.As(err, &se) {
		return se.Code == http.StatusUnauthorized
	}
	return false
}

// Client is the read surface the Code panel needs, one instance per
// (profile, provider), constructed from that provider's config.
type Client interface {
	Kind() Kind

	// Overview
	ListRepos(ctx context.Context) ([]Repo, error)
	SearchMyPRs(ctx context.Context) ([]Item, error)      // authored + review-requested, deduped, reasons tagged
	ListAssignedWork(ctx context.Context) ([]Item, error) // GitHub assigned issues / ADO @Me work items

	// Detail
	ListBranches(ctx context.Context, ref RepoRef) ([]Branch, error)
	ListRecentCommits(ctx context.Context, ref RepoRef, limit int) ([]Commit, error)
	GetReadme(ctx context.Context, ref RepoRef) (md string, htmlURL string, err error)
	RepoPRs(ctx context.Context, ref RepoRef) ([]Item, error)
	RepoIssues(ctx context.Context, ref RepoRef) ([]Item, error) // ADO: empty in v1

	NormalizeCloneURL(url string) string
}

// Notifier is the GitHub-only notifications surface. app_code.go type-asserts
// for it rather than putting a GitHub concept on every provider.
type Notifier interface {
	ListNotifications(ctx context.Context) ([]Notification, error)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && go test ./provider/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd app && go build ./... && git add provider/provider.go provider/provider_test.go
git commit -m "feat(app): add provider package (interface + shared types)"
```

---

## Task 2: `provider/github` — port the GitHub client behind the interface

Port `app/githubclient/client.go` into `app/provider/github/github.go`, keeping the existing HTTP behaviour but (a) binding the token at construction, (b) returning `provider.*` types tagged `KindGitHub`, and (c) satisfying `provider.Client` + `provider.Notifier`. The old `githubclient` package stays in place until Task 5.

**Files:**
- Create: `app/provider/github/github.go`
- Test: `app/provider/github/github_test.go`, `app/provider/github/github_repodetail_test.go`

**Interfaces:**
- Consumes: everything from Task 1.
- Produces: `github.New(token string) *github.Client`; `github.NewWithBase(base, token string, hc *http.Client) *github.Client`. `*github.Client` implements `provider.Client` and `provider.Notifier`.

**Port transformation rules (apply to each method moved from `githubclient/client.go`):**
1. Package becomes `package github`; import `phantom-ink/provider`.
2. `Client` gains a `token string` field set by the constructor. `New(token)` uses `provider`-less defaults; `NewWithBase(base, token, hc)` mirrors the old signature plus `token`.
3. The private `get` helper drops its `token` param and reads `c.token`; it returns `*provider.StatusError` (not the local type) and uses `errors.New("no GITHUB_TOKEN for this profile")` when `c.token==""`.
4. Every public method drops its `token` argument. Detail methods take `ref provider.RepoRef` and use `ref.Owner`, `ref.Name`.
5. Return types change to `provider.Repo/Item/Branch/Commit/Notification`, each built with `Provider: provider.KindGitHub`. The old `Issue` type maps field-for-field to `provider.Item` (`RepoFullName`,`Number`,`Title`,`State`,`HTMLURL`,`UpdatedAt`,`User`,`Draft`,`IsPullRequest`,`Reason`); leave `RepoID` empty.
6. `ListRepos` sets `Repo.ID = ""` (GitHub rows need no GUID) and keeps `Owner/Name/FullName/...`.
7. **`SearchMyPRs`** is new-shaped: it runs the existing authored and review-requested searches, then folds them through the existing dedupe-by-`HTMLURL`-merging-reasons logic (port `mergePullRequests` + `sortIssues` as unexported helpers `mergePRs`/`sortItems` in this package), returning one deduped `[]provider.Item`.
8. **`ListAssignedWork`** = the old `SearchIssuesAssigned`, sorted newest-first.
9. **`RepoPRs`/`RepoIssues`** = the old `SearchPRsByRepo`/`SearchIssuesByRepo`, taking `ref` and sorted newest-first.
10. **`NormalizeCloneURL(url string) string`** = the old free function `normalizeCloneURL` (GitHub host rules: preserve host, rewrite only `api.github.com`→`github.com`). Make it a method so it satisfies the interface; the logic is unchanged.
11. `ListNotifications(ctx)` keeps its body (drop token arg); this is what makes `*github.Client` a `provider.Notifier`.
12. Keep `IsUnauthorized`/`StatusError` usages pointed at the `provider` package (delete the local copies — they now live in `provider`).
13. `Kind() provider.Kind { return provider.KindGitHub }`.

- [ ] **Step 1: Port the tests first (they are the spec for the port)**

Copy `app/githubclient/client_test.go` → `app/provider/github/github_test.go` and `app/githubclient/client_repodetail_test.go` → `app/provider/github/github_repodetail_test.go`, then mechanically adapt:
- `package github`.
- Construct with the token bound: replace `githubclient.NewWithBase(srv.URL, nil)` + per-call `token` args with `github.NewWithBase(srv.URL, "test-token", nil)` and call methods with no token arg.
- Detail calls take a `provider.RepoRef{Provider: provider.KindGitHub, Owner: "o", Name: "r"}` instead of `owner, repo` strings.
- Assert against `provider.Repo/Item/...` field names (the JSON-tag-backed Go field names are unchanged except `Issue`→`Item`).
- For the authored+review-requested tests: assert `SearchMyPRs` returns the deduped/merged set (a PR that is both authored and review-requested appears once with `Reason` containing both), replacing the old separate `SearchPRsAuthored`/`SearchPRsReviewRequested` assertions. The httptest handler must route both `q=…author:@me` and `q=…review-requested:@me` search calls.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && go test ./provider/github/ -v`
Expected: FAIL — `app/provider/github/github.go` does not exist.

- [ ] **Step 3: Write the implementation**

Create `app/provider/github/github.go` by moving `githubclient/client.go`'s body and applying rules 1–13 above. Keep the wire structs (`wireRepo`, `wireSearch`, `wireNotification`, `wireBranch`, `wireCommit`, `wireReadme`) verbatim, the base64 README decode verbatim, and the `subjectBrowserURL`/`repoFullNameFromAPIURL` helpers verbatim. The `X-GitHub-Api-Version: 2022-11-28` header, `Accept: application/vnd.github+json`, and `Authorization: Bearer <token>` stay exactly as they are.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./provider/github/ -race -v`
Expected: PASS.

- [ ] **Step 5: Verify the interface is satisfied**

Add to `github.go` (compile-time assertions):

```go
var (
	_ provider.Client   = (*Client)(nil)
	_ provider.Notifier = (*Client)(nil)
)
```

Run: `cd app && go build ./provider/...`
Expected: builds clean.

- [ ] **Step 6: Commit**

```bash
cd app && git add provider/github/
git commit -m "feat(app): port GitHub client into provider/github"
```

---

## Task 3: `provider/ado` — Azure DevOps client

New implementation of `provider.Client` against ADO REST 7.1. Work items are the "assigned work" surface; `RepoIssues` returns empty in v1 (work items aren't repo-scoped).

**Files:**
- Create: `app/provider/ado/ado.go`
- Test: `app/provider/ado/ado_test.go`

**Interfaces:**
- Consumes: Task 1 types.
- Produces: `ado.New(org, project, pat string) *ado.Client`; `ado.NewWithBase(base, org, project, pat string, hc *http.Client) *ado.Client`. `*ado.Client` implements `provider.Client`. `base` defaults to `https://dev.azure.com`.

- [ ] **Step 1: Write the failing tests**

`app/provider/ado/ado_test.go` — one httptest server switching on path. Cover: repos list, PRs (creator+reviewer via connectionData identity), WIQL→hydrate work items, branches, commits, README-present and README-404, clone-URL normalization, and a 401 surfacing as `provider.IsUnauthorized`.

```go
package ado

import (
	"context"
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"phantom-ink/provider"
)

const (
	testOrg     = "acme"
	testProject = "widgets"
	testPAT     = "pat-123"
	meID        = "11111111-1111-1111-1111-111111111111"
)

// adoServer routes the handful of endpoints the client hits.
func adoServer(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()

	// Identity resolution.
	mux.HandleFunc("/"+testOrg+"/_apis/connectionData", func(w http.ResponseWriter, r *http.Request) {
		wantAuth := "Basic " + base64.StdEncoding.EncodeToString([]byte(":"+testPAT))
		if r.Header.Get("Authorization") != wantAuth {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(`{"authenticatedUser":{"id":"` + meID + `","providerDisplayName":"Me"}}`))
	})

	// Repos.
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/git/repositories", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"count":1,"value":[{"id":"repo-guid","name":"widget-api","defaultBranch":"refs/heads/main","webUrl":"https://dev.azure.com/acme/widgets/_git/widget-api","remoteUrl":"https://dev.azure.com/acme/widgets/_git/widget-api"}]}`))
	})

	// Org-level PRs (creator or reviewer). Return one PR for the creator query.
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/git/pullrequests", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("searchCriteria.creatorId") == meID {
			_, _ = w.Write([]byte(`{"value":[{"pullRequestId":7,"title":"Add caching","status":"active","isDraft":false,"creationDate":"2026-09-10T00:00:00Z","createdBy":{"displayName":"Me"},"repository":{"id":"repo-guid","name":"widget-api"}}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"value":[]}`))
	})

	// WIQL → ids.
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/wit/wiql", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"workItems":[{"id":42}]}`))
	})
	// Work item hydrate.
	mux.HandleFunc("/"+testOrg+"/_apis/wit/workitems", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"value":[{"id":42,"fields":{"System.Title":"Fix flaky test","System.State":"Active","System.WorkItemType":"Bug","System.ChangedDate":"2026-09-12T00:00:00Z"}}]}`))
	})

	// Branches, commits, readme (repo-scoped).
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/git/repositories/repo-guid/refs", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"value":[{"name":"refs/heads/main","objectId":"abc123"}]}`))
	})
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/git/repositories/repo-guid/commits", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"value":[{"commitId":"deadbeef","comment":"Initial commit","author":{"name":"Dev","date":"2026-09-01T00:00:00Z"},"remoteUrl":"https://dev.azure.com/acme/widgets/_git/widget-api/commit/deadbeef"}]}`))
	})
	mux.HandleFunc("/"+testOrg+"/"+testProject+"/_apis/git/repositories/repo-guid/items", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("path") == "/README.md" {
			_, _ = w.Write([]byte(`{"content":"# Widget API\nHello","path":"/README.md"}`))
			return
		}
		w.WriteHeader(http.StatusNotFound)
	})

	return httptest.NewServer(mux)
}

func newTestClient(base string) *Client {
	return NewWithBase(base, testOrg, testProject, testPAT, nil)
}

func TestListRepos(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	repos, err := newTestClient(srv.URL).ListRepos(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(repos) != 1 {
		t.Fatalf("want 1 repo, got %d", len(repos))
	}
	r := repos[0]
	if r.Provider != provider.KindADO || r.Name != "widget-api" || r.ID != "repo-guid" {
		t.Fatalf("unexpected repo: %+v", r)
	}
	if r.DefaultBranch != "main" {
		t.Fatalf("want default branch main, got %q", r.DefaultBranch)
	}
	if r.Owner != testProject {
		t.Fatalf("want owner=project %q, got %q", testProject, r.Owner)
	}
	if !strings.Contains(r.CloneURL, "/_git/widget-api") {
		t.Fatalf("unexpected clone url: %q", r.CloneURL)
	}
}

func TestSearchMyPRs(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	prs, err := newTestClient(srv.URL).SearchMyPRs(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(prs) != 1 {
		t.Fatalf("want 1 PR, got %d", len(prs))
	}
	if prs[0].Number != 7 || !prs[0].IsPullRequest || prs[0].Provider != provider.KindADO {
		t.Fatalf("unexpected PR: %+v", prs[0])
	}
	if !strings.Contains(prs[0].HTMLURL, "/pullrequest/7") {
		t.Fatalf("unexpected PR url: %q", prs[0].HTMLURL)
	}
}

func TestListAssignedWork(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	work, err := newTestClient(srv.URL).ListAssignedWork(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(work) != 1 {
		t.Fatalf("want 1 work item, got %d", len(work))
	}
	w := work[0]
	if w.Number != 42 || w.IsPullRequest || w.Title != "Fix flaky test" {
		t.Fatalf("unexpected work item: %+v", w)
	}
	if !strings.Contains(w.HTMLURL, "/_workitems/edit/42") {
		t.Fatalf("unexpected work item url: %q", w.HTMLURL)
	}
}

func TestRepoDetailReads(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	c := newTestClient(srv.URL)
	ref := provider.RepoRef{Provider: provider.KindADO, Owner: testProject, Name: "widget-api", ID: "repo-guid"}

	branches, err := c.ListBranches(context.Background(), ref)
	if err != nil || len(branches) != 1 || branches[0].Name != "main" {
		t.Fatalf("branches: %+v err=%v", branches, err)
	}
	commits, err := c.ListRecentCommits(context.Background(), ref, 20)
	if err != nil || len(commits) != 1 || commits[0].SHA != "deadbeef" {
		t.Fatalf("commits: %+v err=%v", commits, err)
	}
	md, _, err := c.GetReadme(context.Background(), ref)
	if err != nil || !strings.Contains(md, "Widget API") {
		t.Fatalf("readme: %q err=%v", md, err)
	}
	issues, err := c.RepoIssues(context.Background(), ref)
	if err != nil || len(issues) != 0 {
		t.Fatalf("RepoIssues must be empty in v1: %+v err=%v", issues, err)
	}
}

func TestReadmeAbsentIsNotError(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	c := newTestClient(srv.URL)
	ref := provider.RepoRef{Provider: provider.KindADO, Owner: testProject, Name: "no-readme", ID: "missing-guid"}
	md, _, err := c.GetReadme(context.Background(), ref)
	if err != nil {
		t.Fatalf("absent README must be nil error, got %v", err)
	}
	if md != "" {
		t.Fatalf("absent README must be empty, got %q", md)
	}
}

func TestUnauthorizedSurfaces(t *testing.T) {
	srv := adoServer(t)
	defer srv.Close()
	c := NewWithBase(srv.URL, testOrg, testProject, "wrong-pat", nil)
	_, err := c.SearchMyPRs(context.Background())
	if !provider.IsUnauthorized(err) {
		t.Fatalf("want unauthorized, got %v", err)
	}
}

func TestNormalizeCloneURL(t *testing.T) {
	c := newTestClient("https://dev.azure.com")
	cases := map[string]string{
		"https://dev.azure.com/acme/widgets/_git/widget-api":     "https://dev.azure.com/acme/widgets/_git/widget-api",
		"https://acme@dev.azure.com/acme/widgets/_git/widget-api": "https://dev.azure.com/acme/widgets/_git/widget-api",
		"https://acme.visualstudio.com/widgets/_git/widget-api":   "https://dev.azure.com/acme/widgets/_git/widget-api",
	}
	for in, want := range cases {
		if got := c.NormalizeCloneURL(in); got != want {
			t.Errorf("NormalizeCloneURL(%q) = %q, want %q", in, got, want)
		}
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && go test ./provider/ado/ -v`
Expected: FAIL — package does not exist.

- [ ] **Step 3: Write the implementation**

`app/provider/ado/ado.go`:

```go
// Package ado is a read-only Azure DevOps REST client (api-version 7.1) that
// implements provider.Client. Config (org, project, PAT) binds at construction;
// the base URL is injectable for tests. ADO is org+project scoped: one client
// serves exactly one (org, project).
package ado

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"phantom-ink/provider"
)

const (
	defaultBase    = "https://dev.azure.com"
	apiVersion     = "7.1"
	requestTimeout = 10 * time.Second
	commitLimit    = 20
)

type Client struct {
	base    string
	org     string
	project string
	pat     string
	hc      *http.Client

	// meID caches the authenticated user's GUID (from connectionData); ADO PR
	// creator/reviewer queries need it, work-item queries use @Me instead.
	meID string
}

func New(org, project, pat string) *Client {
	return NewWithBase(defaultBase, org, project, pat, nil)
}

func NewWithBase(base, org, project, pat string, hc *http.Client) *Client {
	if hc == nil {
		hc = &http.Client{Timeout: requestTimeout}
	}
	return &Client{base: strings.TrimRight(base, "/"), org: org, project: project, pat: pat, hc: hc}
}

func (c *Client) Kind() provider.Kind { return provider.KindADO }

// authHeader is Basic auth with an empty username and the PAT as password.
func (c *Client) authHeader() string {
	return "Basic " + base64.StdEncoding.EncodeToString([]byte(":"+c.pat))
}

// withVersion appends api-version to a path that may already carry a query.
func withVersion(path string) string {
	if strings.Contains(path, "?") {
		return path + "&api-version=" + apiVersion
	}
	return path + "?api-version=" + apiVersion
}

func (c *Client) get(ctx context.Context, path string, out any) error {
	return c.do(ctx, http.MethodGet, path, nil, out)
}

func (c *Client) do(ctx context.Context, method, path string, body []byte, out any) error {
	if c.pat == "" || c.org == "" || c.project == "" {
		return errors.New("ADO not configured (need ADO_ORG, ADO_PROJECT, ADO_PAT)")
	}
	var rdr *bytes.Reader
	if body != nil {
		rdr = bytes.NewReader(body)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, rdr)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", c.authHeader())
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
		return &provider.StatusError{Code: resp.StatusCode, Status: resp.Status, Path: path}
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// orgPath / projPath build the two scopes. Project-scoped resources sit under
// /{org}/{project}; identity and work-item hydrate sit under /{org}.
func (c *Client) orgPath(p string) string  { return "/" + c.org + p }
func (c *Client) projPath(p string) string { return "/" + c.org + "/" + c.project + p }

// me resolves and caches the authenticated user's GUID.
func (c *Client) me(ctx context.Context) (string, error) {
	if c.meID != "" {
		return c.meID, nil
	}
	var cd struct {
		AuthenticatedUser struct {
			ID string `json:"id"`
		} `json:"authenticatedUser"`
	}
	if err := c.get(ctx, withVersion(c.orgPath("/_apis/connectionData")), &cd); err != nil {
		return "", err
	}
	c.meID = cd.AuthenticatedUser.ID
	return c.meID, nil
}

// --- Repos ------------------------------------------------------------------

type wireRepo struct {
	ID            string `json:"id"`
	Name          string `json:"name"`
	DefaultBranch string `json:"defaultBranch"`
	WebURL        string `json:"webUrl"`
	RemoteURL     string `json:"remoteUrl"`
}

func (c *Client) ListRepos(ctx context.Context) ([]provider.Repo, error) {
	var wire struct {
		Value []wireRepo `json:"value"`
	}
	if err := c.get(ctx, withVersion(c.projPath("/_apis/git/repositories")), &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Repo, 0, len(wire.Value))
	for _, w := range wire.Value {
		clone := w.RemoteURL
		if clone == "" {
			clone = c.cloneURLFor(w.Name)
		}
		out = append(out, provider.Repo{
			Provider:      provider.KindADO,
			Owner:         c.project,
			Name:          w.Name,
			FullName:      c.project + "/" + w.Name,
			HTMLURL:       w.WebURL,
			CloneURL:      clone,
			DefaultBranch: strings.TrimPrefix(w.DefaultBranch, "refs/heads/"),
			ID:            w.ID,
		})
	}
	return out, nil
}

func (c *Client) cloneURLFor(repo string) string {
	return fmt.Sprintf("%s/%s/%s/_git/%s", c.base, c.org, c.project, repo)
}

// --- Pull requests ----------------------------------------------------------

type wirePR struct {
	PullRequestID int    `json:"pullRequestId"`
	Title         string `json:"title"`
	Status        string `json:"status"`
	IsDraft       bool   `json:"isDraft"`
	CreationDate  string `json:"creationDate"`
	CreatedBy     struct {
		DisplayName string `json:"displayName"`
	} `json:"createdBy"`
	Repository struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	} `json:"repository"`
}

func (c *Client) prURL(repo string, id int) string {
	return fmt.Sprintf("%s/%s/%s/_git/%s/pullrequest/%d", c.base, c.org, c.project, repo, id)
}

func (c *Client) prItem(w wirePR, reason string) provider.Item {
	return provider.Item{
		Provider:      provider.KindADO,
		RepoFullName:  c.project + "/" + w.Repository.Name,
		Number:        w.PullRequestID,
		Title:         w.Title,
		State:         w.Status,
		HTMLURL:       c.prURL(w.Repository.Name, w.PullRequestID),
		UpdatedAt:     w.CreationDate,
		User:          w.CreatedBy.DisplayName,
		Draft:         w.IsDraft,
		IsPullRequest: true,
		Reason:        reason,
		RepoID:        w.Repository.ID,
	}
}

func (c *Client) searchPRs(ctx context.Context, criteria url.Values) ([]wirePR, error) {
	criteria.Set("searchCriteria.status", "active")
	path := c.projPath("/_apis/git/pullrequests") + "?" + criteria.Encode()
	var wire struct {
		Value []wirePR `json:"value"`
	}
	if err := c.get(ctx, withVersion(path), &wire); err != nil {
		return nil, err
	}
	return wire.Value, nil
}

// SearchMyPRs unions PRs I created and PRs where I'm a reviewer, deduped by
// pullRequestId, reasons merged.
func (c *Client) SearchMyPRs(ctx context.Context) ([]provider.Item, error) {
	id, err := c.me(ctx)
	if err != nil {
		return nil, err
	}
	authored, err := c.searchPRs(ctx, url.Values{"searchCriteria.creatorId": {id}})
	if err != nil {
		return nil, err
	}
	reviews, err := c.searchPRs(ctx, url.Values{"searchCriteria.reviewerId": {id}})
	if err != nil {
		return nil, err
	}
	byID := map[int]int{} // pullRequestId -> index in out
	out := make([]provider.Item, 0, len(authored)+len(reviews))
	add := func(w wirePR, reason string) {
		if i, seen := byID[w.PullRequestID]; seen {
			if !strings.Contains(out[i].Reason, reason) {
				out[i].Reason += ", " + reason
			}
			return
		}
		byID[w.PullRequestID] = len(out)
		out = append(out, c.prItem(w, reason))
	}
	for _, w := range authored {
		add(w, provider.ReasonAuthored)
	}
	for _, w := range reviews {
		add(w, provider.ReasonReviewRequested)
	}
	sortItems(out)
	return out, nil
}

// --- Work items -------------------------------------------------------------

func (c *Client) ListAssignedWork(ctx context.Context) ([]provider.Item, error) {
	wiql := `SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = @Me AND [System.State] <> 'Closed' ORDER BY [System.ChangedDate] DESC`
	body, _ := json.Marshal(map[string]string{"query": wiql})
	var idsResp struct {
		WorkItems []struct {
			ID int `json:"id"`
		} `json:"workItems"`
	}
	if err := c.do(ctx, http.MethodPost, withVersion(c.projPath("/_apis/wit/wiql")), body, &idsResp); err != nil {
		return nil, err
	}
	if len(idsResp.WorkItems) == 0 {
		return nil, nil
	}
	ids := make([]string, 0, len(idsResp.WorkItems))
	for _, w := range idsResp.WorkItems {
		ids = append(ids, fmt.Sprintf("%d", w.ID))
	}
	q := url.Values{
		"ids":    {strings.Join(ids, ",")},
		"fields": {"System.Title,System.State,System.WorkItemType,System.ChangedDate"},
	}
	var wi struct {
		Value []struct {
			ID     int `json:"id"`
			Fields struct {
				Title   string `json:"System.Title"`
				State   string `json:"System.State"`
				Type    string `json:"System.WorkItemType"`
				Changed string `json:"System.ChangedDate"`
			} `json:"fields"`
		} `json:"value"`
	}
	if err := c.get(ctx, withVersion(c.orgPath("/_apis/wit/workitems")+"?"+q.Encode()), &wi); err != nil {
		return nil, err
	}
	out := make([]provider.Item, 0, len(wi.Value))
	for _, w := range wi.Value {
		out = append(out, provider.Item{
			Provider:      provider.KindADO,
			RepoFullName:  c.project,
			Number:        w.ID,
			Title:         w.Fields.Title,
			State:         w.Fields.State,
			HTMLURL:       fmt.Sprintf("%s/%s/%s/_workitems/edit/%d", c.base, c.org, c.project, w.ID),
			UpdatedAt:     w.Fields.Changed,
			IsPullRequest: false,
			Reason:        provider.ReasonAssigned,
		})
	}
	sortItems(out)
	return out, nil
}

// --- Repo detail ------------------------------------------------------------

func (c *Client) repoBase(ref provider.RepoRef) string {
	return c.projPath("/_apis/git/repositories/" + url.PathEscape(ref.ID))
}

func (c *Client) ListBranches(ctx context.Context, ref provider.RepoRef) ([]provider.Branch, error) {
	var wire struct {
		Value []struct {
			Name     string `json:"name"`
			ObjectID string `json:"objectId"`
		} `json:"value"`
	}
	if err := c.get(ctx, withVersion(c.repoBase(ref)+"/refs?filter=heads/"), &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Branch, 0, len(wire.Value))
	for _, w := range wire.Value {
		out = append(out, provider.Branch{Name: strings.TrimPrefix(w.Name, "refs/heads/"), SHA: w.ObjectID})
	}
	return out, nil
}

func (c *Client) ListRecentCommits(ctx context.Context, ref provider.RepoRef, limit int) ([]provider.Commit, error) {
	if limit <= 0 {
		limit = commitLimit
	}
	path := fmt.Sprintf("%s/commits?searchCriteria.$top=%d", c.repoBase(ref), limit)
	var wire struct {
		Value []struct {
			CommitID  string `json:"commitId"`
			Comment   string `json:"comment"`
			RemoteURL string `json:"remoteUrl"`
			Author    struct {
				Name string `json:"name"`
				Date string `json:"date"`
			} `json:"author"`
		} `json:"value"`
	}
	if err := c.get(ctx, withVersion(path), &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Commit, 0, len(wire.Value))
	for _, w := range wire.Value {
		out = append(out, provider.Commit{
			SHA:     w.CommitID,
			Message: w.Comment,
			Author:  w.Author.Name,
			Date:    w.Author.Date,
			HTMLURL: w.RemoteURL,
		})
	}
	return out, nil
}

func (c *Client) GetReadme(ctx context.Context, ref provider.RepoRef) (string, string, error) {
	path := c.repoBase(ref) + "/items?path=/README.md&includeContent=true"
	var wire struct {
		Content string `json:"content"`
	}
	if err := c.get(ctx, withVersion(path), &wire); err != nil {
		var se *provider.StatusError
		if errors.As(err, &se) && se.Code == http.StatusNotFound {
			return "", "", nil
		}
		return "", "", err
	}
	htmlURL := fmt.Sprintf("%s/%s/%s/_git/%s?path=/README.md", c.base, c.org, c.project, url.PathEscape(ref.Name))
	return wire.Content, htmlURL, nil
}

// RepoPRs lists one repository's active PRs (repo-scoped).
func (c *Client) RepoPRs(ctx context.Context, ref provider.RepoRef) ([]provider.Item, error) {
	path := c.repoBase(ref) + "/pullrequests?searchCriteria.status=active"
	var wire struct {
		Value []wirePR `json:"value"`
	}
	if err := c.get(ctx, withVersion(path), &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Item, 0, len(wire.Value))
	for _, w := range wire.Value {
		out = append(out, c.prItem(w, ""))
	}
	sortItems(out)
	return out, nil
}

// RepoIssues is empty in v1: ADO work items are project-scoped, not repo-scoped,
// so they surface in the overview's assigned-work section instead.
func (c *Client) RepoIssues(ctx context.Context, ref provider.RepoRef) ([]provider.Item, error) {
	return nil, nil
}

// --- Clone URL --------------------------------------------------------------

// NormalizeCloneURL turns any ADO reference into an https _git clone URL,
// handling dev.azure.com (with or without a user@ prefix) and legacy
// {org}.visualstudio.com. Returns "" when it can't parse one.
func (c *Client) NormalizeCloneURL(repoURL string) string {
	s := strings.TrimSpace(repoURL)
	s = strings.TrimSuffix(s, "/")
	if s == "" {
		return ""
	}
	if i := strings.Index(s, "://"); i >= 0 {
		s = s[i+3:]
	}
	if at := strings.Index(s, "@"); at >= 0 {
		s = s[at+1:]
	}
	// Legacy: {org}.visualstudio.com/{project}/_git/{repo}
	if host := s[:strings.Index(s+"/", "/")]; strings.HasSuffix(host, ".visualstudio.com") {
		org := strings.TrimSuffix(host, ".visualstudio.com")
		rest := strings.TrimPrefix(s, host)
		return "https://dev.azure.com/" + org + rest
	}
	// Modern: dev.azure.com/{org}/{project}/_git/{repo}
	if strings.HasPrefix(s, "dev.azure.com/") {
		return "https://" + s
	}
	return ""
}

// sortItems orders rows most-recently-updated first (RFC3339 string compare).
func sortItems(rows []provider.Item) {
	for i := 1; i < len(rows); i++ {
		for j := i; j > 0 && rows[j].UpdatedAt > rows[j-1].UpdatedAt; j-- {
			rows[j], rows[j-1] = rows[j-1], rows[j]
		}
	}
}

var _ provider.Client = (*Client)(nil)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./provider/ado/ -race -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd app && git add provider/ado/
git commit -m "feat(app): add Azure DevOps provider client"
```

---

## Task 4: `app_ado_token.go` — ADO connection validation

Mirror `ValidateGitHubToken`: after saving ADO keys, confirm the org/project/PAT combination reaches ADO. Hitting `connectionData` proves the PAT; hitting the project's repositories endpoint proves org+project.

**Files:**
- Create: `app/app_ado_token.go`
- Test: `app/app_ado_token_test.go`

**Interfaces:**
- Produces: `func (a *App) ValidateADOConnection(org, project, pat string) ADOConnectionStatus`; `ADOConnectionStatus{Valid, Checked bool; Message string}` (JSON `valid`/`checked`/`message`).

- [ ] **Step 1: Write the failing test**

`app/app_ado_token_test.go`:

```go
package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestCheckADOConnection(t *testing.T) {
	t.Run("valid", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// connectionData and the repositories probe both 200.
			_, _ = w.Write([]byte(`{"authenticatedUser":{"id":"x"},"value":[]}`))
		}))
		defer srv.Close()
		st := checkADOConnection(srv.URL, "acme", "widgets", "pat", &http.Client{Timeout: 5 * time.Second})
		if !st.Valid || !st.Checked {
			t.Fatalf("want valid+checked, got %+v", st)
		}
	})

	t.Run("bad pat", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusUnauthorized)
		}))
		defer srv.Close()
		st := checkADOConnection(srv.URL, "acme", "widgets", "bad", &http.Client{Timeout: 5 * time.Second})
		if st.Valid || !st.Checked {
			t.Fatalf("want invalid+checked, got %+v", st)
		}
	})

	t.Run("missing fields", func(t *testing.T) {
		st := checkADOConnection("http://unused", "", "widgets", "pat", http.DefaultClient)
		if st.Valid || !st.Checked {
			t.Fatalf("missing org must be a clean invalid verdict, got %+v", st)
		}
	})
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test . -run TestCheckADOConnection -v`
Expected: FAIL — `checkADOConnection` undefined.

- [ ] **Step 3: Write minimal implementation**

`app/app_ado_token.go`:

```go
package main

// Post-save validation for a curated ADO connection. Saving org/project/PAT to
// the gateway env store delivers config; it does not prove ADO accepts it. The
// editor calls ValidateADOConnection after a save so a bad PAT or a wrong
// org/project surfaces immediately, not on the first failed read.

import (
	"context"
	"encoding/base64"
	"net/http"
	"time"
)

const adoAPIBase = "https://dev.azure.com"

// ADOConnectionStatus is the verdict returned to the UI.
type ADOConnectionStatus struct {
	Valid   bool   `json:"valid"`
	Checked bool   `json:"checked"`
	Message string `json:"message"`
}

// ValidateADOConnection checks whether ADO accepts the org/project/PAT. Bound to the UI.
func (a *App) ValidateADOConnection(org, project, pat string) ADOConnectionStatus {
	return checkADOConnection(adoAPIBase, org, project, pat, &http.Client{Timeout: 10 * time.Second})
}

// checkADOConnection probes the project's repositories endpoint (which requires
// a valid PAT AND a resolvable org+project). 200 → valid; 401/403 → rejected;
// anything else → inconclusive (leave the UI neutral).
func checkADOConnection(base, org, project, pat string, hc *http.Client) ADOConnectionStatus {
	if org == "" || project == "" || pat == "" {
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "org, project, and PAT are all required"}
	}
	uri := base + "/" + org + "/" + project + "/_apis/git/repositories?api-version=7.1"
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, uri, nil)
	if err != nil {
		return ADOConnectionStatus{Checked: false, Message: "could not build request"}
	}
	req.Header.Set("Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte(":"+pat)))
	req.Header.Set("Accept", "application/json")
	resp, err := hc.Do(req)
	if err != nil {
		return ADOConnectionStatus{Checked: false, Message: "could not reach Azure DevOps: " + err.Error()}
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusOK:
		return ADOConnectionStatus{Valid: true, Checked: true, Message: "Azure DevOps accepted the connection"}
	case resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusNonAuthoritativeInfo:
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "Azure DevOps rejected the PAT (401) — expired or wrong value"}
	case resp.StatusCode == http.StatusForbidden:
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "PAT accepted but lacks access to this org/project (403)"}
	case resp.StatusCode == http.StatusNotFound:
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "org or project not found (404) — check ADO_ORG / ADO_PROJECT"}
	default:
		return ADOConnectionStatus{Checked: false, Message: "inconclusive (Azure DevOps returned " + resp.Status + ")"}
	}
}
```

Note: ADO returns `203 Non-Authoritative Information` (a sign-in HTML redirect) for a bad PAT on some endpoints; treat it as a rejection alongside 401.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && go test . -run TestCheckADOConnection -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd app && git add app_ado_token.go app_ado_token_test.go
git commit -m "feat(app): add ADO connection validation"
```

---

## Task 5: `app_code.go` — fan-out cutover + RepoDetail signature

Rewrite the backend to build the set of configured providers per profile, fan reads across them, merge with provider tags and provider-prefixed section errors, and route detail by `RepoRef`. Delete the old `githubclient` package. Update the two existing backend tests to the new types.

**Files:**
- Modify: `app/app_code.go`
- Modify: `app/app_code_test.go`, `app/app_code_repodetail_test.go`
- Delete: `app/githubclient/`

**Interfaces:**
- Consumes: `provider`, `provider/github`, `provider/ado`.
- Produces: `func (a *App) CodeOverview(profile string) (CodeOverview, error)`; `func (a *App) RepoDetail(profile string, ref provider.RepoRef) (RepoDetailResult, error)`; `providersFor(profile) ([]provider.Client, bool, error)`. `CodeOverview.Repos []provider.Repo`; `PullRequests/Issues []provider.Item`; `Notifications []provider.Notification`. `RepoDetailResult.Branches []provider.Branch`, `Commits []provider.Commit`, `PRs/Issues []provider.Item`.

- [ ] **Step 1: Update the existing tests to the new shape (write the failing tests)**

In `app/app_code_test.go`: rename `TestGitHubOverview_*` call sites from `app.GitHubOverview("work")` to `app.CodeOverview("work")`; the `token_missing` assertions are unchanged (a profile with no GitHub and no ADO config still yields `TokenMissing: true`). Replace the `githubFetcher` fake with a `fakeProvider` implementing `provider.Client`; add a test that two providers' repos merge and are provider-tagged, and that one provider's error is prefixed and isolated:

```go
// fakeProvider is a provider.Client whose each method returns canned data or a
// canned error, for testing the fan-out/merge without a live server.
type fakeProvider struct {
	kind      provider.Kind
	repos     []provider.Repo
	prs       []provider.Item
	work      []provider.Item
	reposErr  error
}

func (f *fakeProvider) Kind() provider.Kind { return f.kind }
func (f *fakeProvider) ListRepos(context.Context) ([]provider.Repo, error) {
	return f.repos, f.reposErr
}
func (f *fakeProvider) SearchMyPRs(context.Context) ([]provider.Item, error)      { return f.prs, nil }
func (f *fakeProvider) ListAssignedWork(context.Context) ([]provider.Item, error) { return f.work, nil }
func (f *fakeProvider) ListBranches(context.Context, provider.RepoRef) ([]provider.Branch, error) {
	return nil, nil
}
func (f *fakeProvider) ListRecentCommits(context.Context, provider.RepoRef, int) ([]provider.Commit, error) {
	return nil, nil
}
func (f *fakeProvider) GetReadme(context.Context, provider.RepoRef) (string, string, error) {
	return "", "", nil
}
func (f *fakeProvider) RepoPRs(context.Context, provider.RepoRef) ([]provider.Item, error) {
	return nil, nil
}
func (f *fakeProvider) RepoIssues(context.Context, provider.RepoRef) ([]provider.Item, error) {
	return nil, nil
}
func (f *fakeProvider) NormalizeCloneURL(u string) string { return u }

func TestBuildCodeOverview_MergesAndTags(t *testing.T) {
	gh := &fakeProvider{kind: provider.KindGitHub, repos: []provider.Repo{{Provider: provider.KindGitHub, FullName: "me/gh"}}}
	ado := &fakeProvider{kind: provider.KindADO, repos: []provider.Repo{{Provider: provider.KindADO, FullName: "proj/ado"}}}
	out := buildCodeOverview(context.Background(), []provider.Client{gh, ado}, "work")
	if len(out.Repos) != 2 {
		t.Fatalf("want 2 merged repos, got %d", len(out.Repos))
	}
}

func TestBuildCodeOverview_ErrorIsolation(t *testing.T) {
	gh := &fakeProvider{kind: provider.KindGitHub, repos: []provider.Repo{{FullName: "me/gh"}}}
	ado := &fakeProvider{kind: provider.KindADO, reposErr: errors.New("boom")}
	out := buildCodeOverview(context.Background(), []provider.Client{gh, ado}, "work")
	if len(out.Repos) != 1 {
		t.Fatalf("healthy provider's repos must still render: got %d", len(out.Repos))
	}
	if !strings.Contains(out.ReposError, "ado:") {
		t.Fatalf("failed provider's error must be prefixed: %q", out.ReposError)
	}
}
```

In `app/app_code_repodetail_test.go`: change `buildRepoDetail` calls to pass a single `provider.Client` fake and a `provider.RepoRef`; the section assertions carry over. Update the `githubRepoFetcher` fake to implement the detail methods of `provider.Client`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && go test . -run 'TestCodeOverview|TestBuildCodeOverview|TestBuildRepoDetail' -v`
Expected: FAIL / build error — `CodeOverview` method + new `buildCodeOverview` signature don't exist yet.

- [ ] **Step 3: Write the implementation**

Rewrite `app/app_code.go`:
- Imports: replace `phantom-ink/githubclient` with `phantom-ink/provider`, `phantom-ink/provider/ado`, `phantom-ink/provider/github`.
- `CodeOverview` struct: change `Repos` to `[]provider.Repo`, `PullRequests`/`Issues` to `[]provider.Item`, `Notifications` to `[]provider.Notification`. Keep all JSON tags identical. Keep `TokenMissing`/`TokenInvalid` (semantics: missing = no provider configured; invalid = any provider 401).
- Add `providersFor`:

```go
// providersFor builds the set of providers a profile has configured in its
// gateway env. Presence is selection: GitHub needs GITHUB_TOKEN; ADO needs all
// of ADO_ORG/ADO_PROJECT/ADO_PAT. The bool reports whether ANY provider is
// configured (drives the "connect something" banner).
func (a *App) providersFor(profile string) ([]provider.Client, bool, error) {
	env, err := a.GetGatewayEnv(profile)
	if err != nil {
		return nil, false, err
	}
	var clients []provider.Client
	if tok := strings.TrimSpace(env["GITHUB_TOKEN"]); tok != "" {
		clients = append(clients, github.New(tok))
	}
	org := strings.TrimSpace(env["ADO_ORG"])
	proj := strings.TrimSpace(env["ADO_PROJECT"])
	pat := strings.TrimSpace(env["ADO_PAT"])
	if org != "" && proj != "" && pat != "" {
		clients = append(clients, ado.New(org, proj, pat))
	}
	anyConfigured := len(clients) > 0
	return clients, anyConfigured, nil
}
```

- Replace `GitHubOverview` with:

```go
// CodeOverview fetches the active profile's launchpad across every configured
// provider (GitHub, ADO), merged. Bound to the UI; one call drives the panel.
func (a *App) CodeOverview(profile string) (CodeOverview, error) {
	clients, any, err := a.providersFor(profile)
	if err != nil {
		return CodeOverview{Profile: profile}, err
	}
	if !any {
		return CodeOverview{Profile: profile, TokenMissing: true}, nil
	}
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	return buildCodeOverview(ctx, clients, profile), nil
}
```

- Rewrite `buildCodeOverview(ctx, clients []provider.Client, profile string) CodeOverview`: for each client, fan its `ListRepos`/`SearchMyPRs`/`ListAssignedWork` out concurrently (reuse the existing `run`/`wg`/`mu` structure). Append results into `out.Repos`/`out.PullRequests`/`out.Issues`. On a section error, append `fmt.Sprintf("%s: %s", client.Kind(), err)` to the matching `*Error` field (join multiples with `"; "`). If `provider.IsUnauthorized(err)` for any call, set `out.TokenInvalid = true`. For notifications: if a client implements `provider.Notifier`, call `ListNotifications` and merge into `out.Notifications`/`out.NotificationsError`. After the wait, sort `out.Repos` by `PushedAt` desc and `out.PullRequests`/`out.Issues` via `sortItems` (port the RFC3339 string-compare sort to operate on `[]provider.Item`, and add a `sortRepos` for `[]provider.Repo`).
- `RepoDetailResult` struct: change section slices to `provider.*`; keep JSON tags. Add `Provider provider.Kind` echo field (`json:"provider"`), keep `Owner`/`Repo`/`DefaultBranch`.
- Replace `RepoDetail`:

```go
// RepoDetail fetches one repository's detail view. The RepoRef (carried from
// the overview row the operator clicked) names the provider AND the repo, so
// routing needs no lookup.
func (a *App) RepoDetail(profile string, ref provider.RepoRef) (RepoDetailResult, error) {
	base := RepoDetailResult{Profile: profile, Provider: ref.Provider, Owner: ref.Owner, Repo: ref.Name, DefaultBranch: ref.DefaultBranch}
	clients, any, err := a.providersFor(profile)
	if err != nil {
		return base, err
	}
	if !any {
		base.TokenMissing = true
		return base, nil
	}
	var client provider.Client
	for _, c := range clients {
		if c.Kind() == ref.Provider {
			client = c
			break
		}
	}
	if client == nil {
		base.TokenMissing = true
		return base, nil
	}
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	return buildRepoDetail(ctx, client, profile, ref), nil
}
```

- Rewrite `buildRepoDetail(ctx, gh provider.Client, profile string, ref provider.RepoRef) RepoDetailResult` to call the client's detail methods with `ref` (branches, commits, readme, `RepoPRs`, `RepoIssues`), keeping the concurrent per-section fan-out and per-section error strings. Populate `Provider/Owner/Repo/DefaultBranch` from `ref`.
- Delete the now-unused `githubFetcher`/`githubRepoFetcher` interfaces and the `mergePullRequests`/`sortIssues` helpers (their logic now lives in `provider/github`), replacing them with `sortItems([]provider.Item)` and `sortRepos([]provider.Repo)`.
- Leave `DispatchRepoTask`, `LaunchInteractiveSession`, `sessionNameFor`, `deriveCloneDest`, `repoNameFromURL`, `normalizeCloneURL`, `runGitClone`, `openTerminalAt`, `OpenRepoLocally` in place for now (Task 6 touches clone).

- [ ] **Step 4: Delete the old package and build**

```bash
cd app && rm -rf githubclient && go build ./...
```

Expected: builds clean (nothing imports `githubclient` anymore).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd app && go test . -race -v -run 'Code|RepoDetail'`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd app && git add app_code.go app_code_test.go app_code_repodetail_test.go
git rm -r githubclient
git commit -m "feat(app): fan Code overview/detail across configured providers"
```

---

## Task 6: ADO-aware local clone

`OpenRepoLocally` must clone ADO repos too. ADO clone auth uses the profile's PAT injected via `http.extraheader` (host git usually has no ADO credential), while GitHub keeps using host credentials. `normalizeCloneURL` must recognize ADO hosts.

**Files:**
- Modify: `app/app_code.go`
- Modify/Test: `app/app_code_test.go` (clone-branch tests)

**Interfaces:**
- Consumes: `providersFor` (Task 5) for the ADO PAT.
- Produces: `runGitCloneAuth(cloneURL, dest, authHeader string) error` (package var, testable); `normalizeCloneURL` now returns ADO https `_git` URLs too.

- [ ] **Step 1: Write the failing tests**

Add to `app/app_code_test.go`:

```go
func TestNormalizeCloneURL_ADO(t *testing.T) {
	cases := map[string]string{
		"https://dev.azure.com/acme/widgets/_git/api":     "https://dev.azure.com/acme/widgets/_git/api",
		"https://acme@dev.azure.com/acme/widgets/_git/api": "https://dev.azure.com/acme/widgets/_git/api",
		"https://acme.visualstudio.com/widgets/_git/api":   "https://dev.azure.com/acme/widgets/_git/api",
		// GitHub still works.
		"git@github.com:o/r.git": "https://github.com/o/r.git",
	}
	for in, want := range cases {
		if got := normalizeCloneURL(in); got != want {
			t.Errorf("normalizeCloneURL(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestIsADOCloneURL(t *testing.T) {
	if !isADOCloneURL("https://dev.azure.com/acme/widgets/_git/api") {
		t.Fatal("dev.azure.com should be ADO")
	}
	if isADOCloneURL("https://github.com/o/r.git") {
		t.Fatal("github should not be ADO")
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && go test . -run 'TestNormalizeCloneURL_ADO|TestIsADOCloneURL' -v`
Expected: FAIL.

- [ ] **Step 3: Write the implementation**

In `app/app_code.go`:
- Extend `normalizeCloneURL` so that, before the GitHub logic, it detects ADO hosts (`dev.azure.com`, `*.visualstudio.com`) and returns the normalized `https://dev.azure.com/{org}/{project}/_git/{repo}` form (reuse the same host-stripping approach the ADO client uses; keep the `_git` path intact rather than forcing a two-segment owner/repo). GitHub URLs fall through to the existing logic unchanged.
- Add:

```go
// isADOCloneURL reports whether a normalized clone URL points at Azure DevOps.
func isADOCloneURL(cloneURL string) bool {
	return strings.Contains(cloneURL, "dev.azure.com/") || strings.Contains(cloneURL, ".visualstudio.com/")
}

// runGitCloneAuth clones with an inline Authorization header (never persisted to
// the clone's config). Used for ADO, where host git has no credential. A package
// var so a test can assert the header-carrying branch without a network.
var runGitCloneAuth = func(cloneURL, dest, authHeader string) error {
	cmd := exec.Command("git", "-c", "http.extraheader=Authorization: "+authHeader, "clone", cloneURL, dest)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		if msg := strings.TrimSpace(stderr.String()); msg != "" {
			return fmt.Errorf("git clone: %s", msg)
		}
		return fmt.Errorf("git clone: %w", err)
	}
	return nil
}
```

- In `OpenRepoLocally`, after computing `cloneURL := normalizeCloneURL(repoURL)`, branch on provider:

```go
		if isADOCloneURL(cloneURL) {
			env, err := a.GetGatewayEnv(profile)
			if err != nil {
				return "", err
			}
			pat := strings.TrimSpace(env["ADO_PAT"])
			if pat == "" {
				return "", fmt.Errorf("profile %q has no ADO_PAT to clone %s", profile, cloneURL)
			}
			auth := "Basic " + base64.StdEncoding.EncodeToString([]byte(":"+pat))
			if err := runGitCloneAuth(cloneURL, dest, auth); err != nil {
				return "", err
			}
		} else if err := runGitClone(cloneURL, dest); err != nil {
			return "", err
		}
```

Add `"encoding/base64"` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test . -race -v -run 'Clone|Code|RepoDetail'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd app && git add app_code.go app_code_test.go
git commit -m "feat(app): clone ADO repos with PAT via http.extraheader"
```

---

## Task 7: Regenerate Wails bindings + frontend store

Regenerate bindings for the changed Go surface, then teach the store the provider dimension: provider fields on `Repo`/`Issue`, a `RepoRef` for detail calls, and provider-aware dispatch URLs.

**Files:**
- Regenerate: `app/frontend/wailsjs/go/main/App.{d.ts,js}` (+ `models.ts` if present)
- Modify: `app/frontend/src/lib/stores/code.svelte.ts`

**Interfaces:**
- Consumes: `CodeOverview(profile)`, `RepoDetail(profile, ref)`, `ValidateADOConnection(org, project, pat)`.
- Produces: `Repo.provider`, `Repo.id`, `Issue.provider`, `Issue.repo_id`, `RepoDetail.provider`; `codeState.openDetail` passing a `RepoRef`.

- [ ] **Step 1: Regenerate bindings**

Run: `just app-contract-gen` is unrelated; regenerate Wails bindings via a dev build:
```bash
cd app && wails generate module
```
(If `wails generate module` is unavailable, `just app-build` regenerates `wailsjs` as a side effect — a full build is acceptable here.)
Verify `App.d.ts` now shows `CodeOverview(arg1:string)`, `RepoDetail(arg1:string,arg2:main.RepoRef)` (or the generated `provider.RepoRef` alias), and `ValidateADOConnection(...)`.

- [ ] **Step 2: Write the failing check**

Update `code.svelte.ts` types and calls, then rely on `npm run check` as the gate (svelte-check fails on the old `GitHubOverview`/4-arg `RepoDetail` references until updated). First update the interfaces:

```ts
export interface Repo {
  provider: 'github' | 'ado';
  id: string;
  owner: string;
  name: string;
  full_name: string;
  description: string;
  html_url: string;
  clone_url: string;
  default_branch: string;
  pushed_at: string;
  stars: number;
  open_issues: number;
}

export interface Issue {
  provider: 'github' | 'ado';
  repo_full_name: string;
  repo_id: string;
  number: number;
  title: string;
  state: string;
  html_url: string;
  updated_at: string;
  user: string;
  draft: boolean;
  is_pull_request: boolean;
  reason: string;
}

export interface RepoRef {
  provider: 'github' | 'ado';
  owner: string;
  name: string;
  id: string;
  clone_url: string;
  default_branch: string;
}
```

Add `provider` to `RepoDetail` interface (`provider: 'github' | 'ado';`).

- [ ] **Step 3: Update the calls**

- In `refresh`, change `(a as any).GitHubOverview(profile)` → `(a as any).CodeOverview(profile)`.
- In `openDetail`, build and pass a `RepoRef`:

```ts
    const ref: RepoRef = {
      provider: repo.provider,
      owner: repo.owner,
      name: repo.name,
      id: repo.id,
      clone_url: repo.clone_url,
      default_branch: repo.default_branch,
    };
    const d = (await (a as any).RepoDetail(profile, ref)) as RepoDetail;
```

- In the PR/issue→`DispatchTarget` mapping (CodePanel builds these — see Task 8), the `repoURL` must stop hardcoding github.com. Add a helper the panel uses:

```ts
/** Best clone URL for a PR/issue row, provider-aware. */
export function itemCloneURL(i: Issue): string {
  // A row carries its html_url; httpsCloneURL trims PR/issue path segments and
  // appends .git, working for both github.com and dev.azure.com/_git URLs.
  return httpsCloneURL({ repoURL: '', htmlURL: i.html_url } as DispatchTarget);
}
```

(For ADO `_git` URLs `httpsCloneURL`'s existing regex keeps host + first two path segments, which is wrong for `_git/{repo}`; extend `httpsCloneURL` to detect `/_git/` and return everything up to and including the repo segment as-is. Add a unit assertion in a comment; svelte has no unit runner here, so keep the logic obvious and covered by manual verification in Task 10.)

- [ ] **Step 4: Run the check to verify it passes**

Run: `cd app/frontend && npm run check`
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
cd app && git add frontend/wailsjs frontend/src/lib/stores/code.svelte.ts
git commit -m "feat(app): provider-aware Code store + regenerated bindings"
```

---

## Task 8: CodePanel — provider badges + work-item labelling

Render the provider on every repo/PR/work-item row and set `provider`/`id`/`repo_id` when building dispatch targets and opening detail.

**Files:**
- Modify: `app/frontend/src/lib/panels/CodePanel.svelte`

**Interfaces:**
- Consumes: `Repo.provider`, `Issue.provider`, `itemCloneURL` (Task 7).

- [ ] **Step 1: Add a badge helper + render it**

At the top of the `<script>`, add a tiny label map and a snippet/helper:

```ts
  const PROVIDER_LABEL: Record<string, string> = { github: 'GitHub', ado: 'Azure DevOps' };
  const providerLabel = (p: string) => PROVIDER_LABEL[p] ?? p;
```

Render a badge next to each repo `full_name` (line ~173 and ~443), each PR/issue `repo_full_name` (line ~366), by adding, immediately after the `<code class="repo">…</code>`:

```svelte
            <span class="provider-badge provider-{r.provider}" title={providerLabel(r.provider)}>{r.provider === 'ado' ? 'ADO' : 'GH'}</span>
```

(Use the matching row variable — `repo`, `r`, `row`, or `i` — in each location.)

- [ ] **Step 2: Provider-aware dispatch targets**

Where the panel builds a `DispatchTarget` from a repo (line ~63) and from a PR/issue (line ~103), replace the hardcoded `repoURL: `https://github.com/${i.repo_full_name}.git`` (line ~106) with `repoURL: itemCloneURL(i)` and, for the repo case, `repoURL: r.clone_url`. Import `itemCloneURL` from the store.

- [ ] **Step 3: Label the work-items section for ADO**

The overview "assigned" section (issues) now mixes GitHub issues and ADO work items. In its header/row, when a row's `provider === 'ado'`, show its type-agnostic label "work item" instead of "issue" (a small inline `{i.provider === 'ado' ? 'work item' : 'issue'}` where the kind word appears). No structural change — the `Issue` shape already carries everything.

- [ ] **Step 4: Add badge styles**

In the panel `<style>`:

```css
  .provider-badge {
    font-size: 0.58rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding: 0.05rem 0.3rem;
    border-radius: 3px;
    margin-left: 0.35rem;
    border: 1px solid var(--border);
    color: var(--text-muted);
    vertical-align: middle;
  }
  .provider-ado { color: #2b88d8; border-color: #2b88d8; }
  .provider-github { color: var(--text-muted); }
```

- [ ] **Step 5: Verify**

Run: `cd app/frontend && npm run check`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
cd app && git add frontend/src/lib/panels/CodePanel.svelte
git commit -m "feat(app): show provider badges + work-item labels in Code panel"
```

---

## Task 9: GatewayEnvEditor — ADO connection seeder + validation

Follow the existing generic-env pattern: a convenience button that seeds the three ADO rows, and auto-validation on save mirroring the `GITHUB_TOKEN` block.

**Files:**
- Modify: `app/frontend/src/lib/components/GatewayEnvEditor.svelte`

**Interfaces:**
- Consumes: `ValidateADOConnection(org, project, pat)`.

- [ ] **Step 1: Seed button**

Add a helper and a button next to `+ variable` (line ~291):

```ts
  function addADOConnection() {
    const want = ['ADO_ORG', 'ADO_PROJECT', 'ADO_PAT'];
    const next = [...rows];
    for (const key of want) {
      if (!next.some((r) => r.key.trim() === key)) next.push({ key, value: '', reveal: key !== 'ADO_PAT' });
    }
    rows = next;
  }
```

```svelte
        <button class="gw-btn" onclick={addADOConnection} title="Seed ADO_ORG / ADO_PROJECT / ADO_PAT rows for an Azure DevOps connection">+ ADO connection</button>
```

- [ ] **Step 2: Validate ADO on save**

In `save()`, after the existing `GITHUB_TOKEN` validation block, add:

```ts
      const org = env['ADO_ORG'], project = env['ADO_PROJECT'], pat = env['ADO_PAT'];
      if (org && project && pat) {
        try {
          const st = await a.ValidateADOConnection(org, project, pat);
          if (st.checked && !st.valid) {
            notifications.warning(`ADO connection saved, but ${st.message}`);
          }
        } catch { /* validation is best-effort */ }
      }
```

- [ ] **Step 3: Verify**

Run: `cd app/frontend && npm run check`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
cd app && git add frontend/src/lib/components/GatewayEnvEditor.svelte
git commit -m "feat(app): seed + validate ADO connection in gateway env editor"
```

---

## Task 10: Full verification + docs

**Files:**
- Modify: `docs/architecture-overview.md` or `CLAUDE.md` MCP/Code note if it names GitHub-only (only if such a line exists).

- [ ] **Step 1: Backend full suite**

Run: `cd app && go test ./... -race`
Expected: PASS (no `githubclient` references remain).

- [ ] **Step 2: Backend vet + build**

Run: `cd app && go vet ./... && go build ./...`
Expected: clean.

- [ ] **Step 3: Frontend check**

Run: `cd app/frontend && npm run check`
Expected: 0 errors.

- [ ] **Step 4: Contract freshness (guard against unrelated drift)**

Run: `just app-contract-gen` then `git diff --exit-code app/internal/contract`
Expected: no diff.

- [ ] **Step 5: Manual smoke (documented, not automated)**

With a profile that has both `GITHUB_TOKEN` and the `ADO_*` trio saved in gateway env: open the Code panel, confirm repos from both hosts appear with correct badges, open an ADO repo's detail (branches/commits/PRs/README render, issues section empty), and confirm "Clone + terminal" on an ADO repo clones via PAT. Note results in the PR description.

- [ ] **Step 6: Commit any doc touch-ups and open the PR**

```bash
cd app && git add -A && git commit -m "docs(app): note ADO as a Code-panel provider" || true
git push -u origin feat/ado-code-provider
```

---

## Self-review notes

- **Spec coverage:** provider interface (Task 1) ✓; github port with constructor-bound token (Task 2) ✓; ADO client with 7.1 endpoints, Basic auth, connectionData identity, WIQL work items, README-404 fail-soft (Task 3) ✓; presence-based `providersFor` + fan-out merge + provider-prefixed errors + `RepoDetail(RepoRef)` (Task 5) ✓; ADO PAT-in-extraheader clone (Task 6) ✓; gateway-env config + ADO validation (Tasks 4, 9) ✓; frontend aggregation + badges + work-item labels (Tasks 7, 8) ✓; notifications GitHub-only (Task 5 via `Notifier`) ✓. Deferred items (multi-org, notifications parity, `repos` provider column) intentionally absent.
- **Type consistency:** `provider.Item` is the single PR/issue/work-item type end to end; `RepoRef` fields match between Go (`provider.RepoRef`) and TS (`RepoRef`); `CodeOverview`/`RepoDetailResult` JSON tags are unchanged so only new fields (`provider`, `id`, `repo_id`) are additive to the frontend.
- **Reconciliation vs spec §5:** the spec described a "Git providers section with sub-forms"; the codebase's actual credential surface is the generic `GatewayEnvEditor`. Tasks 4/9 deliver the spec's *intent* (ADO configurable + validated in the Profiles panel) idiomatically via that editor (seed button + auto-validate on save), rather than a bespoke form — consistent with the existing `GITHUB_TOKEN` pattern.
