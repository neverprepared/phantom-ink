package main

// The Delivery page: DORA metrics for the active profile, plus fleet runner
// health. Two halves with deliberately different shapes:
//
//   - DORAOverview reads LOCAL SQLITE ONLY and computes. No network, so the
//     30/90-day toggle is instant, the tab works offline, and a provider outage
//     costs nothing. Metric arithmetic lives in the pure dora package.
//   - SyncDORA is the only half that talks to a provider. It fans out per repo
//     from that repo's watermark, so a second run fetches days rather than
//     years.
//
// Everything is profile-scoped, like the Code page it sits beside: the store
// filters on profile, the response echoes it back so a reply landing after a
// profile switch can be discarded, and no accessor reads across profiles.
//
// Per-repo provider failures are recorded and skipped, never returned fatally:
// one repo's 403 must not blank a dashboard covering thirty others.

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"phantom-ink/brainbox"
	"phantom-ink/dora"
	"phantom-ink/provider"
)

const (
	// defaultDORAWindowDays is used when the caller passes a non-positive
	// window. The UI toggle offers 30 and 90.
	defaultDORAWindowDays = 30
	// maxDORAWindowDays is the widest window the dashboard can display, and so
	// the deepest history a sync needs to cover. It bounds both the window
	// clamp and the commit-coverage check.
	maxDORAWindowDays = 90
	// doraCommitLimit is how many commits one repo's revert scan pulls.
	// ListRecentCommits is limit-based rather than since-based, so this is a
	// guess at "enough to reach the watermark" — when it is not enough the
	// repo is reported as Truncated rather than silently missing reverts.
	doraCommitLimit = 250
	// runnerOfflineAfter is how stale a runner's heartbeat may be before it
	// reads as down.
	runnerOfflineAfter = 2 * time.Minute
)

// The three caveats from the approved design. They are NOT optional garnish:
// each metric here is a proxy, and presenting the numbers without them would
// overstate what they measure. Kept as exported data so the tab renders the
// same words the design agreed on.
var doraCaveats = []string{
	"Lead time is a proxy: measured PR-opened to merged, not first-commit to deploy, so it reads optimistic.",
	"Change failure rate undercounts: only reverts are detected, and most breakages are forward-fixed.",
	"Merge is treated as deploy: under batched or manual deploys, deployment frequency overcounts.",
}

// doraScopeCaveats are the two extra ways a ONE-PERSON view is lossier than
// the profile-wide one. They are kept separate from doraCaveats because they
// are only true of a scoped view, and a caveat shown when it does not apply
// teaches people to stop reading them.
var doraScopeCaveats = []string{
	"A revert that matched no known PR is counted profile-wide but charged to nobody, so a person's change failure rate reads low.",
	"A merge whose author the provider did not report appears in the profile total but under no person.",
}

// DORAOverview is one page-load of the Delivery tab, computed from local state.
type DORAOverview struct {
	// Profile echoes back which profile these metrics belong to, so a response
	// landing after a profile switch can be discarded by the panel.
	Profile    string `json:"profile"`
	WindowDays int    `json:"window_days"`
	// TokenMissing is set when the profile has NO git provider configured. It
	// is not an error: the panel shows the "connect a provider" banner.
	TokenMissing bool `json:"token_missing"`
	// TokenInvalid reports that the most recent sync IN THIS PROCESS was
	// rejected (401) for this profile. It cannot be derived offline, so it is
	// remembered from that sync rather than guessed at here.
	TokenInvalid bool `json:"token_invalid"`

	Metrics dora.Metrics `json:"metrics"`
	// LastSyncedAt is the freshest watermark across the profile's repos, "" if
	// it has never synced.
	LastSyncedAt string `json:"last_synced_at"`
	// Caveats are the limitations the UI must display alongside the numbers.
	Caveats []string `json:"caveats"`
	// ScopeCaveats apply ONLY when the Delivery tab is scoped to one author.
	// The tab shows them in addition to Caveats, and only while scoped.
	ScopeCaveats []string `json:"scope_caveats"`
	// SyncError reports a problem reading local state, kept as a string for the
	// same reason CodeOverview's section errors are: it belongs inside the
	// card, not in a toast that blanks the page.
	SyncError string `json:"sync_error"`
}

