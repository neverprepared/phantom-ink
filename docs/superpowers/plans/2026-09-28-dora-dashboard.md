# DORA Delivery + Runner Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a DORA delivery-metrics dashboard (deployment frequency, lead time, change failure rate, time to restore) plus fleet runner health/throughput as a new tab in the phantom-ink Work hub.

**Architecture:** Merges to a repo's default branch are the deploy signal; `Revert "..."` commits are the failure signal. Both are fetched incrementally per repo and persisted as events in the app's SQLite DB, so metric computation is a pure local function over a time window. Runner metrics are derived live from `ListRunners` (current state) joined with `ListTasks` (history) — no sampler, no new runner tables.

**Tech Stack:** Go 1.25 (Wails v2 app, module `phantom-ink`), SQLite via the existing `app/db*.go` layer, Svelte 5 frontend with runes.

**Spec:** `docs/superpowers/specs/2026-09-28-dora-dashboard-design.md` — read it before Task 1. The plan argues from the spec; both travel together.

## Global Constraints

- Everything is **profile-scoped**. No accessor, query, or response may cross profiles — cross-profile leakage is a bug, not a cosmetic issue.
- Follow `app/db_jira_links.go` for store style: profile as the first parameter, `ON CONFLICT ... DO NOTHING` for idempotency, timestamps stored as RFC3339 UTC strings.
- Provider errors are **non-fatal and reported per section**, matching `CodeOverview`'s `ReposError` / `PullRequestsError` fields. One repo's 403 must never blank the dashboard.
- Responses echo back `Profile` so a reply landing after a profile switch can be discarded.
- Band thresholds and `strandedAfter` live as named constants in ONE place in `app/dora/`.
- The three UI caveats from the spec (lead time is a proxy, failure rate undercounts, merge ≠ deploy) must be visible in the UI, not hidden.
- Verify with `cd app && go test ./... -race` and `cd app/frontend && npm run check`.
- Never hand-edit `app/internal/contract/*`.

---

### Task 1: Migration 28 + DORA event store

**Files:**
- Modify: `app/db_migrations.go` (append to the `migrations` slice; latest existing version is 27)
- Create: `app/db_dora.go`
- Test: `app/db_dora_test.go`

