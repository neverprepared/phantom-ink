# Jira integration — design

Status: approved in brainstorm 2026-09-22. Section 1 (enablement + resolver)
approved in chat; sections 2-5 written here with recommendations baked in,
pending review.

## Goal

Link code to Jira issues so work can be tracked and automated. Links are
**derived from text, never stored**. The Code panel is the primary surface.

Non-goals for v1: mirroring Jira data into a local store, a Jira write path
beyond issue transitions, Confluence, and a general-purpose platform cache.

## Decisions taken in brainstorm

| Decision | Choice | Rejected |
|---|---|---|
| Link storage | Derived by convention from text | Local join table; Jira remote links |
| Join direction | Code-first (decorate the Code panel) | Jira-first; both |
| Automation | Dispatch agent, drift report, transition on merge | Read-only tracking |
| Enablement | Global integration + per-profile opt-in | Presence-is-selection |

## Section 1 — Enablement and the resolver (APPROVED)

Three gates, all must pass before a profile sees anything Jira:

1. **Global.** `jira` is an Integrations catalog entry with `kind: "saas"`.
   ADR-003 currently assumes every integration is a relocatable compose stack;
   `saas` is a new kind that skips fleet placement entirely. "On" means
   credentials configured and validated. `GET /api/integrations/jira/status`
   calls `GET /rest/api/3/myself` in place of `docker compose ps`.
2. **Credentials.** One app-level set: `JIRA_URL`, `JIRA_USERNAME`,
   `JIRA_API_TOKEN`, stored where gateway secrets already live — not copied
   into each profile's env.
3. **Per profile.** Explicit opt-in, default off. One flag gates all three
   consumers: Code-panel chips, the Jira panel, and the `atlassian` MCP server
   in that profile's gateway allowlist. A single flag so they cannot drift.

This diverges from the Code panel's existing presence-is-selection idiom
(`GITHUB_TOKEN` present => GitHub on). Deliberate: presence-is-selection makes
"why is this showing up?" unanswerable. Documented so the divergence is not
read as an oversight.

### Resolver — `app/jira/`

Parsing is pure, no I/O, no network:

- `ParseKeys(text string) []string` — `\b[A-Z][A-Z0-9]+-\d+\b`, deduped,
  order preserved. Fed `provider.Item.Title` for PRs and `provider.Branch.Name`
  for branches.
- **Project-key allowlist is mandatory, not an optimization.** The bare regex
  matches `UTF-8`, `SHA-256`, `ADR-003`, `CVE-2024-1234`, `PR-1`. Keys are
  filtered against `GET /rest/api/3/project` or the panel fills with chips for
  issues that do not exist.
- `Resolve(keys []string) map[string]Issue` — ONE batched JQL per page,
  `key in (ABC-123, DEF-9)`, read through the cache.

### Cache

In-process TTL inside the Go app. Not a new service.

- Issue status: ~5 min (it moves).
- Project keys: ~24h (it does not).

This is a narrow answer to the open daily-cache TODO. It deliberately does NOT
settle the general gateway-level cache question — one panel's needs should not
design a platform primitive.

### Credential-path guard

`profileAzureConfigDir` (`app/app_code.go:152`) returns a raw env value with no
expansion, which is why a literal `app/$WORKSPACE_HOME/.azure` directory exists
in the working tree. Jira credential resolution runs through the same env path
and MUST NOT repeat it: expand the value and require an absolute path before
use. Fixing the Azure case is adjacent and optional.

## Section 2 — Code panel decoration (slice 1)

`CodeOverview` gains a resolved-issue map; rows gain their keys.

- `provider.Item` is NOT modified — it is the git-provider contract and Jira is
  not a git provider. Instead `CodeOverview` grows:
  - `IssueKeys map[string][]string` — row key -> issue keys found in its title.
    `provider.Item` has no id field, so the row key is the composite
    `<provider>:<repo_full_name>#<number>`, built by one helper used by both
    the producer and the frontend so the two cannot disagree.
  - `Issues map[string]jira.Issue` — issue key -> resolved issue
  - `JiraError string` — non-fatal, same idiom as `ReposError`
- A Jira outage must NOT blank the Code panel. On error: chips are omitted,
  `JiraError` is set, git rows render exactly as they do today.
- Branch-derived keys come from `RepoDetail`, not the overview — the overview
  has no branch names.
- Frontend: a chip per key on a PR row — key, status, assignee, click-through to
  the Jira issue. Rows with no key render unchanged.

Slice 1 stops here. It is read-only, needs no write scope, and proves the
resolver before anything automates on top of it.

## Section 3 — The Jira panel

Code-first makes a standalone panel a **detail view**, not a second data plane:
clicking a chip opens the issue — summary, status, assignee, description, and
the code rows that reference it (reversed from the current page's already
resolved map, not a fresh search).

A JQL-driven "my open issues" list is explicitly deferred. It needs a reverse
index of code -> keys that slice 1 does not build, and its value is unproven
until the chips are trusted.

## Section 4 — Automation

Ordered by cost. Each is a separate slice.

1. **Dispatch an agent at an issue.** From a chip or the issue view, call the
   existing `DispatchRepoTask` path with the issue key, summary, and description
   as the prompt. Read-only token. Rides hub/tasks unchanged — almost no new
   mechanism.
2. **Drift report.** Pure derivation over data already on screen: issue Done
   with PR open; PR merged with issue not Done; branch alive with no issue;
   issue In Progress with no code. Read-only. The report the linking makes
   possible, and the best evidence the resolver is correct.
3. **Transition on merge.** The expensive one. Needs Jira WRITE scope and a
   merge trigger. The Code panel polls; it does not receive git webhooks, so
   either poll-detected merges emit a bus event, or a git-provider webhook does.
   That decision is deferred to its own brainstorm — it reopens the inbound
   event direction set aside earlier, and it is the only piece requiring a
   token that can mutate the team's tracker.

## Section 5 — Testing

- **Resolver parsing**: table-driven unit tests, zero network, including every
  false positive named above (`UTF-8`, `SHA-256`, `ADR-003`, `CVE-2024-1234`).
  TDD starts here.
- **Allowlist filtering**: keys from unknown projects are dropped.
- **Cache**: TTL expiry and single-flight on concurrent identical lookups.
- **Enablement**: opted-out profile yields no chips, no panel, no MCP server —
  one test per consumer, proving the single flag.
- **Degradation**: Jira 500 / 401 / timeout leaves git rows intact and sets
  `JiraError`.
- Existing `app_code_test.go` and `app_code_repodetail_test.go` must stay green.

## Open questions

1. Transition-on-merge trigger: poll-detected merge -> bus event, vs git
   provider webhook. Deferred to its own brainstorm (Section 4).
2. Whether `saas` belongs in the ADR-003 catalog or warrants an ADR amendment.
   The alternative — Jira's on/off switch in Settings instead of Integrations —
   is less consistent but needs no ADR change.
