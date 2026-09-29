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
	if env, envErr := a.GetGatewayEnv(profile); envErr == nil {
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
		Profile:    profile,
		WindowDays: windowDays,
		Caveats:    doraCaveats,
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
	env, err := a.GetGatewayEnv(profile)
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
// Repos are walked SEQUENTIALLY, unlike CodeOverview's concurrent read fan-out.
// This path WRITES, and every write is a transaction against one SQLite file;
// parallelism would buy little on a manual button press and cost lock
// contention. The bound on total work is the repo count, not latency.
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
			deploys, failures, truncated, err := syncOneRepo(ctx, c, db, profile, repo, now, horizon)
			if err != nil {
				if provider.IsUnauthorized(err) {
					res.TokenInvalid = true
				}
				res.RepoErrors[repo.FullName] = err.Error()
				continue
			}
			res.ReposSynced++
			res.DeployEventsAdded += deploys
			res.FailureEventsAdded += failures
			if truncated {
				res.Truncated = append(res.Truncated, repo.FullName)
			}
		}
	}
	sort.Strings(res.Truncated)
	return res
}

// syncOneRepo fetches and stores one repo's deploy and revert events.
//
// The watermark advances ONLY on full success. A half-completed sync that moved
// it would leave a permanent hole: the skipped range is never re-requested, so
// a revert inside it would never be counted and the failure rate would read
// low forever. Re-fetching a range is cheap and the upserts are idempotent, so
// retrying is strictly the safer failure mode.
func syncOneRepo(
	ctx context.Context,
	c provider.Client,
	db *DB,
	profile string,
	repo provider.Repo,
	now, horizon time.Time,
) (deployCount, failureCount int, truncated bool, err error) {
	kind := string(c.Kind())
	watermark, err := db.DORAWatermark(profile, kind, repo.FullName)
	if err != nil {
		return 0, 0, false, err
	}
	ref := provider.RepoRef{
		Provider:      repo.Provider,
		Owner:         repo.Owner,
		Name:          repo.Name,
		ID:            repo.ID,
		CloneURL:      repo.CloneURL,
		DefaultBranch: repo.DefaultBranch,
	}

	// --- Deploy events: merges into the DEFAULT branch.
	merged, err := c.ListMergedPRs(ctx, ref, watermark)
	if err != nil {
		return 0, 0, false, fmt.Errorf("merged PRs: %w", err)
	}
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
	if err := db.UpsertDeployEvents(profile, rows); err != nil {
		return 0, 0, false, fmt.Errorf("store deploy events: %w", err)
	}
	deployCount = len(rows)

	// --- Failure events: `Revert "..."` commits on the same branch.
	commits, err := c.ListRecentCommits(ctx, ref, doraCommitLimit)
	if err != nil {
		// The deploys above are already stored and are correct; the watermark
		// still does not move, so this range's reverts get another chance.
		return deployCount, 0, false, fmt.Errorf("recent commits: %w", err)
	}

	// Match reverts against every deploy the store knows for this repo inside
	// the horizon, not just the merges fetched a moment ago: a revert landing
	// today commonly undoes a merge from weeks back, which an earlier sync
	// already recorded.
	known, err := db.DeployEventsSince(profile, horizon)
	if err != nil {
		return deployCount, 0, false, fmt.Errorf("read known deploys: %w", err)
	}
	candidates := make([]dora.DeployEvent, 0, len(known))
	for _, d := range toDoraDeploys(known) {
		if d.RepoFullName == repo.FullName {
			candidates = append(candidates, d)
		}
	}

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
	if err := db.UpsertFailureEvents(profile, failures); err != nil {
		return deployCount, 0, false, fmt.Errorf("store failure events: %w", err)
	}
	failureCount = len(failures)

	// --- Watermark, and the truncation rule.
	//
	// ListRecentCommits is limit-based, so a busy repo can return 250 commits
	// that do not reach back to the watermark. The uncovered range's reverts
	// were never looked at, which would understate the failure rate, so the
	// repo is flagged AND the watermark advances only to the oldest commit
	// actually seen — the next sync then re-covers the gap.
	//
	// Coverage is measured from the watermark, or from the display horizon when
	// the watermark is older (or absent, on a first sync): history the widest
	// window cannot show is not a gap worth warning about.
	coverFrom := watermark
	if coverFrom.Before(horizon) {
		coverFrom = horizon
	}
	next := now
	if !oldestSeen.IsZero() && oldestSeen.After(coverFrom) {
		truncated = true
		next = oldestSeen
	}
	if err := db.SetDORAWatermark(profile, kind, repo.FullName, next); err != nil {
		return deployCount, failureCount, truncated, fmt.Errorf("advance watermark: %w", err)
	}
	return deployCount, failureCount, truncated, nil
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
