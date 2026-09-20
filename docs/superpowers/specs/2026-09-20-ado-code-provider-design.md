# Design: Azure DevOps as a second Code-panel provider

- **Date:** 2026-09-20
- **Status:** Approved design, pre-implementation
- **Scope:** phantom-ink app (`app/`) — Code panel, Go backend, Profiles panel
- **Branch:** `feat/ado-code-provider`

## Problem

The app's **Code** panel is hardwired to GitHub. GitHub specifics — API
version, Bearer auth, search syntax, `github.com` URL shapes, and the
`GITHUB_TOKEN` env key — are spread across `app/app_code.go` and
`app/githubclient/` at roughly 8.5/10 coupling. There is no provider
concept in the profile struct, the DB, or the credential store.

We want to add **Azure DevOps (ADO)** as a second provider and, in doing
so, turn per-profile git configuration into a real selection: a single
profile can connect to GitHub, ADO, or both, and the Code panel
aggregates across whatever is connected.

Out of the Code panel's scope: git *identity* (user.name / user.email /
signing). That remains shell-profiler / direnv territory.

## Decisions (settled during brainstorming)

1. **Binding model: mixed within a profile.** One profile may talk to
   both GitHub and ADO at once; the panel aggregates. Every repo and list
   item is therefore provider-tagged.
2. **ADO v1 surfaces:** repos + PR list + repo detail; work items
   (issues equivalent); begin-work lanes (dispatch / interactive session
   / clone). **Deferred:** ADO notifications-inbox parity.
3. **ADO scope: one org + one project per profile.** No project
   enumeration; the PAT maps 1:1 to the org.
4. **Enablement: presence-based (Approach A).** A provider is "selected"
   for a profile when its credentials/config exist in the gateway env
   store. No DB migration, no explicit on/off toggle.
5. **ADO API version pinned to `7.1`.**
6. **ADO clone auth:** inject the PAT via
   `git -c http.extraheader="Authorization: Basic base64(:PAT)"` so the
   token never persists in the remote URL. (GitHub clone continues to use
   host git credentials.)

## Architecture

### 1. Provider package

Generalize `app/githubclient` → **`app/provider`**. The defining change
from the current code: **configuration binds at client construction, not
per method call.** Today every `githubclient` method takes a `token`
argument, which only works because GitHub needs nothing else. ADO needs
org + project + PAT, so each client is constructed with its own config
and the read methods drop the token parameter.

```go
package provider

type Kind string

const (
    KindGitHub Kind = "github"
    KindADO    Kind = "ado"
)

// RepoRef identifies a repo across providers. The frontend receives it
// on each repo row and echoes it back on detail calls, so RepoDetail
// routing is deterministic and needs no server-side lookup.
type RepoRef struct {
    Provider      Kind
    Owner         string // GitHub: org/user;  ADO: project
    Name          string
    ID            string // ADO repo GUID; empty for GitHub
    CloneURL      string
    DefaultBranch string
}

// Client is the read surface the Code panel needs. One instance per
// (profile, provider), constructed from that provider's config.
type Client interface {
    Kind() Kind
    ListRepos(ctx context.Context) ([]Repo, error)
    SearchMyPRs(ctx context.Context) ([]Item, error)      // authored + review-requested
    ListAssignedWork(ctx context.Context) ([]Item, error) // GH issues / ADO work items assigned to me
    ListBranches(ctx context.Context, ref RepoRef) ([]Branch, error)
    ListRecentCommits(ctx context.Context, ref RepoRef, limit int) ([]Commit, error)
    GetReadme(ctx context.Context, ref RepoRef) (md string, htmlURL string, err error)
    RepoPRs(ctx context.Context, ref RepoRef) ([]Item, error)
    NormalizeCloneURL(url string) (string, error)
}
```

Shared result types (`Repo`, `Item`, `Branch`, `Commit`) live in the
`provider` package and each carries a `Provider Kind` field so merged
lists remain routable and renderable. `Item` is a unified shape covering
GitHub PRs/issues and ADO PRs/work-items (title, number/id, url, state,
author, updated-at, kind).

Two implementations:

- **`provider/github`** — the existing `githubclient` code moved in
  wholesale, with `token` lifted from method args to a constructor
  (`github.New(token, baseURL)`). Behaviour otherwise unchanged.
- **`provider/ado`** — new (see §4).

Base URL stays injectable in both implementations for `httptest`.

### 2. Config & credential model (no DB migration)

All per-profile, stored in the existing encrypted **gateway env** store
(already per-profile, already has a UI editor in the Profiles panel):

| Provider | Keys | Enabled when |
|---|---|---|
| GitHub | `GITHUB_TOKEN` (+ existing Enterprise host handling) | token non-empty |
| ADO | `ADO_ORG`, `ADO_PROJECT`, `ADO_PAT` | all three non-empty |

`ADO_ORG` is the org **name** (the client builds
`https://dev.azure.com/{org}`).

A new helper in `app_code.go`:

```go
func (a *App) providersFor(profile string) ([]provider.Client, error)
```

reads the profile's gateway env and constructs the enabled clients.
**Presence = selection.** A missing or partially-configured provider is
simply absent — never a hard error.

### 3. Backend fan-out (`app_code.go`)

- **`GitHubOverview` → `CodeOverview(profile)`** (Wails method renamed;
  frontend binding regenerated). Fans out across enabled providers,
  merges repos / PRs / work-items, tags each, and sorts (repos by pushed
  date, items by updated-at). The client-side repo filter is unchanged.
- **Per-provider, per-section errors.** The existing `CodeOverview`
  result already carries per-section error fields; extend them with a
  provider dimension so "ADO repos: 401" surfaces without blanking
  GitHub's repos. Fail-soft throughout.
