// Package dora computes the four DORA delivery metrics over a time window.
//
// It is deliberately PURE: no database, no network, no Wails. Events arrive as
// plain slices and leave as numbers, which is what makes the whole metric
// surface unit-testable without a fixture server and lets the app layer recompute
// a 30- or 90-day view from local SQLite with no I/O at all.
//
// The signals are proxies, and the UI is required to say so:
//
//   - A merge into the default branch stands in for a deployment. Under batched
//     or manual deploys that OVERCOUNTS deployment frequency.
//   - PR-created → merged stands in for lead time. True DORA lead time starts at
//     the first commit, so this reads OPTIMISTIC.
//   - A `Revert "..."` commit stands in for a change failure. Most breakages are
//     forward-fixed rather than reverted, so this is a FLOOR, not a real rate.
//
// Do not present these as exact.
package dora

import (
	"math"
	"sort"
	"time"
)

// Window is the half-open interval [From, To) a set of metrics covers. The UI
// toggle passes 30 or 90 days back from now. An event exactly at From is
// included; one exactly at To is not, so consecutive windows neither
// double-count nor drop an event on the boundary.
type Window struct {
	From time.Time
	To   time.Time
}

// Days is the window's length in days, used as the denominator of deployment
// frequency. A non-positive length yields 0 so the caller cannot divide by it.
func (w Window) Days() float64 {
	d := w.To.Sub(w.From)
	if d <= 0 {
		return 0
	}
	return d.Hours() / 24
}

// Contains reports whether t falls in [From, To).
func (w Window) Contains(t time.Time) bool {
	return !t.Before(w.From) && t.Before(w.To)
}

// DeployEvent is one merge into a repo's default branch. CreatedAt is when the
// PR was opened — the lead-time clock start.
type DeployEvent struct {
	RepoFullName string
	PRNumber     int
	Title        string
	CreatedAt    time.Time
	MergedAt     time.Time
}

// FailureEvent is one revert commit on a default branch. MatchedPRNumber is 0
// when the revert could not be traced back to a known deploy: it still counts
// toward the change failure rate, but contributes no time-to-restore sample,
// because there is no merge timestamp to measure from.
type FailureEvent struct {
	RepoFullName    string
	RevertSHA       string
	RevertedSubject string
	MatchedPRNumber int
	RevertedAt      time.Time
}

// Band is a DORA performance classification.
type Band string

const (
	BandElite  Band = "elite"
	BandHigh   Band = "high"
	BandMedium Band = "medium"
	BandLow    Band = "low"
)

// The keys of Metrics.Bands. Exported so the Svelte tab and the tests read the
// same four strings and cannot drift from the producer.
const (
	BandKeyDeploymentFrequency = "deployment_frequency"
	BandKeyLeadTime            = "lead_time"
	BandKeyChangeFailureRate   = "change_failure_rate"
	BandKeyTimeToRestore       = "time_to_restore"
)

// Band thresholds (DORA 2023 banding) and the stranded-task cutoff live here
// together: they are the only two tuning knobs in the whole feature, so one
// reviewable place beats scattering them across the store and the app layer.
const (
	// Deployment frequency, as deploys per day. Elite is "multiple per day";
	// High reaches down to weekly; Medium to monthly.
	freqElitePerDay  = 1.0      // more than one a day
	freqHighPerDay   = 1.0 / 7  // at least weekly
	freqMediumPerDay = 1.0 / 30 // at least monthly

	// Lead time and time to restore share the same ladder.
	durElite  = time.Hour
	durHigh   = 24 * time.Hour
	durMedium = 7 * 24 * time.Hour

	// Change failure rate, as a fraction.
	cfrElite  = 0.15
	cfrHigh   = 0.30
	cfrMedium = 0.45

	// StrandedAfter is how long a task may sit in "running" before it is
	// treated as abandoned rather than slow. The platform has no liveness
	// reconcile, so tasks orphaned by a dead runner would otherwise drag the
	// mean duration up without bound. Consumed by the app layer's runner
	// metrics.
	StrandedAfter = 6 * time.Hour
)