// DORASyncResult reports what one sync did. Per-repo failures are data here,
// not an error return.
type DORASyncResult struct {
	Profile            string `json:"profile"`
	ReposSynced        int    `json:"repos_synced"`
	DeployEventsAdded  int    `json:"deploy_events_added"`
	FailureEventsAdded int    `json:"failure_events_added"`
	// Truncated names repos whose commit fetch did not reach back to their
	// watermark, so their revert history has a hole. Surfaced rather than
	// swallowed: a silently missing revert understates the failure rate.
	Truncated []string `json:"truncated"`
	// RepoErrors maps a repo's full name to why it failed. Its watermark was
	// NOT advanced, so the next sync retries the same range.
	RepoErrors map[string]string `json:"repo_errors"`
	// TokenInvalid is set when any provider rejected its credential (401).
	TokenInvalid bool `json:"token_invalid"`
}

// doraAuthFailures remembers, per profile, whether the last sync in this
// process hit a 401. Package-level and mutex-guarded, in the same spirit as
// applog: it is process-lifetime diagnostic state, not something worth a
// migration, and DORAOverview has no other way to know — it never calls a
// provider.
var (
	doraAuthMu      sync.Mutex
	doraAuthInvalid = map[string]bool{}
)

func setDORAAuthInvalid(profile string, invalid bool) {
	doraAuthMu.Lock()
	doraAuthInvalid[profile] = invalid
	doraAuthMu.Unlock()
}

func doraAuthIsInvalid(profile string) bool {
	doraAuthMu.Lock()
	defer doraAuthMu.Unlock()
	return doraAuthInvalid[profile]
}

// DORAOverview returns the profile's delivery metrics over the last windowDays,
// computed from local state alone.
func (a *App) DORAOverview(profile string, windowDays int) (DORAOverview, error) {
	out, err := doraOverview(a.db, profile, windowDays, time.Now().UTC())
	if err != nil {
		return out, err
	}
	out.TokenInvalid = doraAuthIsInvalid(profile)
	// A profile with no git provider gets the connect banner, not an error.
	if env, envErr := a.profileEnv(profile); envErr == nil {
		out.TokenMissing = !hasGitProvider(env)
	}
	return out, nil
}

// hasGitProvider mirrors buildProviders' enablement rule: GitHub needs a token,
// ADO needs an org. Checking it here keeps the banner decision identical to the
// one the Code panel makes.
func hasGitProvider(env map[string]string) bool {
	return strings.TrimSpace(env["GITHUB_TOKEN"]) != "" || strings.TrimSpace(env["ADO_ORG"]) != ""
}

// doraOverview is the pure core: store in, metrics out, no network and no
// gateway env. Now is injected so windowing is testable.
func doraOverview(db *DB, profile string, windowDays int, now time.Time) (DORAOverview, error) {
	windowDays = clampDORAWindow(windowDays)
	out := DORAOverview{
		Profile:      profile,
		WindowDays:   windowDays,
		Caveats:      doraCaveats,
		ScopeCaveats: doraScopeCaveats,
	}
	if db == nil {
		return out, errNoDB
	}

	now = now.UTC()
	w := dora.Window{From: now.AddDate(0, 0, -windowDays), To: now}

	deployRows, err := db.DeployEventsSince(profile, w.From)
	if err != nil {
		return out, err
	}
	failureRows, err := db.FailureEventsSince(profile, w.From)
	if err != nil {
		return out, err
	}
	out.Metrics = dora.Compute(toDoraDeploys(deployRows), toDoraFailures(failureRows), w)

	if last, err := db.DORALastSyncedAt(profile); err == nil {
		out.LastSyncedAt = last
	} else {
		out.SyncError = err.Error()
	}
	return out, nil
}

