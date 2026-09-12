# Code Page — GitHub Launchpad (Design Spec)

**Date:** 2026-09-12
**Status:** Implemented (v1). Scope below is the shipped surface.
**Surface:** phantom-ink Wails app — new first-class `Code` panel (⌘9).

## 1. Motivation

Starting work today means leaving the app: open a browser, find the repo or the
PR, copy the URL, come back, retype a prompt into the Jobs form. Two problems
compound:

- **No single pane for the profile's GitHub.** Each profile already curates a
  `GITHUB_TOKEN` in its gateway env, and the fleet already clones with it — but
  nothing in the app shows what that token can see. "What's waiting on me?" is
  answered in a browser tab, per profile, by hand.
- **The distance from "I see the thing" to "an agent is on it" is too long.**
  The hub can already run a worker against a repo; the missing piece is the
  hand-off from a specific PR/issue row to a seeded, editable prompt.

The Code page closes both: read-only visibility over the active profile's
GitHub account, and a one-click dispatch from any row.

## 2. Goals / non-goals

**In (v1)**
1. Read-only visibility for the ACTIVE profile: repositories, open PRs
   (authored + review-requested), open assigned issues, notifications.
2. "Dispatch fleet agent" from any repo/PR/issue row — a modal with templated
   but **editable** prompts plus an agent picker, submitted through the
   existing hub task-submit path.
3. Deep-link (`open ↗`) to github.com for every row.

**Out (deliberately)** — SQLite persistence or caching of GitHub data;
interactive-session launch; local clone; a single-repo file/commit browser; any
new credential UI. **No DB migration in this PR.**