// Metrics is one window's delivery performance, for the aggregate and per repo.
type Metrics struct {
	// DeploysPerDay is merges into default branches ÷ window days.
	DeploysPerDay float64 `json:"deploys_per_day"`
	// LeadTimeP50/P85 are percentiles of merged_at − created_at.
	LeadTimeP50 time.Duration `json:"lead_time_p50"`
	LeadTimeP85 time.Duration `json:"lead_time_p85"`
	// ChangeFailureRate is reverts ÷ deploys, 0 when there are no deploys.
	ChangeFailureRate float64 `json:"change_failure_rate"`
	// RestoreP50 is the median revert_at − merged_at over MATCHED reverts only.
	RestoreP50 time.Duration `json:"restore_p50"`

	DeployCount  int `json:"deploy_count"`
	FailureCount int `json:"failure_count"`
	// RestoreSamples is how many reverts could actually be timed. It is the
	// honest denominator behind RestoreP50, and 0 means the figure is absent
	// rather than instantaneous.
	RestoreSamples int `json:"restore_samples"`

	// Bands classifies each metric, keyed by the BandKey* constants. A metric
	// with nothing to classify is ABSENT rather than banded: a zero lead time
	// would otherwise render as "elite", the most flattering possible lie.
	Bands map[string]Band `json:"bands"`

	// PerRepo is the same metrics computed per repository, keyed by full name.
	// Nested values carry a nil PerRepo — the structure is one level deep by
	// construction, not by luck.
	PerRepo map[string]Metrics `json:"per_repo"`
}

// Compute reduces the events falling inside w to one set of metrics, plus a
// per-repo breakdown. Events outside the window are ignored; nothing here
// mutates its inputs.
func Compute(deploys []DeployEvent, failures []FailureEvent, w Window) Metrics {
	m := computeOne(inWindowDeploys(deploys, w), inWindowFailures(failures, w), w)

	// Per-repo breakdown. Group first so each repo's metrics see only its own
	// events — a revert in one repo must never count against another's deploys.
	byRepoDeploys := map[string][]DeployEvent{}
	byRepoFailures := map[string][]FailureEvent{}
	repos := map[string]bool{}
	for _, d := range inWindowDeploys(deploys, w) {
		byRepoDeploys[d.RepoFullName] = append(byRepoDeploys[d.RepoFullName], d)
		repos[d.RepoFullName] = true
	}
	for _, f := range inWindowFailures(failures, w) {
		byRepoFailures[f.RepoFullName] = append(byRepoFailures[f.RepoFullName], f)
		repos[f.RepoFullName] = true
	}
	if len(repos) > 0 {
		m.PerRepo = make(map[string]Metrics, len(repos))
		for repo := range repos {
			one := computeOne(byRepoDeploys[repo], byRepoFailures[repo], w)
			one.PerRepo = nil
			m.PerRepo[repo] = one
		}
	}
	return m
}

