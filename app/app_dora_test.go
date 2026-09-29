package main

import (
	"context"
	"errors"
	"testing"
	"time"

	"phantom-ink/brainbox"
	"phantom-ink/dora"
	"phantom-ink/provider"
)

// The DORA app layer is tested through its PURE cores — syncDORA and
// doraOverview — exactly as app_code_test.go tests buildCodeOverview rather
// than CodeOverview. That keeps these tests free of gateway env, provider
// construction and the network, and leaves the App methods as the thin
// resolve-then-delegate wrappers they should be.

func doraTestRepo(owner, name, branch string) provider.Repo {
	return provider.Repo{
		Provider: provider.KindGitHub, Owner: owner, Name: name,
		FullName: owner + "/" + name, DefaultBranch: branch,
	}
}

func TestSyncDORAIsolatesPerRepoErrors(t *testing.T) {
	db := newMigratedTestDB(t)
	fp := &fakeProvider{
		kind: provider.KindGitHub,
		repos: []provider.Repo{
			doraTestRepo("o", "good", "main"),
			doraTestRepo("o", "bad", "main"),
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

	res := syncDORA(context.Background(), []provider.Client{fp}, db, "personal", time.Now().UTC())

	if got := res.RepoErrors["o/bad"]; got == "" {
		t.Fatal("the failing repo must be reported in RepoErrors")
	}
	if _, ok := res.RepoErrors["o/good"]; ok {
		t.Fatalf("the succeeding repo must not appear in RepoErrors: %+v", res.RepoErrors)
	}
	if res.ReposSynced != 1 {
		t.Fatalf("only the good repo synced: got %d", res.ReposSynced)
	}
	if res.Profile != "personal" {
		t.Fatalf("profile must be echoed: %q", res.Profile)
	}
	evs, err := db.DeployEventsSince("personal", time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("read events: %v", err)
	}
	if len(evs) != 1 || evs[0].RepoFullName != "o/good" {
		t.Fatalf("the good repo's events must persist despite the other's failure: %+v", evs)
	}
	if res.DeployEventsAdded != 1 {
		t.Fatalf("DeployEventsAdded = %d want 1", res.DeployEventsAdded)
	}
}

func TestSyncDORAFiltersNonDefaultBranchMerges(t *testing.T) {
	db := newMigratedTestDB(t)
	fp := &fakeProvider{
		kind:  provider.KindGitHub,
		repos: []provider.Repo{doraTestRepo("o", "r", "main")},
		mergedPRsByRepo: map[string][]provider.MergedPR{
			"o/r": {
				{RepoFullName: "o/r", Number: 1, BaseRef: "main", CreatedAt: "2026-09-01T10:00:00Z", MergedAt: "2026-09-01T12:00:00Z"},
				{RepoFullName: "o/r", Number: 2, BaseRef: "release", CreatedAt: "2026-09-02T10:00:00Z", MergedAt: "2026-09-02T12:00:00Z"},
			},
		},
	}

	syncDORA(context.Background(), []provider.Client{fp}, db, "personal", time.Now().UTC())

	evs, _ := db.DeployEventsSince("personal", time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if len(evs) != 1 || evs[0].PRNumber != 1 {
		t.Fatalf("only the main-branch merge is a deployment: %+v", evs)
	}
}

func TestSyncDORAAdvancesWatermarkOnlyForSucceedingRepos(t *testing.T) {
	db := newMigratedTestDB(t)
	// The failing repo starts with a watermark; after the failed sync it must
	// still hold it, so the next run retries that range instead of skipping
	// the merges that happened inside it.
	before := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	if err := db.SetDORAWatermark("personal", "github", "o/bad", before); err != nil {
		t.Fatalf("seed watermark: %v", err)
	}
	now := time.Date(2026, 9, 20, 0, 0, 0, 0, time.UTC)
	fp := &fakeProvider{
		kind: provider.KindGitHub,
		repos: []provider.Repo{
			doraTestRepo("o", "good", "main"),
			doraTestRepo("o", "bad", "main"),
		},
		mergedPRsErrByRepo: map[string]error{"o/bad": errors.New("403 forbidden")},
	}

	syncDORA(context.Background(), []provider.Client{fp}, db, "personal", now)

	bad, err := db.DORAWatermark("personal", "github", "o/bad")
	if err != nil {
		t.Fatalf("read bad watermark: %v", err)
	}
	if !bad.Equal(before) {
		t.Fatalf("a failed repo's watermark must not move: got %v want %v", bad, before)
	}
	good, err := db.DORAWatermark("personal", "github", "o/good")
	if err != nil {
		t.Fatalf("read good watermark: %v", err)
	}
	if !good.Equal(now) {
		t.Fatalf("a succeeding repo's watermark advances to the sync time: got %v want %v", good, now)
	}
	// The watermark it held is what gets asked for on the next fetch.
	if got := fp.sinceFor("o/bad"); !got.Equal(before) {
		t.Fatalf("the stored watermark must be the since argument: got %v want %v", got, before)
	}
}

// A commit failure must not advance the watermark either: the deploy half
// landing while the revert half silently skipped a range would leave a
// permanent hole in the change-failure rate.
func TestSyncDORADoesNotAdvanceWatermarkWhenCommitsFail(t *testing.T) {
	db := newMigratedTestDB(t)
	now := time.Date(2026, 9, 20, 0, 0, 0, 0, time.UTC)
	fp := &fakeProvider{
		kind:  provider.KindGitHub,
		repos: []provider.Repo{doraTestRepo("o", "r", "main")},
		mergedPRsByRepo: map[string][]provider.MergedPR{
			"o/r": {{RepoFullName: "o/r", Number: 1, BaseRef: "main",
				CreatedAt: "2026-09-01T10:00:00Z", MergedAt: "2026-09-01T12:00:00Z"}},
		},
		commitsErrByRepo: map[string]error{"o/r": errors.New("500 server error")},
	}

	res := syncDORA(context.Background(), []provider.Client{fp}, db, "personal", now)

	if res.RepoErrors["o/r"] == "" {
		t.Fatal("the commit failure must be reported per repo")
	}
	wm, _ := db.DORAWatermark("personal", "github", "o/r")
	if !wm.IsZero() {
		t.Fatalf("a half-failed sync must not advance the watermark: got %v", wm)
	}
	// The deploys that DID arrive are still worth keeping — they are correct,
	// just not yet paired with their revert history.
	evs, _ := db.DeployEventsSince("personal", time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC))
	if len(evs) != 1 {
		t.Fatalf("successfully fetched deploys must persist: %+v", evs)
	}
}

func TestSyncDORAFlagsTruncatedCommitWindow(t *testing.T) {
	db := newMigratedTestDB(t)
	now := time.Date(2026, 9, 20, 0, 0, 0, 0, time.UTC)
	// Watermark 10 days back, but the oldest commit returned is only 2 days
	// old — days 10→2 were never looked at, so any revert in them is missing.
	watermark := now.AddDate(0, 0, -10)
	if err := db.SetDORAWatermark("personal", "github", "o/r", watermark); err != nil {
		t.Fatalf("seed watermark: %v", err)
	}
	oldest := now.AddDate(0, 0, -2)
	fp := &fakeProvider{
		kind:  provider.KindGitHub,
		repos: []provider.Repo{doraTestRepo("o", "r", "main")},
		commitsByRepo: map[string][]provider.Commit{
			"o/r": {
				{SHA: "c1", Message: "chore: bump", Date: now.AddDate(0, 0, -1).Format(time.RFC3339)},
				{SHA: "c2", Message: "chore: tidy", Date: oldest.Format(time.RFC3339)},
			},
		},
	}

	res := syncDORA(context.Background(), []provider.Client{fp}, db, "personal", now)

	if len(res.Truncated) != 1 || res.Truncated[0] != "o/r" {
		t.Fatalf("an uncovered commit range must be reported as truncated: %+v", res.Truncated)
	}
	wm, _ := db.DORAWatermark("personal", "github", "o/r")
	if !wm.Equal(oldest.UTC()) {
		t.Fatalf("a truncated sync advances only to the oldest commit seen: got %v want %v", wm, oldest.UTC())
	}
	if got := fp.commitLimitFor("o/r"); got != doraCommitLimit {
		t.Fatalf("commit fetch limit = %d want %d", got, doraCommitLimit)
	}
}

// The mirror case: commits reach back past the watermark, so the range IS
// covered and nothing is flagged.
func TestSyncDORADoesNotFlagACoveredCommitWindow(t *testing.T) {
	db := newMigratedTestDB(t)
	now := time.Date(2026, 9, 20, 0, 0, 0, 0, time.UTC)
	watermark := now.AddDate(0, 0, -5)
	if err := db.SetDORAWatermark("personal", "github", "o/r", watermark); err != nil {
		t.Fatalf("seed watermark: %v", err)
	}
	fp := &fakeProvider{
		kind:  provider.KindGitHub,
		repos: []provider.Repo{doraTestRepo("o", "r", "main")},
		commitsByRepo: map[string][]provider.Commit{
			"o/r": {
				{SHA: "c1", Message: "chore: bump", Date: now.AddDate(0, 0, -1).Format(time.RFC3339)},
				{SHA: "c2", Message: "chore: old", Date: now.AddDate(0, 0, -9).Format(time.RFC3339)},
			},
		},
	}

	res := syncDORA(context.Background(), []provider.Client{fp}, db, "personal", now)

	if len(res.Truncated) != 0 {
		t.Fatalf("the watermark was reached, nothing is missing: %+v", res.Truncated)
	}
	wm, _ := db.DORAWatermark("personal", "github", "o/r")
	if !wm.Equal(now) {
		t.Fatalf("a covered sync advances to the sync time: got %v want %v", wm, now)
	}
}

// A revert commit becomes a failure event, matched back to the merge it undid
// so the pair yields a time to restore.
func TestSyncDORARecordsRevertsAndMatchesThemToMerges(t *testing.T) {
	db := newMigratedTestDB(t)
	now := time.Date(2026, 9, 20, 0, 0, 0, 0, time.UTC)
	fp := &fakeProvider{
		kind:  provider.KindGitHub,
		repos: []provider.Repo{doraTestRepo("o", "r", "main")},
		mergedPRsByRepo: map[string][]provider.MergedPR{
			"o/r": {{RepoFullName: "o/r", Number: 41, Title: "Fix the bug (#41)", BaseRef: "main",
				CreatedAt: "2026-09-10T10:00:00Z", MergedAt: "2026-09-10T12:00:00Z"}},
		},
		commitsByRepo: map[string][]provider.Commit{
			"o/r": {
				{SHA: "rev1", Message: "Revert \"Fix the bug (#41)\"\n\nThis reverts commit abc.",
					Date: "2026-09-11T12:00:00Z"},
				{SHA: "rev2", Message: "Revert \"Something nobody knows\"", Date: "2026-09-12T12:00:00Z"},
				{SHA: "plain", Message: "chore: not a revert", Date: "2026-09-13T12:00:00Z"},
			},
		},
	}

	res := syncDORA(context.Background(), []provider.Client{fp}, db, "personal", now)

	if res.FailureEventsAdded != 2 {
		t.Fatalf("both reverts count as failures, the plain commit does not: got %d", res.FailureEventsAdded)
	}
	fails, err := db.FailureEventsSince("personal", time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("read failures: %v", err)
	}
	if len(fails) != 2 {
		t.Fatalf("want 2 stored failures: %+v", fails)
	}
	byS := map[string]FailureEventRow{}
	for _, f := range fails {
		byS[f.RevertSHA] = f
	}
	if byS["rev1"].MatchedPRNumber != 41 {
		t.Fatalf("a revert naming a known PR must be matched: %+v", byS["rev1"])
	}
	if byS["rev2"].MatchedPRNumber != 0 {
		t.Fatalf("an unmatchable revert is stored unmatched, not dropped: %+v", byS["rev2"])
	}
	if byS["rev2"].RevertedSubject != "Something nobody knows" {
		t.Fatalf("the parsed subject must be kept so the UI can explain it: %+v", byS["rev2"])
	}
}

// Timestamps cross the store as RFC3339 strings and the metrics need
// time.Time. An unparseable one must be SKIPPED, never coerced to the zero
// time — a zero time lands inside every window and would corrupt the metrics.
func TestConvertersSkipUnparseableTimestamps(t *testing.T) {
	deploys := toDoraDeploys([]DeployEventRow{
		{RepoFullName: "o/r", PRNumber: 1, CreatedAt: "2026-09-01T10:00:00Z", MergedAt: "2026-09-01T12:00:00Z"},
		{RepoFullName: "o/r", PRNumber: 2, CreatedAt: "2026-09-02T10:00:00Z", MergedAt: "not a timestamp"},
		{RepoFullName: "o/r", PRNumber: 3, CreatedAt: "garbage", MergedAt: "2026-09-03T12:00:00Z"},
	})
	if len(deploys) != 1 || deploys[0].PRNumber != 1 {
		t.Fatalf("only the fully-parseable deploy survives: %+v", deploys)
	}
	if deploys[0].MergedAt.IsZero() || deploys[0].CreatedAt.IsZero() {
		t.Fatalf("a surviving row must carry real times: %+v", deploys[0])
	}

	fails := toDoraFailures([]FailureEventRow{
		{RepoFullName: "o/r", RevertSHA: "a", RevertedAt: "2026-09-04T12:00:00Z"},
		{RepoFullName: "o/r", RevertSHA: "b", RevertedAt: ""},
	})
	if len(fails) != 1 || fails[0].RevertSHA != "a" {
		t.Fatalf("only the parseable failure survives: %+v", fails)
	}
}

func TestDORAOverviewEchoesProfileAndCaveats(t *testing.T) {
	db := newMigratedTestDB(t)
	now := time.Date(2026, 9, 20, 12, 0, 0, 0, time.UTC)
	rows := []DeployEventRow{
		{Provider: "github", RepoFullName: "o/r", PRNumber: 1, Title: "a",
			CreatedAt: now.AddDate(0, 0, -5).Add(-2 * time.Hour).Format(time.RFC3339),
			MergedAt:  now.AddDate(0, 0, -5).Format(time.RFC3339)},
		// Outside a 30-day window, inside a 90-day one.
		{Provider: "github", RepoFullName: "o/r", PRNumber: 2, Title: "b",
			CreatedAt: now.AddDate(0, 0, -50).Add(-2 * time.Hour).Format(time.RFC3339),
			MergedAt:  now.AddDate(0, 0, -50).Format(time.RFC3339)},
	}
	if err := db.UpsertDeployEvents("personal", rows); err != nil {
		t.Fatalf("seed: %v", err)
	}
	if err := db.SetDORAWatermark("personal", "github", "o/r", now.Add(-time.Hour)); err != nil {
		t.Fatalf("seed watermark: %v", err)
	}

	ov, err := doraOverview(db, "personal", 30, now)
	if err != nil {
		t.Fatalf("doraOverview: %v", err)
	}
	if ov.Profile != "personal" {
		t.Fatalf("profile must be echoed so a reply after a profile switch can be discarded: %q", ov.Profile)
	}
	if ov.WindowDays != 30 {
		t.Fatalf("window echoed back: %d", ov.WindowDays)
	}
	if len(ov.Caveats) != 3 {
		t.Fatalf("all three spec caveats must reach the UI: %+v", ov.Caveats)
	}
	if ov.Metrics.DeployCount != 1 {
		t.Fatalf("the 30-day window excludes the 50-day-old merge: got %d", ov.Metrics.DeployCount)
	}
	if ov.LastSyncedAt == "" {
		t.Fatal("LastSyncedAt must be reported so the UI can show sync freshness")
	}

	wide, err := doraOverview(db, "personal", 90, now)
	if err != nil {
		t.Fatalf("doraOverview 90: %v", err)
	}
	if wide.Metrics.DeployCount != 2 {
		t.Fatalf("the 90-day window includes both: got %d", wide.Metrics.DeployCount)
	}
}

func TestDORAOverviewNeverSeesAnotherProfilesEvents(t *testing.T) {
	db := newMigratedTestDB(t)
	now := time.Date(2026, 9, 20, 12, 0, 0, 0, time.UTC)
	row := DeployEventRow{
		Provider: "github", RepoFullName: "o/r", PRNumber: 1,
		CreatedAt: now.AddDate(0, 0, -2).Add(-time.Hour).Format(time.RFC3339),
		MergedAt:  now.AddDate(0, 0, -2).Format(time.RFC3339),
	}
	if err := db.UpsertDeployEvents("work", []DeployEventRow{row}); err != nil {
		t.Fatalf("seed: %v", err)
	}
	ov, err := doraOverview(db, "personal", 30, now)
	if err != nil {
		t.Fatalf("doraOverview: %v", err)
	}
	if ov.Metrics.DeployCount != 0 {
		t.Fatalf("personal must not see work's deploys: %+v", ov.Metrics)
	}
	// An empty window is a zero state with an explanation, not an error.
	if len(ov.Caveats) != 3 {
		t.Fatalf("caveats render even with no data: %+v", ov.Caveats)
	}
}

func TestDORAOverviewDefaultsAnInvalidWindow(t *testing.T) {
	db := newMigratedTestDB(t)
	now := time.Date(2026, 9, 20, 12, 0, 0, 0, time.UTC)
	for _, days := range []int{0, -1} {
		ov, err := doraOverview(db, "personal", days, now)
		if err != nil {
			t.Fatalf("doraOverview(%d): %v", days, err)
		}
		if ov.WindowDays != defaultDORAWindowDays {
			t.Fatalf("windowDays %d must default to %d, got %d", days, defaultDORAWindowDays, ov.WindowDays)
		}
	}
}

func TestDORAOverviewWithNoDBIsAnError(t *testing.T) {
	if _, err := doraOverview(nil, "personal", 30, time.Now()); err == nil {
		t.Fatal("a nil store must be reported, not silently rendered as zero metrics")
	}
}

// The band keys the tab reads come straight from the dora package, so the
// overview and the frontend cannot drift apart.
func TestDORAOverviewCarriesBandKeys(t *testing.T) {
	db := newMigratedTestDB(t)
	now := time.Date(2026, 9, 20, 12, 0, 0, 0, time.UTC)
	if err := db.UpsertDeployEvents("personal", []DeployEventRow{{
		Provider: "github", RepoFullName: "o/r", PRNumber: 1,
		CreatedAt: now.AddDate(0, 0, -2).Add(-time.Hour).Format(time.RFC3339),
		MergedAt:  now.AddDate(0, 0, -2).Format(time.RFC3339),
	}}); err != nil {
		t.Fatalf("seed: %v", err)
	}
	ov, err := doraOverview(db, "personal", 30, now)
	if err != nil {
		t.Fatalf("doraOverview: %v", err)
	}
	if _, ok := ov.Metrics.Bands[dora.BandKeyDeploymentFrequency]; !ok {
		t.Fatalf("missing deployment-frequency band: %+v", ov.Metrics.Bands)
	}
	if _, ok := ov.Metrics.PerRepo["o/r"]; !ok {
		t.Fatalf("per-repo breakdown missing: %+v", ov.Metrics.PerRepo)
	}
}

// --- Runner metrics ---------------------------------------------------------
//
// computeRunnerRows is the pure derivation, so these need no App, no DB and no
// hub. The property that matters most: a task the platform ABANDONED must not
// be averaged in as a very slow one.

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
	got := computeRunnerRows(runners, tasks, now)

	if len(got) != 1 {
		t.Fatalf("want one row: %+v", got)
	}
	row := got[0]
	if row.Stranded != 1 {
		t.Fatalf("a task running with a 12h-old UpdatedAt is stranded: got %d", row.Stranded)
	}
	if row.MeanDurationSeconds != 600 {
		t.Fatalf("mean must come from the completed task ALONE (600s), got %v", row.MeanDurationSeconds)
	}
	if row.Completed != 1 {
		t.Fatalf("completed = %d want 1", row.Completed)
	}
	if !row.Online {
		t.Fatal("a runner seen just now is online")
	}
	if row.Backends["docker"] != 2 {
		t.Fatalf("backend split counts every task: %+v", row.Backends)
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
	if ghost.MeanDurationSeconds != 0 {
		t.Fatalf("a stranded task contributes no duration: %+v", *ghost)
	}
}

func TestRunnerMetricsUnavailableWhenNoRunners(t *testing.T) {
	// No runners registered and no tasks: the UI hides the whole section
	// rather than rendering an empty table.
	rows := computeRunnerRows(nil, nil, time.Now().UTC())
	if len(rows) != 0 {
		t.Fatalf("nothing registered yields no rows: %+v", rows)
	}
}

// A task still legitimately in flight — recent heartbeat, registered runner —
// is neither stranded nor finished, so it contributes to InFlight only.
func TestRunnerMetricsKeepsHealthyRunningTasksOutOfEveryTotal(t *testing.T) {
	now := time.Now().UTC()
	runners := []brainbox.Runner{{Name: "m3-64", LastSeen: now.Unix(), MaxConcurrent: 4, InFlight: 1, QueueDepth: 3}}
	tasks := []brainbox.Task{
		{ID: "live", RunnerName: "m3-64", Status: "running",
			CreatedAt: now.Add(-3 * time.Minute).Format(time.RFC3339),
			UpdatedAt: now.Add(-1 * time.Minute).Format(time.RFC3339)},
	}
	row := computeRunnerRows(runners, tasks, now)[0]
	if row.Stranded != 0 {
		t.Fatalf("a fresh running task is not stranded: %+v", row)
	}
	if row.Completed != 0 || row.Failed != 0 {
		t.Fatalf("a running task is neither completed nor failed: %+v", row)
	}
	if row.MeanDurationSeconds != 0 {
		t.Fatalf("an unfinished task has no duration to average: %+v", row)
	}
	// Live counters come from the runner's own report, not derived from tasks.
	if row.InFlight != 1 || row.QueueDepth != 3 || row.MaxConcurrent != 4 {
		t.Fatalf("live counters must pass through: %+v", row)
	}
}

func TestRunnerMetricsCountsFailuresAndAveragesTerminalTasksOnly(t *testing.T) {
	now := time.Now().UTC()
	rf := func(d time.Duration) string { return now.Add(d).Format(time.RFC3339) }
	runners := []brainbox.Runner{{Name: "m3-64", LastSeen: now.Unix()}}
	tasks := []brainbox.Task{
		{ID: "1", RunnerName: "m3-64", Status: "completed", CreatedAt: rf(-40 * time.Minute), UpdatedAt: rf(-30 * time.Minute)}, // 10m
		{ID: "2", RunnerName: "m3-64", Status: "failed", CreatedAt: rf(-40 * time.Minute), UpdatedAt: rf(-20 * time.Minute)},    // 20m
	}
	row := computeRunnerRows(runners, tasks, now)[0]
	if row.Completed != 1 || row.Failed != 1 {
		t.Fatalf("one of each: %+v", row)
	}
	// A failure is still a measured run, so it belongs in the mean.
	if row.MeanDurationSeconds != 900 {
		t.Fatalf("mean of 10m and 20m is 900s, got %v", row.MeanDurationSeconds)
	}
}

// A runner whose heartbeat has gone quiet reads as down, and every task it was
// running is stranded by definition — that is the platform gap this exists to
// make visible.
func TestRunnerMetricsMarksAStaleRunnerOfflineAndItsTasksStranded(t *testing.T) {
	now := time.Now().UTC()
	runners := []brainbox.Runner{{Name: "dead", LastSeen: now.Add(-1 * time.Hour).Unix()}}
	tasks := []brainbox.Task{
		{ID: "x", RunnerName: "dead", Status: "running",
			CreatedAt: now.Add(-10 * time.Minute).Format(time.RFC3339),
			UpdatedAt: now.Add(-2 * time.Minute).Format(time.RFC3339)},
	}
	row := computeRunnerRows(runners, tasks, now)[0]
	if row.Online {
		t.Fatalf("an hour-old heartbeat is not online: %+v", row)
	}
	if row.Stranded != 1 {
		t.Fatalf("a dead runner's running task is stranded: %+v", row)
	}
}

// Tasks dispatched in-process carry no runner name. They must not be folded
// into a real runner's numbers, nor invent a blank-named row.
func TestRunnerMetricsIgnoresTasksWithNoRunner(t *testing.T) {
	now := time.Now().UTC()
	runners := []brainbox.Runner{{Name: "m3-64", LastSeen: now.Unix()}}
	tasks := []brainbox.Task{
		{ID: "inproc", RunnerName: "", Status: "completed",
			CreatedAt: now.Add(-20 * time.Minute).Format(time.RFC3339),
			UpdatedAt: now.Add(-10 * time.Minute).Format(time.RFC3339)},
	}
	rows := computeRunnerRows(runners, tasks, now)
	if len(rows) != 1 || rows[0].Name != "m3-64" {
		t.Fatalf("an unattributed task must not create a row: %+v", rows)
	}
	if rows[0].Completed != 0 {
		t.Fatalf("an unattributed task must not be credited to a runner: %+v", rows[0])
	}
}

func TestRunnerMetricsRowsAreSortedByName(t *testing.T) {
	now := time.Now().UTC()
	runners := []brainbox.Runner{
		{Name: "zeta", LastSeen: now.Unix()},
		{Name: "alpha", LastSeen: now.Unix()},
	}
	rows := computeRunnerRows(runners, nil, now)
	if len(rows) != 2 || rows[0].Name != "alpha" || rows[1].Name != "zeta" {
		t.Fatalf("rows must be stably ordered for the UI: %+v", rows)
	}
}