func clampDORAWindow(days int) int {
	if days <= 0 {
		return defaultDORAWindowDays
	}
	if days > maxDORAWindowDays {
		return maxDORAWindowDays
	}
	return days
}

// SyncDORA fetches new deploy and revert events for every repo the profile's
// providers expose, then advances each repo's watermark. Per-repo failures are
// reported in the result; the call itself fails only when the profile's env
// cannot be read at all.
func (a *App) SyncDORA(profile string) (DORASyncResult, error) {
	env, err := a.profileEnv(profile)
	if err != nil {
		return DORASyncResult{Profile: profile}, err
	}
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	azConfigDir := a.profileAzureConfigDir(profile)
	projects, _ := a.resolveADOProjects(ctx, env, azConfigDir)
	clients := buildProviders(env, azConfigDir, projects)
	if len(clients) == 0 {
		// Nothing configured: not an error, just nothing to do. The overview's
		// TokenMissing drives the banner.
		return DORASyncResult{Profile: profile, RepoErrors: map[string]string{}}, nil
	}

	res := syncDORA(ctx, clients, a.db, profile, time.Now().UTC())
	setDORAAuthInvalid(profile, res.TokenInvalid)
	return res, nil
}

// syncDORA is the pure-ish core: given clients and a store it does the whole
// fan-out, so tests drive it with a fake provider and an in-memory DB.
//
// Phased, because the two halves have opposite constraints. The REMOTE FETCH
// is latency-bound — 1-10 paged calls for merged PRs plus one for commits, per
// repo — so it fans out under a concurrency cap. The WRITES stay strictly
// serial: every one is a transaction against a single SQLite file, and the
// pool is deliberately one connection, so parallel writers would only queue.
//
// This used to be one sequential loop whose comment claimed "the bound on
// total work is the repo count, not latency". That was the wrong way round:
// the fetches dominate, so 50 repos on a cold sync meant ~45s to minutes of
// almost entirely waiting.
//
// Ordering guarantees that survive the change:
//   - A repo's watermark still advances ONLY on its own full success, so a
//     partial sync cannot leave a permanent hole.
//   - Revert candidates still include deploys written earlier in this same
//     sync, because every deploy upsert happens before any revert matching.
//   - Results are accumulated in a deterministic repo order, not completion
//     order, so RepoErrors and Truncated do not depend on scheduling.
func syncDORA(ctx context.Context, clients []provider.Client, db *DB, profile string, now time.Time) DORASyncResult {
	res := DORASyncResult{Profile: profile, RepoErrors: map[string]string{}}
	if db == nil {
		res.RepoErrors["*"] = errNoDB.Error()
		return res
	}
	now = now.UTC()
	// The deepest history any displayable window needs. Fetching further back
	// would burn rate limit on events the dashboard cannot show.
	horizon := now.AddDate(0, 0, -maxDORAWindowDays)

	// --- Phase 1: enumerate repos and read each watermark (serial, local).
	type repoJob struct {
		client    provider.Client
		kind      string
		repo      provider.Repo
		watermark time.Time
	}
	var jobs []repoJob
	for _, c := range clients {
		kind := string(c.Kind())
		repos, err := c.ListRepos(ctx)
		if err != nil {
			if provider.IsUnauthorized(err) {
				res.TokenInvalid = true
			}
			// Keyed by provider, not repo: the repo list is what failed, so
			// there is no repo to name.
			res.RepoErrors[kind+" (repo list)"] = err.Error()
			continue
		}
		for _, repo := range repos {
			watermark, err := db.DORAWatermark(profile, kind, repo.FullName)
			if err != nil {
				res.RepoErrors[repo.FullName] = err.Error()
				continue
			}
			jobs = append(jobs, repoJob{client: c, kind: kind, repo: repo, watermark: watermark})
		}
	}
	if len(jobs) == 0 {
		sort.Strings(res.Truncated)
		return res
	}

	// --- Phase 2: fetch every repo's remote data concurrently.
	fetched := make([]repoFetch, len(jobs))
	var wg sync.WaitGroup
	sem := make(chan struct{}, maxConcurrentReads)
	for i, j := range jobs {
		wg.Add(1)
		go func(i int, j repoJob) {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
			case <-ctx.Done():
				fetched[i] = repoFetch{err: ctx.Err()}
				return
			}
			fetched[i] = fetchRepoDORA(ctx, j.client, j.repo, j.watermark)
		}(i, j)
	}
	wg.Wait()

	// --- Phase 3: store deploy events (serial), in job order.
	//
	// All of them, before any revert matching, so a revert can match a deploy
	// this sync just learned about in another repo's pass — the property the
	// old per-repo read provided incidentally.
	deployCounts := make([]int, len(jobs))
	storeFailed := make([]bool, len(jobs))
	for i, j := range jobs {
		f := fetched[i]

		// Store whatever was successfully fetched FIRST, even when a later
		// call failed: those deploys are correct, and the watermark staying
		// put is what makes re-fetching safe.
		if f.mergedOK {
			rows := deployRowsFor(j.kind, j.repo, f.merged)
			if err := db.UpsertDeployEvents(profile, rows); err != nil {
				res.RepoErrors[j.repo.FullName] = fmt.Errorf("store deploy events: %w", err).Error()
				storeFailed[i] = true
				continue
			}
			deployCounts[i] = len(rows)
		}

		if f.err != nil {
			if provider.IsUnauthorized(f.err) {
				res.TokenInvalid = true
			}
			res.RepoErrors[j.repo.FullName] = f.err.Error()
			storeFailed[i] = true
			continue
		}
	}

	// --- Phase 4: read known deploys ONCE and group by repo.
	//
	// This was previously re-read inside every repo's pass — the same
	// profile+horizon query, N times, each returning every repo's rows only to
	// filter all but one away in Go.
	known, err := db.DeployEventsSince(profile, horizon)
	if err != nil {
		res.RepoErrors["*"] = fmt.Errorf("read known deploys: %w", err).Error()
		sort.Strings(res.Truncated)
		return res
	}
	candidatesByRepo := make(map[string][]dora.DeployEvent, len(jobs))
	for _, d := range toDoraDeploys(known) {
		candidatesByRepo[d.RepoFullName] = append(candidatesByRepo[d.RepoFullName], d)
	}

	// --- Phase 5: match reverts, store failures, advance watermarks (serial).
	for i, j := range jobs {
		if storeFailed[i] {
			continue
		}
		f := fetched[i]
		failures, oldestSeen, err := failureRowsFor(j.kind, j.repo, f.commits, candidatesByRepo[j.repo.FullName])
		if err != nil {
			res.RepoErrors[j.repo.FullName] = err.Error()
			continue
		}
		if err := db.UpsertFailureEvents(profile, failures); err != nil {
			res.RepoErrors[j.repo.FullName] = fmt.Errorf("store failure events: %w", err).Error()
			continue
		}

		// --- Watermark, and the truncation rule.
		//
		// ListRecentCommits is limit-based, so a busy repo can return 250
		// commits that do not reach back to the watermark. The uncovered
		// range's reverts were never looked at, which would understate the
		// failure rate, so the repo is flagged AND the watermark advances only
		// to the oldest commit actually seen — the next sync then re-covers
		// the gap.
		//
		// Coverage is measured from the watermark, or from the display horizon
		// when the watermark is older (or absent, on a first sync): history
		// the widest window cannot show is not a gap worth warning about.
		coverFrom := j.watermark
		if coverFrom.Before(horizon) {
			coverFrom = horizon
		}
		next := now
		truncated := false
		if !oldestSeen.IsZero() && oldestSeen.After(coverFrom) {
			truncated = true
			next = oldestSeen
		}
		if err := db.SetDORAWatermark(profile, j.kind, j.repo.FullName, next); err != nil {
			res.RepoErrors[j.repo.FullName] = fmt.Errorf("advance watermark: %w", err).Error()
			continue
		}

		res.ReposSynced++
		res.DeployEventsAdded += deployCounts[i]
		res.FailureEventsAdded += len(failures)
		if truncated {
			res.Truncated = append(res.Truncated, j.repo.FullName)
		}
	}

	sort.Strings(res.Truncated)
	return res
}

