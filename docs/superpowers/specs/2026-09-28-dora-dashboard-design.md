# DORA Delivery Metrics + Runner Metrics

**Date:** 2026-09-28
**Status:** Approved design
**Branch:** `feat/dora-dashboard`

## Goal

A DORA metrics dashboard for the four standard delivery metrics, plus fleet
runner health and throughput, as a new tab in the app's Work hub. Profile-scoped
like every other feature.

## Decisions (and why)

| Decision | Choice | Rationale |
|---|---|---|
| Deployment signal | **Merge to default branch** | PR data already flows; no CI/workflow-run plumbing for two providers. Accurate under trunk-based CD. |
| Failure signal | **Revert commits on default branch** | `ListRecentCommits` already exists; zero new integrations. |
| Runner data | **Live snapshot + task-derived history** | `ListRunners` gives current state; `ListTasks` gives real history. No sampler, no new runner tables. |
| Persistence | **SQLite event tables + incremental sync** | DORA is a time series. Makes the 30/90d toggle free and accumulates history. |
| Repo scope | **All repos, aggregate + per-repo breakdown** | No filter in v1 (YAGNI). |

## Non-goals (v1)

- No workflow-run / CI-status ingestion for either provider.
- No repo selection/filter UI.
- No sampled runner utilization time series (no sampler goroutine).
- No cross-profile or fleet-wide aggregate view.

## Known limitations — MUST be labeled in the UI, not hidden

1. **Lead time is a proxy.** Measured PR-created → merged, not first-commit →
   deploy. Reads optimistic versus true DORA lead time.
2. **Change failure rate undercounts.** Most breakages are forward-fixed, not
   reverted. Reverts are a floor, not a true rate.
3. **Merge ≠ deploy.** Under batched or manual deploys, deployment frequency
   overcounts.

Each metric card carries a short caveat note. Do not present these as exact.

## Data model

New migration **version 28** in `app/db_migrations.go` (latest existing is 27).
Follow the store pattern in `app/db_jira_links.go`: every accessor scoped by
profile, `ON CONFLICT ... DO NOTHING` for idempotency, timestamps as RFC3339 UTC.

```sql
CREATE TABLE IF NOT EXISTS dora_deploy_events (
  profile        TEXT NOT NULL,
  provider       TEXT NOT NULL,          -- 'github' | 'ado'
  repo_full_name TEXT NOT NULL,
  pr_number      INTEGER NOT NULL,
  title          TEXT NOT NULL DEFAULT '',
  author         TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL,          -- PR opened (lead-time start)
  merged_at      TEXT NOT NULL,          -- deploy event
  merge_sha      TEXT NOT NULL DEFAULT '',
  html_url       TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (profile, provider, repo_full_name, pr_number)
);
CREATE INDEX IF NOT EXISTS idx_dora_deploy_merged
  ON dora_deploy_events (profile, merged_at);

CREATE TABLE IF NOT EXISTS dora_failure_events (
  profile        TEXT NOT NULL,
  provider       TEXT NOT NULL,
  repo_full_name TEXT NOT NULL,
  revert_sha     TEXT NOT NULL,
  reverted_subject TEXT NOT NULL DEFAULT '',
  matched_pr_number INTEGER NOT NULL DEFAULT 0,  -- 0 = unmatched
  reverted_at    TEXT NOT NULL,          -- revert commit date
  PRIMARY KEY (profile, provider, repo_full_name, revert_sha)
);
CREATE INDEX IF NOT EXISTS idx_dora_failure_at
  ON dora_failure_events (profile, reverted_at);

CREATE TABLE IF NOT EXISTS dora_sync_state (
  profile        TEXT NOT NULL,
  provider       TEXT NOT NULL,
  repo_full_name TEXT NOT NULL,
  last_synced_at TEXT NOT NULL,          -- watermark for incremental fetch
  PRIMARY KEY (profile, provider, repo_full_name)
);
```

New store file `app/db_dora.go` with the accessors (upsert deploy events, upsert
failure events, read events in a window, get/set watermark).

## Provider interface change

Add to `provider.Client` in `app/provider/provider.go`:

```go
// ListMergedPRs returns PRs merged into the repo's DEFAULT branch with a merge
// timestamp at or after since. Used for DORA deploy events.
ListMergedPRs(ctx context.Context, ref RepoRef, since time.Time) ([]MergedPR, error)
```

New type — deliberately NOT extending `provider.Item`, which feeds every open-PR
view and must not grow merge fields:

```go
type MergedPR struct {
    Provider     Kind   `json:"provider"`
    RepoFullName string `json:"repo_full_name"`
    Number       int    `json:"number"`
    Title        string `json:"title"`
    Author       string `json:"author"`
    CreatedAt    string `json:"created_at"`
    MergedAt     string `json:"merged_at"`
    MergeSHA     string `json:"merge_sha"`
    BaseRef      string `json:"base_ref"`
    HTMLURL      string `json:"html_url"`
}
```

`Repo.DefaultBranch` already exists on `provider.Repo`, so the default branch
needs no extra fetch — read it from the repo list the sync already has.

Implementations:
- **GitHub** (`app/provider/github/github.go`): search `is:pr is:merged
  base:<default> repo:<owner>/<name> merged:>=<since>`, or the pulls API with
  `state=closed` filtered on non-null `merged_at`. Existing queries are all
  `is:open` — this is the first merged-PR path in the codebase.
- **ADO** (`app/provider/ado/`): completed pull requests filtered on
  `targetRefName` matching the default branch.

## Computation package