Data is fetched live and held in frontend state. A page-open is ~5 REST calls
against a 5000 req/hr authenticated budget, so caching would buy nothing and
cost correctness (a cache is a place for one profile's rows to survive into
another profile's view).

## 3. Security & profile scoping

Profiles are foundational here, not a filter applied late:

- The token is the profile's **existing** curated `GITHUB_TOKEN`, read through
  `GetGatewayEnv(profile)` (decrypted host-side) on **every** call. No new
  secret, no new credential storage, no new UI for credentials.
- Nothing is cached across calls or profiles. Switching profiles resets the
  store and refetches; a response that lands after a switch is **dropped**
  (the overview echoes back its `profile` so the panel can compare).
- `githubclient` refuses to issue a request with an empty token rather than
  falling back to an unauthenticated call — an unauthenticated fetch would
  return *some other* public view under the profile's name.
- Dispatch always sends `workspace_profile`, so the agent runs with the same
  profile's credentials it was launched from.

## 4. Architecture

```
CodePanel.svelte ──(getApi)──> App.GitHubOverview(profile)   [app/app_code.go]
       │                            │  token = GetGatewayEnv(profile)["GITHUB_TOKEN"]
       │                            ▼
       │                      githubclient  ──> api.github.com   (read-only, 5 calls, concurrent)
       │
 codeState (rune store) ── shared by the sections AND the dispatch modal
       │
DispatchModal ──> App.DispatchRepoTask ──> client.SubmitTask ──> POST /api/hub/tasks  (existing)
```

### `app/githubclient/` (new package)

A tiny READ-ONLY REST client, not a GitHub SDK. Base URL injectable so tests
run against an `httptest.Server` — the same shape as the existing direct-call
precedent in `app/app_github_token.go`. `Authorization: Bearer <token>`,
`Accept: application/vnd.github+json`, per-request context, 10s timeout, token
passed per call.

| Method | Endpoint |
|---|---|
| `ListRepos` | `GET /user/repos?sort=pushed&per_page=50&affiliation=owner,collaborator,organization_member` |
| `SearchPRsAuthored` | `GET /search/issues?q=is:open is:pr author:@me` |
| `SearchPRsReviewRequested` | `GET /search/issues?q=is:open is:pr review-requested:@me` |
| `SearchIssuesAssigned` | `GET /search/issues?q=is:open is:issue assignee:@me` |
| `ListNotifications` | `GET /notifications` |

Returned structs hold only what the UI renders. Two mapping decisions worth
recording:

- Search hits carry no repository object, only `repository_url`
  (`https://api.github.com/repos/o/n`) — `repo_full_name` is derived from it.
- A notification's `subject.url` is an **API** URL and is not browsable.
  It is rewritten to a `github.com` link for PR and issue subjects; anything
  else (releases, discussions, checks) falls back to the repository page rather
  than handing the UI a dead `api.github.com` link.

Non-2xx responses return a typed `*StatusError`, so `IsUnauthorized(err)`
distinguishes "your token is bad" (401) from "GitHub is having a day" (5xx).

### `app/app_code.go`

`GitHubOverview(profile) (CodeOverview, error)`:

1. Resolve the token from the profile's gateway env.
2. Empty token → `CodeOverview{TokenMissing: true}` **and no error**, so the
   panel renders an actionable "connect a token" banner instead of an error
   toast.
3. Fan the five reads out concurrently (goroutines + `sync.WaitGroup`; no new
   dependency — `errgroup` isn't already vendored and wouldn't help, since we
   deliberately do **not** cancel siblings on the first failure).
4. Fold into one struct with a **per-section error string**
   (`ReposError`, `PullRequestsError`, `IssuesError`, `NotificationsError`).
   This is the central resilience decision: fine-grained PATs scope
   notifications separately from repositories, so a 401 on `/notifications`
   must not blank the repositories list. A 401 on **any** section additionally
   sets `TokenInvalid`, which the panel surfaces as a reconnect banner.

PRs merge authored + review-requested, deduped on `html_url`; a PR that is both
mine and awaiting my review appears once, tagged with **both** reasons, so the
row explains why it is listed. Rows sort updated-desc (`updated_at` is RFC3339,
so a string compare is already chronological).

`DispatchRepoTask(DispatchRepoRequest)` builds a `brainbox.SubmitTaskRequest`
and returns `client.SubmitTask(...)` — fire-and-forget, matching Jobs.

**Deviation from the original task note:** the agent picker uses the existing
`App.ListAgentRoles()`, not `App.ListAgents()`. `ListAgents()` returns detected
local CLI binaries (claude, codex, …); the hub's `agent_name` must be a
brainbox agent role (`worker`, `reviewer`, …), which is what `ListAgentRoles()`
returns and what the Jobs panel already uses. Still no new backend method.

### Frontend

- **Registration** (3 precedented touch-points): a `code` entry in
  `lib/panels.ts` (⌘9 — verified free), a branch in `layout/AppShell.svelte`,
  and `'9': 'code'` in `App.svelte`'s keyboard panel map so the advertised
  shortcut actually fires. `Sidebar.svelte` picks the panel up from the
  registry automatically.
- **`lib/stores/code.svelte.ts`** — `codeState`, a class-based rune store
  (following `stores/streamLive.svelte.ts`). It holds the four result lists,
  the loading/error/token flags, the repository filter, **and** the dispatch
  modal's target/prompt/agent. The modal is a sibling of the sections, not a
  child, so shared state in the store is what avoids prop-drilling a target
  three levels down. It also owns the prompt templates (they are data, and
  testable as data).
- **`lib/panels/CodePanel.svelte`** — one `GitHubOverview(activeProfile)` call
  fills three `CardExpander` cards: *needs attention* (default open),
  *notifications*, *repositories* (with a client-side filter). The active
  profile comes from the app's global `profileState` — no per-panel profile
  picker. Refetch on profile change and on a manual ⟳.
- **`lib/components/DispatchModal.svelte`** — template chips **seed** an
  editable textarea (PR: "Review this PR" / "Fix failing CI"; issue: "Address
  this issue"; repo: "Start work"), each expanding with repo, `#number`, title,
  and URL baked in. Agent picker defaults to `worker`.

## 5. Verification

- `cd app && go test ./... -race` — includes the new `githubclient` httptest
  suite (written first) and `app_code` tests covering the merge/dedupe/tagging,
  a single failing section not blanking the page, a half-failed PR section
  keeping its good half, 401 → `TokenInvalid`, token-missing as a value not an
  error, and dispatch carrying `workspace_profile`.
- `cd app/frontend && npm run check` — the three new files add zero errors and
  zero warnings (the repo has a pre-existing baseline of 21/53).
- Wails bindings regenerated (`wails generate module`) and committed.

## 6. Follow-ups (not in v1)

- Per-repo drill-down (branches, recent commits, checks).
- Dispatch straight into an interactive session, not only an autonomous task.
- Notification read/dismiss (a write, so it needs its own scoping pass).
- Watching dispatched tasks inline instead of linking out to Jobs.