// repoFetch is one repo's remote reads, carried from the concurrent fetch
// phase to the serial write phase.
type repoFetch struct {
	merged []provider.MergedPR
	// mergedOK records that the merged-PR call SUCCEEDED, independently of
	// err. A commits failure must still let its deploys be stored -- they were
	// fetched and are correct -- while leaving the watermark alone so the
	// uncovered revert range is retried. Keying that off err alone silently
	// dropped them.
	mergedOK bool
	commits  []provider.Commit
	err      error
}

// fetchRepoDORA does ONLY the remote reads for one repo. No writes, so it is
// safe to run concurrently with other repos.
func fetchRepoDORA(ctx context.Context, c provider.Client, repo provider.Repo, watermark time.Time) repoFetch {
	ref := provider.RepoRef{
		Provider:      repo.Provider,
		Owner:         repo.Owner,
		Name:          repo.Name,
		ID:            repo.ID,
		CloneURL:      repo.CloneURL,
		DefaultBranch: repo.DefaultBranch,
	}
	merged, err := c.ListMergedPRs(ctx, ref, watermark)
	if err != nil {
		return repoFetch{err: fmt.Errorf("merged PRs: %w", err)}
	}
	commits, err := c.ListRecentCommits(ctx, ref, doraCommitLimit)
	if err != nil {
		// Deploys are still usable; the caller stores them and leaves the
		// watermark alone, so this range's reverts get another chance.
		return repoFetch{merged: merged, mergedOK: true, err: fmt.Errorf("recent commits: %w", err)}
	}
	return repoFetch{merged: merged, mergedOK: true, commits: commits}
}