// computeOne is the metric arithmetic over events ALREADY filtered to w.
func computeOne(deploys []DeployEvent, failures []FailureEvent, w Window) Metrics {
	m := Metrics{
		DeployCount:  len(deploys),
		FailureCount: len(failures),
		Bands:        map[string]Band{},
	}

	if days := w.Days(); days > 0 {
		m.DeploysPerDay = float64(len(deploys)) / days
	}

	leads := make([]time.Duration, 0, len(deploys))
	for _, d := range deploys {
		// A merge recorded as earlier than its own PR creation is bad provider
		// data, not a negative lead time; drop the sample rather than let it
		// pull the percentiles below zero.
		if d.CreatedAt.IsZero() || !d.MergedAt.After(d.CreatedAt) {
			continue
		}
		leads = append(leads, d.MergedAt.Sub(d.CreatedAt))
	}
	m.LeadTimeP50 = percentile(leads, 0.50)
	m.LeadTimeP85 = percentile(leads, 0.85)

	if len(deploys) > 0 {
		m.ChangeFailureRate = float64(len(failures)) / float64(len(deploys))
	}

	// Time to restore, over reverts whose original merge is in this window.
	// A revert matched to a merge OUTSIDE the window has no measurable
	// duration here, and an unmatched revert has none at all.
	mergedAt := make(map[repoPR]time.Time, len(deploys))
	for _, d := range deploys {
		mergedAt[repoPR{d.RepoFullName, d.PRNumber}] = d.MergedAt
	}
	restores := make([]time.Duration, 0, len(failures))
	for _, f := range failures {
		if f.MatchedPRNumber == 0 {
			continue
		}
		merged, ok := mergedAt[repoPR{f.RepoFullName, f.MatchedPRNumber}]
		if !ok || !f.RevertedAt.After(merged) {
			continue
		}
		restores = append(restores, f.RevertedAt.Sub(merged))
	}
	m.RestoreSamples = len(restores)
	m.RestoreP50 = percentile(restores, 0.50)

	// Bands. Deployment frequency is always classifiable — zero deploys IS a
	// real "below monthly". The rest need at least one sample to be honest.
	m.Bands[BandKeyDeploymentFrequency] = bandForDeployFrequency(m.DeploysPerDay)
	if len(deploys) > 0 {
		m.Bands[BandKeyChangeFailureRate] = bandForChangeFailureRate(m.ChangeFailureRate)
	}
	if len(leads) > 0 {
		m.Bands[BandKeyLeadTime] = bandForLeadTime(m.LeadTimeP50)
	}
	if len(restores) > 0 {
		m.Bands[BandKeyTimeToRestore] = bandForRestoreTime(m.RestoreP50)
	}
	return m
}

// repoPR keys a deploy by (repo, PR number). PR numbers are only unique within
// a repo, so the repo has to be part of the key or two repos' PR #1 would match
// each other's reverts.
type repoPR struct {
	repo string
	pr   int
}

func inWindowDeploys(in []DeployEvent, w Window) []DeployEvent {
	out := make([]DeployEvent, 0, len(in))
	for _, d := range in {
		if w.Contains(d.MergedAt) {
			out = append(out, d)
		}
	}
	return out
}

func inWindowFailures(in []FailureEvent, w Window) []FailureEvent {
	out := make([]FailureEvent, 0, len(in))
	for _, f := range in {
		if w.Contains(f.RevertedAt) {
			out = append(out, f)
		}
	}
	return out
}

// percentile returns the nearest-rank p-th percentile of xs. Nearest rank (not
// interpolation) keeps every reported figure an actual observed duration, which
// is what a reader of "p85 lead time" expects to be able to point at. Empty
// input is 0 — the caller is responsible for not rendering that as a real
// measurement. xs is copied before sorting, so the caller's slice is untouched.
func percentile(xs []time.Duration, p float64) time.Duration {
	if len(xs) == 0 {
		return 0
	}
	sorted := append([]time.Duration(nil), xs...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	i := int(math.Ceil(p*float64(len(sorted)))) - 1
	if i < 0 {
		i = 0
	}
	if i >= len(sorted) {
		i = len(sorted) - 1
	}
	return sorted[i]
}

func bandForDeployFrequency(perDay float64) Band {
	switch {
	case perDay > freqElitePerDay:
		return BandElite
	case perDay >= freqHighPerDay:
		return BandHigh
	case perDay >= freqMediumPerDay:
		return BandMedium
	default:
		return BandLow
	}
}

func bandForLeadTime(d time.Duration) Band { return bandForDuration(d) }

func bandForRestoreTime(d time.Duration) Band { return bandForDuration(d) }

// bandForDuration is the shared ladder behind lead time and time to restore:
// the spec gives both the same thresholds, so they get the same code rather
// than two copies that can drift apart.
func bandForDuration(d time.Duration) Band {
	switch {
	case d < durElite:
		return BandElite
	case d < durHigh:
		return BandHigh
	case d < durMedium:
		return BandMedium
	default:
		return BandLow
	}
}

func bandForChangeFailureRate(rate float64) Band {
	switch {
	case rate <= cfrElite:
		return BandElite
	case rate <= cfrHigh:
		return BandHigh
	case rate <= cfrMedium:
		return BandMedium
	default:
		return BandLow
	}
}