- **`RepoDetail(profile, ref RepoRef)`** routes to the right client by
  `ref.Provider`. (Replaces the current
  `RepoDetail(profile, owner, repo, defaultBranch)` signature; the
  frontend now passes the whole `RepoRef` it was given.)
- **Notifications inbox stays GitHub-only** — populated only when GitHub
  is enabled; ADO contributes nothing to this section in v1.
- **Begin-work lanes:**
  - *Dispatch to fleet* and *interactive session* are provider-agnostic
    (they forward profile env) — unchanged.
  - *Clone locally* (`OpenRepoLocally`) gains ADO clone-URL
    normalization and, for ADO, injects the PAT via
    `git -c http.extraheader="Authorization: Basic <base64(":"+PAT)>" clone <url>`.
    GitHub clone continues to rely on host git credentials.

### 4. ADO client specifics

- **Base:** `https://dev.azure.com/{org}`
- **Auth:** `Authorization: Basic base64(":" + PAT)`
- **API version:** `api-version=7.1` on every request
- **Repos:** `GET /{project}/_apis/git/repositories`
- **Branches:** `GET /{project}/_apis/git/repositories/{repoId}/refs?filter=heads/`
- **Commits:** `GET /{project}/_apis/git/repositories/{repoId}/commits?searchCriteria.$top={limit}`
- **README:** `GET /{project}/_apis/git/repositories/{repoId}/items?path=/README.md&includeContent=true`
  — fail-soft when absent (404 → empty readme, not an error).
- **PRs:** `GET /{project}/_apis/git/pullrequests?searchCriteria.creatorId={id}`
  plus a reviewer-scoped query. ADO requires a GUID for "me": resolve it
  once per client via `_apis/connectionData` (`authenticatedUser.id`) and
  cache it on the client instance.
- **Work items (assigned to me):** WIQL
  `POST /{project}/_apis/wit/wiql` with
  `SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = @Me AND [System.State] <> 'Closed'`
  (the `@Me` macro avoids the GUID dance), then batch-hydrate via
  `GET /_apis/wit/workitems?ids={csv}&api-version=7.1`.
- **Clone URL:** `https://dev.azure.com/{org}/{project}/_git/{repo}`.
- **`NormalizeCloneURL`** also handles legacy `{org}.visualstudio.com`
  host forms.

### 5. Frontend

**`CodePanel.svelte`:**
- Same three overview sections, now aggregated across providers.
- Each repo / PR / work-item row shows a small **provider badge**
  (GitHub mark vs. Azure mark).
- The detail view echoes the row's `RepoRef` (including `Provider`) back
  to `RepoDetail`. The "Issues" block renders GitHub issues or ADO work
  items depending on the row's provider.
- Begin-work lanes unchanged in the UI.

**Profiles panel:**
- A "Git providers" section with two sub-forms:
  - **GitHub** — token + the existing validate button
    (`ValidateGitHubToken`).
  - **ADO** — org, project, PAT + a new validate button. Validation hits
    `_apis/connectionData` and reports org/project reachability,
    mirroring `ValidateGitHubToken` / `GitHubTokenStatus`.

### 6. Data flow

```
Profiles panel  ──edits──▶  gateway env store (encrypted, per-profile)
                                     │
CodePanel ──CodeOverview(profile)──▶ app_code.go
                                     │  providersFor(profile)
                                     ├─▶ provider/github (if GITHUB_TOKEN)
                                     └─▶ provider/ado    (if ADO_* trio)
                                     │  merge + tag + sort, per-provider errors
CodePanel ◀── CodeOverview{repos[], prs[], work[], notifications, errors} ──┘

CodePanel ──RepoDetail(profile, RepoRef)──▶ app_code.go ──route by ref.Provider──▶ client
```

## Error handling

- Provider absent/misconfigured → omitted silently (not an error).
- Provider reachable but a section fails → that
  `(provider, section)` error is surfaced in the result; other providers
  and sections render normally.
- ADO README 404 → empty readme, not an error.
- Clone failures surface as they do today.

## Testing

- **`provider/github`, `provider/ado`:** table-driven tests against
  `httptest` servers via the injectable base URL. ADO coverage:
  connectionData identity resolution, PR creator/reviewer queries, WIQL
  round-trip, README-absent path.
- **`app_code` fan-out:** fake `provider.Client`s asserting merge,
  provider-tagging, sort order, and **error isolation** (one provider's
  401 does not blank the other's results).
- **ADO clone-URL normalization:** unit tests for `dev.azure.com` and
  `{org}.visualstudio.com` forms.
- **Frontend:** `cd app/frontend && npm run check` (svelte-check) after
  the panel changes; regenerate Wails bindings for the renamed/changed
  methods.

## Deferred (explicitly out of scope)

- Multi-org / multi-project ADO connections.
- ADO notifications-inbox parity.
- A `provider` column on the local `repos` (clones) table — on-disk
  clones are provider-agnostic; add only if a future feature needs it.
- Any change to git identity handling (shell-profiler territory).

## Affected files (anticipated)

- `app/githubclient/` → `app/provider/` (github impl moved; token→ctor)
- `app/provider/provider.go` (new — interface + shared types)
- `app/provider/ado/` (new — ADO client)
- `app/app_code.go` (providersFor, CodeOverview rename, RepoDetail
  signature, ADO clone path)
- `app/app_github_token.go` (add ADO validation alongside GitHub)
- `app/frontend/src/lib/panels/CodePanel.svelte` (badges, RepoRef,
  work-items rendering)
- Profiles panel component (ADO config sub-form)
- `app/frontend/wailsjs/*` (regenerated bindings)