// deployRowsFor converts merged PRs into storable deploy rows.
func deployRowsFor(kind string, repo provider.Repo, merged []provider.MergedPR) []DeployEventRow {
	rows := make([]DeployEventRow, 0, len(merged))
	for _, m := range merged {
		// The provider deliberately does not filter on base branch — the
		// default branch lives on Repo, which only the caller has. A merge
		// into a release or feature branch is not a deployment.
		//
		// An EMPTY DefaultBranch means the provider did not tell us which
		// branch is the trunk. Accepting the merge then is the fail-soft
		// choice: it can overcount (the caveat the UI already states) whereas
		// rejecting everything would blank the dashboard outright.
		if repo.DefaultBranch != "" && m.BaseRef != repo.DefaultBranch {
			continue
		}
		rows = append(rows, DeployEventRow{
			Provider:     kind,
			RepoFullName: repoFullNameOr(m.RepoFullName, repo.FullName),
			PRNumber:     m.Number,
			Title:        m.Title,
			Author:       m.Author,
			CreatedAt:    m.CreatedAt,
			MergedAt:     m.MergedAt,
			MergeSHA:     m.MergeSHA,
			HTMLURL:      m.HTMLURL,
		})
	}
	return rows
}

// failureRowsFor finds `Revert "..."` commits and matches them against this
// repo's known deploys. Also returns the oldest commit date actually seen,
// which drives the truncation rule.
func failureRowsFor(
	kind string,
	repo provider.Repo,
	commits []provider.Commit,
	candidates []dora.DeployEvent,
) ([]FailureEventRow, time.Time, error) {
	oldestSeen := time.Time{}
	failures := make([]FailureEventRow, 0, 8)
	for _, cm := range commits {
		at, perr := time.Parse(time.RFC3339, cm.Date)
		if perr != nil {
			// An undated commit cannot be placed in a window. Skip it loudly
			// rather than dating it to the zero time, which would land inside
			// every window and invent failures.
			logErr("dora: %s commit %s has an unparseable date %q, skipped", repo.FullName, cm.SHA, cm.Date)
			continue
		}
		at = at.UTC()
		if oldestSeen.IsZero() || at.Before(oldestSeen) {
			oldestSeen = at
		}
		subject, ok := dora.ParseRevertSubject(cm.Message)
		if !ok {
			continue
		}
		prNumber, _ := dora.MatchRevert(subject, candidates)
		failures = append(failures, FailureEventRow{
			Provider:        kind,
			RepoFullName:    repo.FullName,
			RevertSHA:       cm.SHA,
			RevertedSubject: subject,
			MatchedPRNumber: prNumber, // 0 when unmatched: still a failure, just untimeable
			RevertedAt:      at.Format(time.RFC3339),
		})
	}
	return failures, oldestSeen, nil
}

