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

---

# Slice A — Begin-work lanes

v1 had exactly one way to act on a row: ⚡ Dispatch, which hands the work to an
autonomous fleet agent and walks away. That is the right default for "fix the
failing CI on #42" and the wrong one for "I want to poke at this myself." Slice A
adds the two lanes where the *operator* is the one working, closing the second
v1 follow-up ("dispatch straight into an interactive session").

## 1. The three lanes

| Lane | Where the work happens | Who drives | Entry point |
|---|---|---|---|
| Autonomous task | fleet agent container (hub) | nobody — it opens a PR | `Begin work ▾` → **Autonomous task** (default) |
| Interactive session | brainbox container session | you, attached | `Begin work ▾` → **Interactive session** |
| Host terminal | this Mac, in the profile's workspace | you, in iTerm/Terminal | repo row → `Clone + terminal` |

All three are profile-scoped. The task carries `workspace_profile`; the session
carries `workspace_profile` + `workspace_home`; the host clone lands under the
profile's own `workspace_home`. A lane started under profile A cannot touch
profile B's tree or credentials.

## 2. Host lane — `OpenRepoLocally(profile, repoURL) (string, error)`

`app/app_code.go`. Resolves `findProfile(profile).WorkspaceHome`, derives
`<workspaceHome>/code/<repo>`, and:

- **Destination missing** → `git clone <httpsURL> <dest>` (parent `code/` dir
  created first), then open a terminal in it.
- **Destination present** → open it **as-is**. No clone, and deliberately **no
  pull**: this button must never touch a working tree the operator may have
  dirty. Fetching is their call, in the terminal it just opened.

Terminal opening reuses the existing `openLocalSessionTab(dir)` — the same path
`OpenLocalSession` takes — so there is one implementation of "open a tab running
claude", not two.

**Auth is the HOST's git credentials** (the operator's `gh` / credential
helper). The profile's `GITHUB_TOKEN` is *not* embedded in the clone URL and not
passed as an `http.extraHeader`: either would persist the secret into the
clone's remote or reflog, on disk, indefinitely. A private-repo failure returns
git's stderr verbatim — on a credential problem git's own wording ("Repository
not found", "could not read Username") *is* the answer.

`normalizeCloneURL` accepts anything GitHub hands us — `clone_url`, a search
hit's `html_url`, a PR URL, an `api.github.com/repos/...` URL, an scp-style or
`ssh://` remote — and returns one https clone URL. The **host is preserved**
(only `api.github.com` is rewritten to `github.com`), so a GitHub Enterprise
remote still clones from its own host.

## 3. Container lane — `LaunchInteractiveSession(req) (SessionActionResponse, error)`

```go
type InteractiveSessionRequest struct { Profile, RepoURL, Task string }
```

Builds `brainbox.CreateSessionRequest{Name: "code-<repo>-<suffix>", ExecMode:
"interactive", WorkspaceProfile, WorkspaceHome, Task}` and returns
`a.CreateSession(...)` — reused, not reimplemented, so the profile env
forwarding and the `PROFILE_ENV_KEY` / image-delivery handling it already does
apply unchanged.

A brainbox session has **no repo field**: the repo reaches the container only
through the seeded `Task`. So the session lane's prompt always carries
`git clone <httpsURL>` plus the fact that `GITHUB_TOKEN` is already in the
session's env (it arrives with the forwarded profile env). The store enforces
that on submit even if the operator deletes the line — a session that can't find
the repo is a dead session.

The name suffix (`%06x` of the nanosecond clock) exists so a second launch on
the same repo doesn't collide with the first.

## 4. Testability

The pure logic is factored into helpers unit-tested directly —
`normalizeCloneURL` (ssh/html/api/enterprise → https, `.git` handling) and
`deriveCloneDest` (the `<home>/code/<repo>` path). The two side effects sit
behind package-var seams, `runGitClone` and `openTerminalAt`, so the
clone-vs-open-existing **branch** is asserted with a temp dir and a fake runner:
no network, no AppleScript. The existing-checkout test writes an uncommitted
file into the destination and asserts it survives. `LaunchInteractiveSession` is
tested through an `httptest` server that inspects the `/api/create` body
(`exec_mode`, `workspace_profile`, `workspace_home`, verbatim task, name shape),
matching the style already used in `app_code_test.go`.

## 5. Frontend

- **`DispatchModal.svelte`** grows a *launch as* segmented toggle (Autonomous
  task | Interactive session). Both lanes share the one editable templated
  textarea; the agent picker is hidden in session mode (you are the agent). The
  lane resets to `task` on every open — a launcher that silently remembers
  "container" from twenty minutes ago starts the wrong kind of work. Switching
  to the session lane prepends the clone instruction **idempotently**, so
  toggling back and forth never stacks duplicates on the operator's edits.
- **`CodePanel.svelte`**: repo rows are `[open ↗] · [Clone + terminal] ·
  [Begin work ▾]`; PR/issue rows are `[open ↗] · [Begin work ▾]` seeded with
  that row's context. `Clone + terminal` reports the checkout path on success
  and git's stderr on failure.
- **`code.svelte.ts`** owns the lane (`dispatchLane`), the in-flight flag for
  the host clone (`cloningRepo`), and the per-lane results
  (`lastTaskID` / `lastSessionURL` / `lastClonePath` / `cloneError`), all
  cleared by `reset()` on a profile switch — a path or session URL from the
  previous profile has no business surviving one.

## 6. Explicitly out of Slice A

Single-repo drill-down (Slice B) · SQLite caching of the overview (Slice C) ·
open-in-editor / reveal-in-Finder (terminal-only by choice) · any new credential
UI · `git pull`/`fetch` of an existing clone.