**Interfaces:**
- Consumes: the existing `*DB` type and `errNoDB` sentinel from `app/db.go`.
- Produces:
  - `func (db *DB) UpsertDeployEvents(profile string, evs []DeployEventRow) error`
  - `func (db *DB) UpsertFailureEvents(profile string, evs []FailureEventRow) error`
  - `func (db *DB) DeployEventsSince(profile string, since time.Time) ([]DeployEventRow, error)`
  - `func (db *DB) FailureEventsSince(profile string, since time.Time) ([]FailureEventRow, error)`
  - `func (db *DB) DORAWatermark(profile, prov, repo string) (time.Time, error)`
  - `func (db *DB) SetDORAWatermark(profile, prov, repo string, at time.Time) error`
  - Row types `DeployEventRow`, `FailureEventRow` (fields mirror the spec's columns).

- [ ] **Step 1: Write the failing test**

In `app/db_dora_test.go` (follow the DB setup helper already used by `app/db_jira_links_test.go`):

```go
func TestDeployEventsAreProfileScopedAndIdempotent(t *testing.T) {
	db := newTestDB(t)
	ev := DeployEventRow{
		Provider: "github", RepoFullName: "o/r", PRNumber: 1,
		Title: "Add thing", Author: "me",
		CreatedAt: "2026-09-01T10:00:00Z", MergedAt: "2026-09-01T12:00:00Z",
	}
	if err := db.UpsertDeployEvents("personal", []DeployEventRow{ev, ev}); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	if err := db.UpsertDeployEvents("work", []DeployEventRow{ev}); err != nil {
		t.Fatalf("upsert other profile: %v", err)
	}
	got, err := db.DeployEventsSince("personal", time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("want 1 event for personal (idempotent, not leaked), got %d", len(got))
	}
	if got[0].PRNumber != 1 {
		t.Fatalf("wrong row: %+v", got[0])
	}
}

func TestWatermarkRoundTrips(t *testing.T) {
	db := newTestDB(t)
	if _, err := db.DORAWatermark("personal", "github", "o/r"); err != nil {
		t.Fatalf("missing watermark must be zero-time, not an error: %v", err)
	}
	want := time.Date(2026, 9, 20, 8, 0, 0, 0, time.UTC)
	if err := db.SetDORAWatermark("personal", "github", "o/r", want); err != nil {
		t.Fatalf("set: %v", err)
	}
	got, err := db.DORAWatermark("personal", "github", "o/r")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if !got.Equal(want) {
		t.Fatalf("want %v got %v", want, got)
	}
}
```

If `newTestDB` does not exist under that name, reuse whatever helper `app/db_jira_links_test.go` uses — do not invent a second DB-setup path.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./ -run 'TestDeployEventsAreProfileScoped|TestWatermarkRoundTrips' -v`
Expected: FAIL — `DeployEventRow` and the accessors are undefined.

- [ ] **Step 3: Add migration 28**

Append to the `migrations` slice in `app/db_migrations.go` the three `CREATE TABLE IF NOT EXISTS` statements and two indexes exactly as written in the spec's "Data model" section, as `{version: 28, sql: ...}`.

- [ ] **Step 4: Write the store**

Create `app/db_dora.go` with the row types and the six accessors. Every query filters on `profile` in its `WHERE`. Upserts use `ON CONFLICT(profile, provider, repo_full_name, pr_number) DO NOTHING` (and the revert_sha equivalent for failures). `DORAWatermark` returns the zero `time.Time` and a nil error when no row exists — a missing watermark means "never synced", not a failure.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd app && go test ./ -run 'TestDeployEventsAreProfileScoped|TestWatermarkRoundTrips' -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/db_migrations.go app/db_dora.go app/db_dora_test.go
git commit -m "Add DORA event tables and profile-scoped store"
```

---

### Task 2: Pure metrics computation

**Files:**
- Create: `app/dora/dora.go`
- Test: `app/dora/dora_test.go`

**Interfaces:**
- Consumes: nothing — this package must import no DB, network, or Wails code.
- Produces:
  - `type Window struct{ From, To time.Time }`
  - `type DeployEvent struct{ RepoFullName string; PRNumber int; Title string; CreatedAt, MergedAt time.Time }`
  - `type FailureEvent struct{ RepoFullName string; RevertSHA, RevertedSubject string; MatchedPRNumber int; RevertedAt time.Time }`
  - `type Band string` with `BandElite`, `BandHigh`, `BandMedium`, `BandLow`
  - `type Metrics struct { DeploysPerDay float64; LeadTimeP50, LeadTimeP85 time.Duration; ChangeFailureRate float64; RestoreP50 time.Duration; DeployCount, FailureCount int; Bands map[string]Band; PerRepo map[string]Metrics }`
  - `Bands` is keyed by exactly these four strings, declared as exported consts in `dora.go` so the frontend and tests cannot drift: `"deployment_frequency"`, `"lead_time"`, `"change_failure_rate"`, `"time_to_restore"`.
  - `PerRepo` is keyed by `RepoFullName`; its nested `Metrics` values have `PerRepo` nil to avoid infinite recursion.
  - `func Compute(deploys []DeployEvent, failures []FailureEvent, w Window) Metrics`

- [ ] **Step 1: Write the failing test**

```go
package dora

import (
	"testing"
	"time"
)

func day(d int) time.Time { return time.Date(2026, 9, d, 12, 0, 0, 0, time.UTC) }

func TestComputeCoreMetrics(t *testing.T) {
	w := Window{From: day(1), To: day(11)} // 10 days
	deploys := []DeployEvent{
		{RepoFullName: "o/r", PRNumber: 1, CreatedAt: day(2).Add(-2 * time.Hour), MergedAt: day(2)},
		{RepoFullName: "o/r", PRNumber: 2, CreatedAt: day(3).Add(-4 * time.Hour), MergedAt: day(3)},
		{RepoFullName: "o/r", PRNumber: 3, CreatedAt: day(4).Add(-6 * time.Hour), MergedAt: day(4)},
		{RepoFullName: "o/r", PRNumber: 4, CreatedAt: day(20), MergedAt: day(20)}, // outside window
	}
	failures := []FailureEvent{
		{RepoFullName: "o/r", RevertSHA: "abc", MatchedPRNumber: 2, RevertedAt: day(3).Add(3 * time.Hour)},
	}
	m := Compute(deploys, failures, w)

	if m.DeployCount != 3 {
		t.Fatalf("window must exclude day 20: got %d", m.DeployCount)
	}
	if got, want := m.DeploysPerDay, 0.3; got < want-0.01 || got > want+0.01 {
		t.Fatalf("deploys/day: got %v want %v", got, want)
	}
	if m.LeadTimeP50 != 4*time.Hour {
		t.Fatalf("lead p50: got %v want 4h", m.LeadTimeP50)
	}
	if got, want := m.ChangeFailureRate, 1.0/3.0; got < want-0.01 || got > want+0.01 {
		t.Fatalf("cfr: got %v want %v", got, want)
	}
	if m.RestoreP50 != 3*time.Hour {
		t.Fatalf("restore p50: got %v want 3h", m.RestoreP50)
	}
}

func TestComputeZeroDeploysDoesNotDivideByZero(t *testing.T) {
	m := Compute(nil, []FailureEvent{{RevertedAt: day(2)}}, Window{From: day(1), To: day(11)})
	if m.ChangeFailureRate != 0 {
		t.Fatalf("no deploys must yield 0 rate, not NaN/Inf: got %v", m.ChangeFailureRate)
	}
}

func TestUnmatchedRevertCountsAsFailureButNotRestore(t *testing.T) {
	w := Window{From: day(1), To: day(11)}
	deploys := []DeployEvent{{RepoFullName: "o/r", PRNumber: 1, CreatedAt: day(2), MergedAt: day(2)}}
	failures := []FailureEvent{{RepoFullName: "o/r", RevertSHA: "z", MatchedPRNumber: 0, RevertedAt: day(3)}}
	m := Compute(deploys, failures, w)
	if m.FailureCount != 1 {
		t.Fatalf("unmatched revert still counts as a failure: got %d", m.FailureCount)
	}
	if m.RestoreP50 != 0 {
		t.Fatalf("unmatched revert must be excluded from restore time: got %v", m.RestoreP50)
	}
}

func TestBandClassification(t *testing.T) {
	if got := bandForLeadTime(30 * time.Minute); got != BandElite {
		t.Fatalf("<1h lead time is Elite, got %s", got)
	}
	if got := bandForChangeFailureRate(0.50); got != BandLow {
		t.Fatalf(">45%% failure rate is Low, got %s", got)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./dora/ -v`
Expected: FAIL — package `dora` does not exist.

- [ ] **Step 3: Implement the package**

Create `app/dora/dora.go` with the types above and:
- Window filtering: half-open `[From, To)` on `MergedAt` for deploys and `RevertedAt` for failures.
- `DeploysPerDay` = deploy count ÷ window days (guard a zero-length window).
- Percentile helper: sort ascending, nearest-rank index `int(math.Ceil(p*float64(len(xs)))) - 1`, clamped to `[0, len-1]`; empty input returns `0`.
- `ChangeFailureRate` = failures ÷ deploys, returning `0` when deploys is zero.
- `RestoreP50` over matched failures only, where duration = `RevertedAt − MergedAt` of the deploy with that `PRNumber` in the same repo; skip a failure whose match is not in the window.
- Band constants and the four `bandFor*` helpers using the spec's threshold table, all in this file.
- `const StrandedAfter = 6 * time.Hour` — **exported**, because Task 7 consumes it from package `main`. It lives here beside the band constants so both tuning knobs sit in one reviewable place, per the spec.
- `PerRepo` computed by grouping on `RepoFullName` and recursing with the same window.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./dora/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/dora/dora.go app/dora/dora_test.go
git commit -m "Add pure DORA metrics computation with band classification"
```

---

### Task 3: Revert-commit parsing and matching

**Files:**
- Create: `app/dora/revert.go`
- Test: `app/dora/revert_test.go`

**Interfaces:**
- Consumes: `DeployEvent` from Task 2.
- Produces:
  - `func ParseRevertSubject(commitMessage string) (subject string, ok bool)`
  - `func MatchRevert(subject string, deploys []DeployEvent) (prNumber int, ok bool)`

- [ ] **Step 1: Write the failing test**

```go
package dora

import "testing"

func TestParseRevertSubject(t *testing.T) {
	cases := []struct {
		msg     string
		want    string
		wantOK  bool
	}{
		{"Revert \"Add thing\"", "Add thing", true},
		{"Revert \"Add thing\"\n\nThis reverts commit abc123.", "Add thing", true},
		{"Revert \"Add thing (#12)\"", "Add thing (#12)", true},
		{"Add thing", "", false},
		{"Reverting the thing", "", false},
	}
	for _, c := range cases {
		got, ok := ParseRevertSubject(c.msg)
		if ok != c.wantOK || got != c.want {
			t.Errorf("ParseRevertSubject(%q) = (%q,%v) want (%q,%v)", c.msg, got, ok, c.want, c.wantOK)
		}
	}
}

func TestMatchRevertPrefersPRNumberInSubject(t *testing.T) {
	deploys := []DeployEvent{
		{PRNumber: 12, Title: "Add thing"},
		{PRNumber: 99, Title: "Add thing"},
	}
	if got, ok := MatchRevert("Add thing (#99)", deploys); !ok || got != 99 {
		t.Fatalf("an explicit (#99) must win over title ambiguity: got %d ok=%v", got, ok)
	}
}

func TestMatchRevertFallsBackToTitle(t *testing.T) {
	deploys := []DeployEvent{{PRNumber: 7, Title: "Fix the bug"}}
	if got, ok := MatchRevert("Fix the bug", deploys); !ok || got != 7 {
		t.Fatalf("title match: got %d ok=%v", got, ok)
	}
	if _, ok := MatchRevert("Unrelated", deploys); ok {
		t.Fatal("no match must report ok=false, not a bogus PR number")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./dora/ -run TestParseRevert -v`
Expected: FAIL — `ParseRevertSubject` undefined.

- [ ] **Step 3: Implement**

`ParseRevertSubject`: take the first line; require the prefix `Revert "` and a trailing `"`; return the inner text. Anything else is `ok=false`.

`MatchRevert`: if the subject contains a trailing `(#N)`, match on `PRNumber == N` first. Otherwise compare the subject against each deploy's `Title` after trimming a trailing `(#N)` from the title too. Exact match after trimming; no fuzzy matching.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./dora/ -v`
Expected: PASS (Task 2 tests still green)

- [ ] **Step 5: Commit**

```bash
git add app/dora/revert.go app/dora/revert_test.go
git commit -m "Parse and match revert commits to their original merges"
```

---

### Task 4: `ListMergedPRs` on the provider interface + GitHub implementation

**Files:**
- Modify: `app/provider/provider.go` (add `MergedPR` type and the interface method)
- Modify: `app/provider/github/github.go` (real implementation)
- Modify: `app/provider/ado/ado.go` (compile-stub only — real work is Task 5)
- Modify: `app/app_code_test.go` (`fakeProvider`)
- Modify: `app/app_code_repodetail_test.go` (`fakeDetailProvider`)
- Test: `app/provider/github/github_mergedprs_test.go`

**CRITICAL — this task must land as one commit.** Adding a method to `provider.Client` breaks **three** implementations at once: the real ADO client and BOTH test fakes (`fakeProvider`, `fakeDetailProvider`). If you add the interface method without updating all three, `go build ./...` fails and every other package's tests fail with it. Update all three in this task.

**Interfaces:**
- Consumes: `RepoRef`, `Kind` from `app/provider/provider.go`.
- Produces: `MergedPR` (exact fields in the spec) and
  `ListMergedPRs(ctx context.Context, ref RepoRef, since time.Time) ([]MergedPR, error)`.

- [ ] **Step 1: Write the failing test**

In `app/provider/github/github_mergedprs_test.go`, follow the existing fixture/httptest style of `app/provider/github/github_repodetail_test.go`:

```go
func TestListMergedPRsMapsFieldsAndFiltersUnmerged(t *testing.T) {
	// Serve a canned GitHub pulls response with one merged and one closed-unmerged PR.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`[
		  {"number":12,"title":"Add thing","user":{"login":"me"},
		   "created_at":"2026-09-01T10:00:00Z","merged_at":"2026-09-01T12:00:00Z",
		   "merge_commit_sha":"deadbeef","base":{"ref":"main"},
		   "html_url":"https://example.test/pr/12"},
		  {"number":13,"title":"Other branch","user":{"login":"me"},
		   "created_at":"2026-09-02T10:00:00Z","merged_at":"2026-09-02T12:00:00Z",
		   "merge_commit_sha":"cafe","base":{"ref":"release"},
		   "html_url":"https://example.test/pr/13"},
		  {"number":14,"title":"Abandoned","user":{"login":"me"},
		   "created_at":"2026-09-03T10:00:00Z","merged_at":null,
		   "base":{"ref":"main"},"html_url":"https://example.test/pr/14"}
		]`))
	}))
	defer srv.Close()

	c := newTestClient(t, srv.URL) // reuse the helper the existing github tests use
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "o", Name: "r"},
		time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("both merged PRs are returned; base-branch filtering is the CALLER's job: got %d", len(got))
	}
	if got[0].Number != 12 || got[0].MergedAt != "2026-09-01T12:00:00Z" ||
		got[0].MergeSHA != "deadbeef" || got[0].BaseRef != "main" || got[0].Author != "me" {
		t.Fatalf("field mapping wrong: %+v", got[0])
	}
}
```

**Note on the default branch:** `DefaultBranch` lives on `provider.Repo`, NOT on
`provider.RepoRef`. Do NOT add it to `RepoRef`. `ListMergedPRs` therefore does
**not** filter by base branch — it returns every merged PR with `BaseRef`
populated, and `SyncDORA` (Task 6) does the filtering using the `Repo.DefaultBranch`
it already has from `ListRepos`. This keeps the provider dumb and the policy in one
place. Adjust the second fixture PR in the test to be merged into `release` so the
test proves `BaseRef` is mapped rather than filtered.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./provider/github/ -run TestListMergedPRs -v`
Expected: FAIL — `ListMergedPRs` undefined.

- [ ] **Step 3: Add the type and interface method**

In `app/provider/provider.go`, add the `MergedPR` struct exactly as specified in the spec, and add `ListMergedPRs` to the `Client` interface with the doc comment from the spec.

- [ ] **Step 4: Implement for GitHub, stub ADO, fix both fakes**

- GitHub: list closed PRs for the repo, drop any with a null `merged_at`, keep those whose `merged_at` is at or after `since`, and map into `MergedPR` with `BaseRef` from `base.ref`. Do NOT filter on base branch here. Paginate until results predate `since`.
- ADO: add a stub returning `(nil, nil)` with a comment `// TODO(Task 5): real implementation` — this is the ONE permitted placeholder in this plan, and only because it exists to keep the tree compiling for exactly one task.
- `fakeProvider` in `app/app_code_test.go`: add
  ```go
  func (f *fakeProvider) ListMergedPRs(context.Context, provider.RepoRef, time.Time) ([]provider.MergedPR, error) {
  	return f.mergedPRs, f.mergedPRsErr
  }
  ```
  and add the two backing fields to the struct so Task 6 can drive it.
- `fakeDetailProvider` in `app/app_code_repodetail_test.go`: add the same method returning `(nil, nil)`.

- [ ] **Step 5: Run the full suite to verify nothing else broke**

Run: `cd app && go build ./... && go test ./... -race`
Expected: PASS — this is the step that proves all three implementations were updated.

- [ ] **Step 6: Commit**

```bash
git add app/provider/provider.go app/provider/github/ app/provider/ado/ app/app_code_test.go app/app_code_repodetail_test.go
git commit -m "Add ListMergedPRs to the provider interface with GitHub support"
```

---

### Task 5: ADO implementation of `ListMergedPRs`

**Files:**
- Modify: `app/provider/ado/ado.go` (replace the Task 4 stub)
- Test: `app/provider/ado/ado_mergedprs_test.go`

**Interfaces:**
- Consumes: `MergedPR`, `ListMergedPRs` from Task 4.
- Produces: no new names — fills in the stub.

- [ ] **Step 1: Write the failing test**

Mirror Task 4's test against an ADO-shaped payload, following the existing style in `app/provider/ado/ado_test.go`:

```go
func TestADOListMergedPRsFiltersByTargetBranch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"value":[
		  {"pullRequestId":12,"title":"Add thing","status":"completed",
		   "createdBy":{"uniqueName":"me@example.test"},
		   "creationDate":"2026-09-01T10:00:00Z","closedDate":"2026-09-01T12:00:00Z",
		   "lastMergeCommit":{"commitId":"deadbeef"},
		   "targetRefName":"refs/heads/main"},
		  {"pullRequestId":13,"title":"Other branch","status":"completed",
		   "createdBy":{"uniqueName":"me@example.test"},
		   "creationDate":"2026-09-02T10:00:00Z","closedDate":"2026-09-02T12:00:00Z",
		   "lastMergeCommit":{"commitId":"cafe"},
		   "targetRefName":"refs/heads/release"}
		]}`))
	}))
	defer srv.Close()

	c := newTestADOClient(t, srv.URL) // reuse the existing ado test helper
	got, err := c.ListMergedPRs(context.Background(),
		provider.RepoRef{Owner: "org", Name: "repo"},
		time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("ListMergedPRs: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("both completed PRs returned; branch filtering is the caller's job: %+v", got)
	}
	if got[0].MergedAt != "2026-09-01T12:00:00Z" || got[0].MergeSHA != "deadbeef" {
		t.Fatalf("field mapping wrong: %+v", got[0])
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./provider/ado/ -run TestADOListMergedPRs -v`
Expected: FAIL — the stub returns nil, so `len(got)` is 0.

- [ ] **Step 3: Implement**

Query completed pull requests. Map `targetRefName` into `BaseRef` by stripping the
`refs/heads/` prefix — do NOT filter on it (Task 6 filters). Use `closedDate` as
`MergedAt` and `creationDate` as `CreatedAt`, take `lastMergeCommit.commitId` as
`MergeSHA` and `createdBy.uniqueName` as `Author`. Drop anything with `closedDate`
before `since`. Remove the Task 4 `TODO` comment.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./provider/... -race`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/provider/ado/
git commit -m "Implement ListMergedPRs for Azure DevOps"
```

---

### Task 6: `DORAOverview` and `SyncDORA`

**Files:**
- Create: `app/app_dora.go`
- Test: `app/app_dora_test.go`

**Interfaces:**
- Consumes: the Task 1 store, the Task 2/3 `dora` package, Task 4's `ListMergedPRs`, and the existing per-profile provider resolution used by `CodeOverview` in `app/app_code.go`.
- Produces:
  - `func (a *App) DORAOverview(profile string, windowDays int) (DORAOverview, error)`
  - `func (a *App) SyncDORA(profile string) (DORASyncResult, error)`
  - `type DORAOverview struct { Profile string; WindowDays int; TokenMissing, TokenInvalid bool; Metrics dora.Metrics; LastSyncedAt string; Caveats []string; SyncError string }`
  - `type DORASyncResult struct { Profile string; ReposSynced int; DeployEventsAdded, FailureEventsAdded int; Truncated []string; RepoErrors map[string]string }`

- [ ] **Step 1: Write the failing test**

```go
func TestSyncDORAIsolatesPerRepoErrors(t *testing.T) {
	// Repo "o/good" succeeds, repo "o/bad" fails. The good repo's events must
	// still be stored, and the failure reported per repo rather than returned
	// fatally. Extend fakeProvider (Task 4) so mergedPRs/mergedPRsErr can be
	// keyed per repo: mergedPRsByRepo map[string][]provider.MergedPR and
	// mergedPRsErrByRepo map[string]error.
	app, db := newTestApp(t) // reuse the helper app_code_test.go uses
	fp := &fakeProvider{
		kind: provider.KindGitHub,
		repos: []provider.Repo{
			{Provider: provider.KindGitHub, Owner: "o", Name: "good", FullName: "o/good", DefaultBranch: "main"},
			{Provider: provider.KindGitHub, Owner: "o", Name: "bad", FullName: "o/bad", DefaultBranch: "main"},
		},
		mergedPRsByRepo: map[string][]provider.MergedPR{
			"o/good": {{
				Provider: provider.KindGitHub, RepoFullName: "o/good", Number: 1,
				Title: "Add thing", Author: "me", BaseRef: "main",
				CreatedAt: "2026-09-01T10:00:00Z", MergedAt: "2026-09-01T12:00:00Z",
			}},
		},
		mergedPRsErrByRepo: map[string]error{"o/bad": errors.New("403 forbidden")},
	}
	app.setTestProvider(t, "personal", fp) // follow however app_code_test.go injects providers

	res, err := app.SyncDORA("personal")
	if err != nil {
		t.Fatalf("one repo failing must not fail the whole sync: %v", err)
	}
	if got := res.RepoErrors["o/bad"]; got == "" {
		t.Fatal("the failing repo must be reported in RepoErrors")
	}
	if _, ok := res.RepoErrors["o/good"]; ok {
		t.Fatal("the succeeding repo must not appear in RepoErrors")
	}
	evs, err := db.DeployEventsSince("personal", time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("read events: %v", err)
	}
	if len(evs) != 1 || evs[0].RepoFullName != "o/good" {
		t.Fatalf("the good repo's events must persist despite the other's failure: %+v", evs)
	}
}

func TestSyncDORAFiltersNonDefaultBranchMerges(t *testing.T) {
	// A merged PR whose BaseRef is not the repo's DefaultBranch is NOT a
	// deployment and must not be stored. This is the filtering Task 4
	// deliberately pushed to the caller.
	app, db := newTestApp(t)
	fp := &fakeProvider{
		kind:  provider.KindGitHub,
		repos: []provider.Repo{{Provider: provider.KindGitHub, Owner: "o", Name: "r", FullName: "o/r", DefaultBranch: "main"}},
		mergedPRsByRepo: map[string][]provider.MergedPR{
			"o/r": {
				{RepoFullName: "o/r", Number: 1, BaseRef: "main", CreatedAt: "2026-09-01T10:00:00Z", MergedAt: "2026-09-01T12:00:00Z"},
				{RepoFullName: "o/r", Number: 2, BaseRef: "release", CreatedAt: "2026-09-02T10:00:00Z", MergedAt: "2026-09-02T12:00:00Z"},
			},
		},
	}
	app.setTestProvider(t, "personal", fp)

	if _, err := app.SyncDORA("personal"); err != nil {
		t.Fatalf("sync: %v", err)
	}
	evs, _ := db.DeployEventsSince("personal", time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if len(evs) != 1 || evs[0].PRNumber != 1 {
		t.Fatalf("only the main-branch merge is a deployment: %+v", evs)
	}
}

func TestSyncDORAAdvancesWatermarkOnlyForSucceedingRepos(t *testing.T) {
	// A repo whose fetch failed must keep its previous watermark so the next
	// sync retries that range instead of skipping it.
}

func TestSyncDORAFlagsTruncatedCommitWindow(t *testing.T) {
	// When the oldest commit returned is still newer than the watermark, the
	// repo must appear in DORASyncResult.Truncated.
}

func TestDORAOverviewEchoesProfileAndCaveats(t *testing.T) {
	// Overview must echo the requested profile and carry the three spec caveats
	// so the UI can render them.
}
```

The two remaining stubs (`TestSyncDORAAdvancesWatermarkOnlyForSucceedingRepos`,
`TestSyncDORAFlagsTruncatedCommitWindow`, `TestDORAOverviewEchoesProfileAndCaveats`)
follow the same construction as the two written above: build a `fakeProvider`, call
the method, assert on the result and on what landed in the DB. Write them fully
before implementing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && go test ./ -run 'TestSyncDORA|TestDORAOverview' -v`
Expected: FAIL — `SyncDORA` undefined.

- [ ] **Step 3: Implement**

`SyncDORA`: resolve providers for the profile the same way `CodeOverview` does; list repos; for each repo read the watermark, call `ListMergedPRs(since: watermark)` and `ListRecentCommits(limit: 250)`; convert commits via `dora.ParseRevertSubject` + `dora.MatchRevert` against the deploy events; upsert both sets; apply the truncation rule from the spec (if the oldest commit seen is still newer than the watermark, record the repo in `Truncated` and advance the watermark only to the oldest commit actually seen). Record per-repo errors in `RepoErrors` and continue.

`DORAOverview`: read events from the store for the window, convert them, call
`dora.Compute`, and populate `Caveats` with the three spec strings. Never hits the
network.

**Conversion is required and is easy to get wrong.** Task 1's `DeployEventRow` /
`FailureEventRow` store timestamps as RFC3339 **strings**; Task 2's
`dora.DeployEvent` / `dora.FailureEvent` use `time.Time`. Write two small
converters in `app_dora.go` and give them a test: a row with an unparseable
timestamp must be **skipped with a logged warning**, never silently coerced to the
zero time — a zero time would land inside every window and corrupt the metrics.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./... -race`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/app_dora.go app/app_dora_test.go
git commit -m "Add DORA overview and incremental sync with per-repo error isolation"
```

---

### Task 7: `RunnerMetrics`

**Files:**
- Modify: `app/app_dora.go`
- Test: `app/app_dora_test.go`

**Interfaces:**
- Consumes: `brainbox.Runner` (`app/brainbox/runners.go`), `brainbox.Task` and `ListTasks` (`app/brainbox/hub.go`), and `dora.StrandedAfter` from Task 2.
- Produces:
  - `func (a *App) RunnerMetrics(profile string) (RunnerMetrics, error)`
  - `type RunnerMetrics struct { Profile string; Available bool; Runners []RunnerRow; Unreachable string }`
  - `type RunnerRow struct { Name, Host, Version string; Tags []string; Online bool; QueueDepth, InFlight, MaxConcurrent int; Completed, Failed, Stranded int; MeanDurationSeconds float64; Backends map[string]int }`

- [ ] **Step 1: Write the failing test**

```go
func TestRunnerMetricsExcludesStrandedTasksFromMeanDuration(t *testing.T) {
	now := time.Now().UTC()
	rf := func(t time.Time) string { return t.Format(time.RFC3339) }

	runners := []brainbox.Runner{{Name: "m3-64", Host: "m3", LastSeen: now.Unix(), MaxConcurrent: 4}}
	tasks := []brainbox.Task{
		{ID: "a", RunnerName: "m3-64", Status: "completed", Backend: "docker",
			CreatedAt: rf(now.Add(-70 * time.Minute)), UpdatedAt: rf(now.Add(-60 * time.Minute))}, // 10m
		{ID: "b", RunnerName: "m3-64", Status: "running", Backend: "docker",
			CreatedAt: rf(now.Add(-13 * time.Hour)), UpdatedAt: rf(now.Add(-12 * time.Hour))}, // stranded
	}
	got := computeRunnerRows(runners, tasks, now) // extract the pure part for testability

	row := got[0]
	if row.Stranded != 1 {
		t.Fatalf("a task running with a 12h-old UpdatedAt is stranded: got %d", row.Stranded)
	}
	if row.MeanDurationSeconds != 600 {
		t.Fatalf("mean must come from the completed task ALONE (600s), got %v", row.MeanDurationSeconds)
	}
}

func TestRunnerMetricsTreatsUnregisteredRunnersTaskAsStranded(t *testing.T) {
	now := time.Now().UTC()
	runners := []brainbox.Runner{{Name: "m3-64", LastSeen: now.Unix(), MaxConcurrent: 4}}
	tasks := []brainbox.Task{
		// Recent UpdatedAt, but "ghost" is not a registered runner.
		{ID: "c", RunnerName: "ghost", Status: "running",
			CreatedAt: now.Add(-5 * time.Minute).Format(time.RFC3339),
			UpdatedAt: now.Add(-1 * time.Minute).Format(time.RFC3339)},
	}
	got := computeRunnerRows(runners, tasks, now)
	var ghost *RunnerRow
	for i := range got {
		if got[i].Name == "ghost" {
			ghost = &got[i]
		}
	}
	if ghost == nil {
		t.Fatal("a task attributed to an unregistered runner must still surface as a row")
	}
	if ghost.Stranded != 1 || ghost.Online {
		t.Fatalf("unregistered runner's running task is stranded and offline: %+v", *ghost)
	}
}

func TestRunnerMetricsUnavailableWhenNoRunners(t *testing.T) {
	// No runners registered => Available false, so the UI hides the section
	// rather than rendering an empty table.
}
```

`TestRunnerMetricsUnavailableWhenNoRunners` follows the same shape: pass an empty
runner slice and assert `Available` is false.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app && go test ./ -run TestRunnerMetrics -v`
Expected: FAIL — `RunnerMetrics` undefined.

- [ ] **Step 3: Implement**

Extract the derivation into a pure helper
`func computeRunnerRows(runners []brainbox.Runner, tasks []brainbox.Task, now time.Time) []RunnerRow`
so the tests above need no App, no DB and no network. `RunnerMetrics` becomes a thin
wrapper: fetch, then call the helper.

Call `ListRunners`; if it errors, return `Available: false` with `Unreachable` set — this must NOT fail `DORAOverview`. Call `ListTasks("", profile)` and group by `RunnerName`. `Online` is `LastSeen` within a freshness bound. A task is stranded per the spec rule (`running` AND (`UpdatedAt` older than `dora.StrandedAfter` OR runner not registered)); stranded tasks are excluded from `MeanDurationSeconds` and counted in `Stranded`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app && go test ./... -race`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/app_dora.go app/app_dora_test.go
git commit -m "Derive per-runner throughput and health, excluding stranded tasks"
```

---

### Task 8: Work hub tab and frontend

**Files:**
- Modify: `app/frontend/src/lib/stores.svelte` (`WorkTab` union gains `'dora'`)
- Modify: `app/frontend/src/lib/panels/WorkHubPanel.svelte` (register the tab)
- Create: `app/frontend/src/lib/components/DoraTab.svelte`
- Regenerate: `app/frontend/wailsjs`

**Interfaces:**
- Consumes: `DORAOverview`, `SyncDORA`, `RunnerMetrics` from Tasks 6-7 via the Wails bindings.
- Produces: no Go names.

- [ ] **Step 1: Regenerate the bindings**

Run the project's binding refresh (`just app-dev` once, or the `wails generate module` step the repo already uses) so `DORAOverview`, `SyncDORA` and `RunnerMetrics` appear under `app/frontend/wailsjs`. Do not hand-write binding files.

- [ ] **Step 2: Add the tab**

In `app/frontend/src/lib/stores.svelte`, extend `WorkTab` with `'dora'`. In `WorkHubPanel.svelte`, add it to the `tabs` derivation with the label `Delivery`, following exactly how the `jira` tab is registered and guarded. Mirror the existing guard: if the active tab becomes unavailable after a profile switch, fall back to `'code'`.

- [ ] **Step 3: Build `DoraTab.svelte`**

Follow `JiraTab.svelte` for structure, loading state, and card styling. Render:
- A 30/90-day toggle driving `windowDays`.
- Four metric cards (deployment frequency, lead time p50/p85, change failure rate, time to restore p50), each with its band badge and its caveat line from `Caveats`.
- A per-repo breakdown table from `Metrics.PerRepo`.
- A Sync button calling `SyncDORA`, showing `LastSyncedAt`, and surfacing `Truncated` repos as a warning and `RepoErrors` inline.
- A runner section rendered ONLY when `RunnerMetrics.Available` is true, with `Stranded` shown as its own column so it is never silently folded into failures.
- The `TokenMissing` banner pointing at the Profiles panel, matching `CodePanel.svelte`'s existing banner — not an error toast.

- [ ] **Step 4: Verify**

Run: `cd app/frontend && npm run check`
Expected: PASS, no svelte-check errors.

Run: `cd app && go test ./... -race`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/frontend/src app/frontend/wailsjs
git commit -m "Add Delivery tab rendering DORA metrics and runner health"
```

---

## Final verification

- [ ] `cd app && go test ./... -race` passes
- [ ] `cd app/frontend && npm run check` passes
- [ ] `git grep -n "TODO(Task 5)" app/` returns nothing (the Task 4 stub was replaced)
- [ ] The three spec caveats are visible in the UI
- [ ] Switching profiles never shows another profile's metrics