// repoFullNameOr prefers the provider's own full name and falls back to the
// repo row's, so the store key is stable even when a payload omits it.
func repoFullNameOr(fromProvider, fallback string) string {
	if strings.TrimSpace(fromProvider) != "" {
		return fromProvider
	}
	return fallback
}

// --- Row -> metric conversion ----------------------------------------------
//
// The store keeps timestamps as RFC3339 strings; the dora package works in
// time.Time. A row whose timestamp will not parse is SKIPPED and logged, never
// coerced to the zero time: the zero time falls inside every window, so a
// single bad row would otherwise add a phantom event to every metric.

func toDoraDeploys(rows []DeployEventRow) []dora.DeployEvent {
	out := make([]dora.DeployEvent, 0, len(rows))
	for _, r := range rows {
		merged, err := time.Parse(time.RFC3339, r.MergedAt)
		if err != nil {
			logErr("dora: deploy %s#%d has an unparseable merged_at %q, skipped", r.RepoFullName, r.PRNumber, r.MergedAt)
			continue
		}
		created, err := time.Parse(time.RFC3339, r.CreatedAt)
		if err != nil {
			logErr("dora: deploy %s#%d has an unparseable created_at %q, skipped", r.RepoFullName, r.PRNumber, r.CreatedAt)
			continue
		}
		out = append(out, dora.DeployEvent{
			RepoFullName: r.RepoFullName,
			PRNumber:     r.PRNumber,
			Title:        r.Title,
			Author:       r.Author,
			CreatedAt:    created.UTC(),
			MergedAt:     merged.UTC(),
		})
	}
	return out
}

func toDoraFailures(rows []FailureEventRow) []dora.FailureEvent {
	out := make([]dora.FailureEvent, 0, len(rows))
	for _, r := range rows {
		at, err := time.Parse(time.RFC3339, r.RevertedAt)
		if err != nil {
			logErr("dora: revert %s %s has an unparseable reverted_at %q, skipped", r.RepoFullName, r.RevertSHA, r.RevertedAt)
			continue
		}
		out = append(out, dora.FailureEvent{
			RepoFullName:    r.RepoFullName,
			RevertSHA:       r.RevertSHA,
			RevertedSubject: r.RevertedSubject,
			MatchedPRNumber: r.MatchedPRNumber,
			RevertedAt:      at.UTC(),
		})
	}
	return out
}

// --- Runner metrics ---------------------------------------------------------
//
// Fleet health, derived rather than sampled: ListRunners gives the live state
// (up/down, queue depth, in-flight vs. capacity) and ListTasks gives the real
// history to reduce per runner. No sampler goroutine, no new tables — the hub
// already holds both halves.