New top-level `app/dora/` (matching `provider/` and `brainbox/`). Pure — no I/O,
no DB, no network — so it is trivially unit-testable:

```go
// Window is a half-open interval [From, To); the UI toggle passes 30 or 90 days
// back from now. Events exactly at From are included, at To excluded.
type Window struct{ From, To time.Time }

func Compute(deploys []DeployEvent, failures []FailureEvent, window Window) Metrics
```

`Metrics` carries, for the window and per repo:
- **Deployment frequency** — merges per day.
- **Lead time for changes** — p50 and p85 of merged_at − created_at.
- **Change failure rate** — failures ÷ deploys.
- **Time to restore** — p50 of revert_at − matched merged_at (matched events only).
- **Band** — Elite / High / Medium / Low. Thresholds are DORA 2023 banding and
  MUST live as named constants in one place in `app/dora/` so they are reviewable
  and adjustable:

| Metric | Elite | High | Medium | Low |
|---|---|---|---|---|
| Deployment frequency | multiple / day | daily → weekly | weekly → monthly | < monthly |
| Lead time (p50) | < 1 hour | < 1 day | 1 day → 1 week | > 1 week |
| Change failure rate | <= 15% | <= 30% | <= 45% | > 45% |
| Time to restore (p50) | < 1 hour | < 1 day | < 1 week | > 1 week |

Revert matching lives here too: parse `Revert "<subject>"` from a commit message,
match `<subject>` back to a deploy event's title within the window. Unmatched
reverts still count toward failure rate but are excluded from time-to-restore.

## App surface — `app/app_dora.go`

```go
func (a *App) DORAOverview(profile string, windowDays int) (DORAOverview, error)
func (a *App) SyncDORA(profile string) (DORASyncResult, error)
func (a *App) RunnerMetrics(profile string) (RunnerMetrics, error)
```

- `DORAOverview` reads local SQLite only and computes — fast, offline-capable.
  Echoes `Profile` back so a response landing after a profile switch can be
  discarded (same guard as `CodeOverview`).
- `SyncDORA` fans out per repo from the watermark and makes **two** calls per
  repo: `ListMergedPRs` (deploy events) and `ListRecentCommits` (revert
  detection). It upserts both, then advances the watermark. Per-repo errors are
  **non-fatal and reported per section**, matching `CodeOverview`'s `ReposError` /
  `PullRequestsError` convention. One repo's 403 must not blank the dashboard.
- **Commit-window truncation.** `ListRecentCommits(ctx, ref, limit)` is
  limit-based, not `since`-based. Fetch with `limit = 250` and stop consuming once
  commits predate the watermark. If the OLDEST returned commit is still newer than
  the watermark, the window was not fully covered: set a per-repo `Truncated` flag
  on the sync result and surface it in the UI ("revert history may be incomplete
  for <repo>"). Do NOT silently drop the gap. Advance the watermark only to the
  oldest commit actually seen in that case.
- `RunnerMetrics` joins `ListRunners` (live: up/down via `LastSeen`, queue depth,
  in-flight vs. `MaxConcurrent`, tags, version, host) with `ListTasks`
  derivation per `RunnerName` (throughput, mean duration from
  `CreatedAt`→`UpdatedAt` on terminal status, failure rate, backend split).
  **Tasks stranded in `running` by a dead runner are excluded from duration stats
  and surfaced as a separate `Stranded` count** — this is a known platform gap
  (no liveness reconcile), so it must not silently skew the mean. Stranded is
  defined concretely as: `Status == "running"` AND (`UpdatedAt` older than a
  `strandedAfter` constant, default **6h**, OR its `RunnerName` is not present in
  the current `ListRunners` result). Put `strandedAfter` next to the band
  constants so both are adjustable in one place.

## Frontend

- `WorkTab` in `app/frontend/src/lib/stores.svelte` gains `'dora'`.
- New `app/frontend/src/lib/components/DoraTab.svelte`, following `JiraTab.svelte`.
- Tab appears for every profile with a git provider configured. When
  `TokenMissing`, show the existing "connect a provider" banner rather than an error.
- Runner section renders **only when runners are registered** — absent, not
  present-and-empty.
- Four metric cards + band badges, a per-repo breakdown table, a 30/90d toggle,
  and a manual Sync button reporting last-sync time.
- Regenerate `app/frontend/wailsjs` bindings for the new methods.

## Error handling

- No provider configured → `TokenMissing`, banner, no error toast.
- Provider 401 → `TokenInvalid`, same as `CodeOverview`.
- Per-repo sync failure → recorded, watermark NOT advanced for that repo, other
  repos proceed.
- Empty window (no merges) → zero state with an explanatory line, not an error.
- Router/hub unreachable → runner section hidden with a note; DORA metrics still
  render from local DB.

## Testing

- `app/dora/*_test.go` — pure unit tests: windowing, p50/p85 percentiles, band
  classification, revert-subject matching (matched + unmatched), division-by-zero
  when deploys are zero.
- `app/app_dora_test.go` — app methods against a fake `provider.Client`, following
  `app_code_test.go`. Covers incremental watermark advance, per-repo error
  isolation, profile echo, and stranded-task exclusion.
- `app/provider/github`, `app/provider/ado` — `ListMergedPRs` against recorded
  fixtures, per existing provider tests.
- Frontend: `cd app/frontend && npm run check` must pass.

## Verification

```
cd app && go test ./... -race
cd app/frontend && npm run check
```

Both must pass before the PR. Note CI is path-filtered — an `app/` change does
trigger `test-app`.