// RunnerMetrics is the runner section of the Delivery tab.
type RunnerMetrics struct {
	// Available is false when no runners are registered or the hub could not
	// be reached. Callers hide the section entirely rather than rendering an
	// empty table, which would read as "the fleet is idle".
	Available bool        `json:"available"`
	Runners   []RunnerRow `json:"runners"`
	// Unreachable explains a hub that did not answer.
	Unreachable string `json:"unreachable"`
}

// RunnerRow is one runner's live state plus its derived throughput.
type RunnerRow struct {
	Name    string   `json:"name"`
	Host    string   `json:"host"`
	Version string   `json:"version"`
	Tags    []string `json:"tags"`
	Online  bool     `json:"online"`

	QueueDepth    int `json:"queue_depth"`
	InFlight      int `json:"in_flight"`
	MaxConcurrent int `json:"max_concurrent"`

	Completed int `json:"completed"`
	Failed    int `json:"failed"`
	// Stranded counts tasks left in "running" by a runner that died or went
	// quiet. It is its OWN number, never folded into Failed: the platform has
	// no liveness reconcile, so these are a known gap to be seen rather than a
	// verdict on the runner's reliability. They are also excluded from
	// MeanDurationSeconds, which they would otherwise inflate without bound.
	Stranded int `json:"stranded"`

	MeanDurationSeconds float64 `json:"mean_duration_seconds"`
	// Backends counts tasks per execution backend (docker | utm | ssh).
	Backends map[string]int `json:"backends"`
}

// RunnerMetrics returns the fleet's live health and derived throughput for one
// profile. A hub that cannot be reached is reported, never returned as an
// error: the DORA half of the page reads local state and must still render.
func (a *App) RunnerMetrics() (RunnerMetrics, error) {
	out := RunnerMetrics{}
	if a.client == nil {
		out.Unreachable = "no brainbox client configured"
		return out, nil
	}
	runners, err := a.client.ListRunners()
	if err != nil {
		out.Unreachable = err.Error()
		return out, nil
	}
	// FLEET-WIDE, deliberately: no profile filter on the task history.
	//
	// This used to take a profile and pass it here, which silently made each
	// row half one scope and half another: queue depth, in flight and max
	// concurrent come off ListRunners and are totals across every profile,
	// while completed / failed / stranded / mean came only from the named
	// profile's tasks. Side by side in one table they read as comparable, so a
	// runner busy with another profile's work showed "queue 5, in flight 5,
	// done 0" under yours. A runner is fleet infrastructure; both halves are
	// now the same scope. Per-profile attribution, if it is ever wanted, is an
	// explicit filter the operator chooses -- not an implicit half of a row.
	tasks, err := a.client.ListTasks("", "")
	if err != nil {
		// Live state without history is still worth showing; say so rather
		// than dropping the section.
		out.Unreachable = err.Error()
	}
	out.Runners = computeRunnerRows(runners, tasks, time.Now().UTC())
	out.Available = len(out.Runners) > 0
	return out, nil
}

// computeRunnerRows is the pure derivation: live runner state joined with task
// history, grouped by runner name. Now is injected so the liveness and
// stranded rules are testable without sleeping.
//
// A task attributed to a runner that is NOT registered still produces a row.
// Hiding it would hide exactly the case worth seeing — work the fleet believes
// is in flight on a machine that is gone.
func computeRunnerRows(runners []brainbox.Runner, tasks []brainbox.Task, now time.Time) []RunnerRow {
	now = now.UTC()

	registered := make(map[string]brainbox.Runner, len(runners))
	rows := make(map[string]*RunnerRow, len(runners))
	for _, r := range runners {
		registered[r.Name] = r
		rows[r.Name] = &RunnerRow{
			Name:          r.Name,
			Host:          r.Host,
			Version:       r.Version,
			Tags:          r.Tags,
			Online:        isRunnerOnline(r, now),
			QueueDepth:    r.QueueDepth,
			InFlight:      r.InFlight,
			MaxConcurrent: r.MaxConcurrent,
			Backends:      map[string]int{},
		}
	}

	// Durations are summed per runner and averaged at the end, so the mean is
	// over terminal, non-stranded tasks only.
	var (
		durSum   = map[string]float64{}
		durCount = map[string]int{}
	)

	for _, t := range tasks {
		name := strings.TrimSpace(t.RunnerName)
		if name == "" {
			// Dispatched in-process, not to a runner. Crediting it to anyone
			// would be an invention.
			continue
		}
		row, ok := rows[name]
		if !ok {
			// A ghost: tasks point at a runner the hub no longer lists.
			row = &RunnerRow{Name: name, Backends: map[string]int{}}
			rows[name] = row
		}
		if b := strings.TrimSpace(t.Backend); b != "" {
			row.Backends[b]++
		}

		if isStrandedTask(t, registered, now) {
			row.Stranded++
			continue
		}
		switch t.Status {
		case "completed", "succeeded", "success":
			row.Completed++
		case "failed", "error", "cancelled", "canceled":
			row.Failed++
		default:
			// Still legitimately in flight: not finished, so no duration and
			// no verdict.
			continue
		}
		// A failure is still a measured run, so it counts toward the mean.
		if d, ok := taskDuration(t); ok {
			durSum[name] += d.Seconds()
			durCount[name]++
		}
	}

	out := make([]RunnerRow, 0, len(rows))
	for name, row := range rows {
		if n := durCount[name]; n > 0 {
			row.MeanDurationSeconds = durSum[name] / float64(n)
		}
		out = append(out, *row)
	}
	// Stable order so the table does not reshuffle between polls.
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// isRunnerOnline reports whether a runner's heartbeat is recent enough to
// believe. LastSeen is epoch seconds; a zero value means it never checked in.
func isRunnerOnline(r brainbox.Runner, now time.Time) bool {
	if r.LastSeen <= 0 {
		return false
	}
	return now.Sub(time.Unix(r.LastSeen, 0).UTC()) < runnerOfflineAfter
}

// isStrandedTask implements the spec's rule: a task in "running" whose
// UpdatedAt is older than dora.StrandedAfter, OR whose runner is not in the
// current ListRunners result (including one that is listed but has gone
// quiet — a runner nobody has heard from cannot be making progress).
//
// These are counted separately and kept out of the duration mean because the
// platform has no liveness reconcile: their UpdatedAt stops advancing while
// wall-clock does not, so averaging them in would drag the mean up without
// bound and hide the real throughput.
func isStrandedTask(t brainbox.Task, registered map[string]brainbox.Runner, now time.Time) bool {
	if t.Status != "running" {
		return false
	}
	r, ok := registered[strings.TrimSpace(t.RunnerName)]
	if !ok || !isRunnerOnline(r, now) {
		return true
	}
	updated := coerceMillis(t.UpdatedAt)
	if updated <= 0 {
		// No usable heartbeat on a task claiming to run: treat it as stranded
		// rather than as an infinitely fast one.
		return true
	}
	return now.Sub(time.UnixMilli(updated).UTC()) > dora.StrandedAfter
}

// taskDuration is CreatedAt -> UpdatedAt for a terminal task. ok is false when
// either timestamp is unusable or the pair is not ordered, so a bad row is
// dropped from the mean rather than contributing a negative or zero duration.
func taskDuration(t brainbox.Task) (time.Duration, bool) {
	start, end := coerceMillis(t.CreatedAt), coerceMillis(t.UpdatedAt)
	if start <= 0 || end <= 0 || end < start {
		return 0, false
	}
	return time.Duration(end-start) * time.Millisecond, true
}
