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
	if m.LeadTimeP85 != 6*time.Hour {
		t.Fatalf("lead p85: got %v want 6h", m.LeadTimeP85)
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

func TestBandThresholdsMatchTheSpecTable(t *testing.T) {
	freq := []struct {
		perDay float64
		want   Band
	}{
		{2, BandElite},         // multiple per day
		{1, BandHigh},          // daily
		{1.0 / 7, BandHigh},    // weekly
		{1.0 / 20, BandMedium}, // between weekly and monthly
		{1.0 / 60, BandLow},    // less than monthly
	}
	for _, c := range freq {
		if got := bandForDeployFrequency(c.perDay); got != c.want {
			t.Errorf("bandForDeployFrequency(%v) = %s want %s", c.perDay, got, c.want)
		}
	}
	lead := []struct {
		d    time.Duration
		want Band
	}{
		{30 * time.Minute, BandElite},
		{5 * time.Hour, BandHigh},
		{3 * 24 * time.Hour, BandMedium},
		{14 * 24 * time.Hour, BandLow},
	}
	for _, c := range lead {
		if got := bandForLeadTime(c.d); got != c.want {
			t.Errorf("bandForLeadTime(%v) = %s want %s", c.d, got, c.want)
		}
		// Restore uses the same shape, so the same cases must classify alike.
		if got := bandForRestoreTime(c.d); got != c.want {
			t.Errorf("bandForRestoreTime(%v) = %s want %s", c.d, got, c.want)
		}
	}
	cfr := []struct {
		r    float64
		want Band
	}{{0.15, BandElite}, {0.30, BandHigh}, {0.45, BandMedium}, {0.46, BandLow}}
	for _, c := range cfr {
		if got := bandForChangeFailureRate(c.r); got != c.want {
			t.Errorf("bandForChangeFailureRate(%v) = %s want %s", c.r, got, c.want)
		}
	}
}

func TestBandsUseTheExportedKeysOnly(t *testing.T) {
	m := Compute(
		[]DeployEvent{{RepoFullName: "o/r", PRNumber: 1, CreatedAt: day(2).Add(-2 * time.Hour), MergedAt: day(2)}},
		[]FailureEvent{{RepoFullName: "o/r", RevertSHA: "z", MatchedPRNumber: 1, RevertedAt: day(3)}},
		Window{From: day(1), To: day(11)},
	)
	for _, k := range []string{BandKeyDeploymentFrequency, BandKeyLeadTime, BandKeyChangeFailureRate, BandKeyTimeToRestore} {
		if _, ok := m.Bands[k]; !ok {
			t.Errorf("missing band for %q — the frontend reads these exact keys", k)
		}
	}
	if len(m.Bands) != 4 {
		t.Fatalf("unexpected band keys: %+v", m.Bands)
	}
}

// A band is a claim about delivery performance. With no data there is nothing
// to claim, and a zero lead time would otherwise read as "Elite" — the most
// flattering possible lie. Absent beats wrong.
func TestBandsAreAbsentWhenThereIsNothingToClassify(t *testing.T) {
	m := Compute(nil, nil, Window{From: day(1), To: day(11)})
	if _, ok := m.Bands[BandKeyLeadTime]; ok {
		t.Error("no deploys must not yield an Elite lead-time band")
	}
	if _, ok := m.Bands[BandKeyChangeFailureRate]; ok {
		t.Error("no deploys must not yield a change-failure-rate band")
	}
	if _, ok := m.Bands[BandKeyTimeToRestore]; ok {
		t.Error("no restores measured must not yield a time-to-restore band")
	}
	// Zero deploys IS genuinely below monthly, so this band is honest.
	if got := m.Bands[BandKeyDeploymentFrequency]; got != BandLow {
		t.Errorf("zero deploys is a real Low frequency, got %q", got)
	}
}

func TestPerRepoBreakdownSplitsAndDoesNotRecurse(t *testing.T) {
	w := Window{From: day(1), To: day(11)}
	deploys := []DeployEvent{
		{RepoFullName: "o/a", PRNumber: 1, CreatedAt: day(2).Add(-1 * time.Hour), MergedAt: day(2)},
		{RepoFullName: "o/a", PRNumber: 2, CreatedAt: day(3).Add(-1 * time.Hour), MergedAt: day(3)},
		{RepoFullName: "o/b", PRNumber: 9, CreatedAt: day(4).Add(-9 * time.Hour), MergedAt: day(4)},
	}
	failures := []FailureEvent{{RepoFullName: "o/b", RevertSHA: "z", MatchedPRNumber: 9, RevertedAt: day(5)}}
	m := Compute(deploys, failures, w)

	if len(m.PerRepo) != 2 {
		t.Fatalf("want a row per repo, got %+v", m.PerRepo)
	}
	a, b := m.PerRepo["o/a"], m.PerRepo["o/b"]
	if a.DeployCount != 2 || b.DeployCount != 1 {
		t.Fatalf("per-repo deploy counts wrong: a=%d b=%d", a.DeployCount, b.DeployCount)
	}
	// o/a has no reverts; o/b's one revert is 100% of its single deploy.
	if a.FailureCount != 0 || b.FailureCount != 1 {
		t.Fatalf("failures leaked across repos: a=%d b=%d", a.FailureCount, b.FailureCount)
	}
	if b.ChangeFailureRate != 1 {
		t.Fatalf("o/b cfr: got %v want 1", b.ChangeFailureRate)
	}
	if a.PerRepo != nil || b.PerRepo != nil {
		t.Fatal("nested PerRepo must be nil — it would recurse forever through JSON")
	}
}

// A revert whose matched deploy merged BEFORE the window has no measurable
// restore time inside it; counting it would need a merge timestamp the window
// does not contain.
func TestRestoreSkipsFailureWhoseDeployIsOutsideTheWindow(t *testing.T) {
	w := Window{From: day(5), To: day(11)}
	deploys := []DeployEvent{
		{RepoFullName: "o/r", PRNumber: 1, CreatedAt: day(2), MergedAt: day(2)}, // before window
		{RepoFullName: "o/r", PRNumber: 2, CreatedAt: day(6), MergedAt: day(6)},
	}
	failures := []FailureEvent{
		{RepoFullName: "o/r", RevertSHA: "a", MatchedPRNumber: 1, RevertedAt: day(7)},
		{RepoFullName: "o/r", RevertSHA: "b", MatchedPRNumber: 2, RevertedAt: day(6).Add(5 * time.Hour)},
	}
	m := Compute(deploys, failures, w)
	if m.DeployCount != 1 {
		t.Fatalf("only the in-window merge counts: got %d", m.DeployCount)
	}
	if m.FailureCount != 2 {
		t.Fatalf("both reverts happened inside the window: got %d", m.FailureCount)
	}
	if m.RestoreP50 != 5*time.Hour {
		t.Fatalf("only the measurable restore counts: got %v want 5h", m.RestoreP50)
	}
}

// Half-open [From, To): a merge exactly at From is in, one exactly at To is out.
func TestWindowIsHalfOpen(t *testing.T) {
	w := Window{From: day(1), To: day(11)}
	deploys := []DeployEvent{
		{RepoFullName: "o/r", PRNumber: 1, CreatedAt: day(1), MergedAt: day(1)},
		{RepoFullName: "o/r", PRNumber: 2, CreatedAt: day(11), MergedAt: day(11)},
	}
	m := Compute(deploys, nil, w)
	if m.DeployCount != 1 {
		t.Fatalf("From inclusive, To exclusive: got %d", m.DeployCount)
	}
}

func TestZeroLengthWindowDoesNotProduceInfiniteFrequency(t *testing.T) {
	m := Compute(
		[]DeployEvent{{RepoFullName: "o/r", PRNumber: 1, MergedAt: day(1)}},
		nil,
		Window{From: day(1), To: day(1)},
	)
	if m.DeploysPerDay != 0 {
		t.Fatalf("a zero-length window must not divide by zero: got %v", m.DeploysPerDay)
	}
}

func TestPercentileNearestRank(t *testing.T) {
	if got := percentile(nil, 0.5); got != 0 {
		t.Fatalf("empty input is 0, got %v", got)
	}
	xs := []time.Duration{time.Hour, 2 * time.Hour, 3 * time.Hour, 4 * time.Hour}
	if got := percentile(xs, 0.5); got != 2*time.Hour {
		t.Fatalf("p50 of 1..4h = 2h, got %v", got)
	}
	if got := percentile(xs, 0.85); got != 4*time.Hour {
		t.Fatalf("p85 of 1..4h = 4h, got %v", got)
	}
	if got := percentile(xs, 1); got != 4*time.Hour {
		t.Fatalf("p100 must clamp to the last element, got %v", got)
	}
	if got := percentile([]time.Duration{5 * time.Minute}, 0.5); got != 5*time.Minute {
		t.Fatalf("single sample, got %v", got)
	}
}

func TestStrandedAfterIsExportedForTheAppLayer(t *testing.T) {
	// Task 7 reads this from package main; it lives here beside the band
	// constants so both tuning knobs sit in one reviewable place.
	if StrandedAfter != 6*time.Hour {
		t.Fatalf("StrandedAfter = %v want 6h", StrandedAfter)
	}
}

// Wails cannot serialise time.Duration, so the durations cross to the frontend
// as seconds. A mirror that drifts from its duration would render a number
// that contradicts the band badge sitting next to it.
func TestDurationMirrorsMatchTheirDurations(t *testing.T) {
	w := Window{From: day(1), To: day(11)}
	deploys := []DeployEvent{
		{RepoFullName: "o/r", PRNumber: 1, CreatedAt: day(2).Add(-2 * time.Hour), MergedAt: day(2)},
		{RepoFullName: "o/r", PRNumber: 2, CreatedAt: day(3).Add(-6 * time.Hour), MergedAt: day(3)},
	}
	failures := []FailureEvent{{RepoFullName: "o/r", RevertSHA: "z", MatchedPRNumber: 1, RevertedAt: day(2).Add(90 * time.Minute)}}
	m := Compute(deploys, failures, w)

	if m.LeadTimeP50Seconds != m.LeadTimeP50.Seconds() {
		t.Errorf("lead p50 mirror %v != %v", m.LeadTimeP50Seconds, m.LeadTimeP50.Seconds())
	}
	if m.LeadTimeP85Seconds != m.LeadTimeP85.Seconds() {
		t.Errorf("lead p85 mirror %v != %v", m.LeadTimeP85Seconds, m.LeadTimeP85.Seconds())
	}
	if m.RestoreP50Seconds != m.RestoreP50.Seconds() {
		t.Errorf("restore p50 mirror %v != %v", m.RestoreP50Seconds, m.RestoreP50.Seconds())
	}
	if m.RestoreP50Seconds != 5400 {
		t.Errorf("90 minutes is 5400s, got %v", m.RestoreP50Seconds)
	}
	// The per-repo rows go to the same table, so they need mirrors too.
	r := m.PerRepo["o/r"]
	if r.LeadTimeP50Seconds != r.LeadTimeP50.Seconds() || r.LeadTimeP50Seconds == 0 {
		t.Errorf("per-repo mirror not populated: %+v", r)
	}
}

// The Delivery tab's scope selector reads PerAuthor to answer "which of these
// numbers are mine". A revert carries no author of its own, so it is charged
// to the author of the PR it reverted — "whose change got reverted" is the
// right attribution for a change failure rate.
func TestPerAuthorBreakdownSplitsAndDoesNotRecurse(t *testing.T) {
	w := Window{From: day(1), To: day(11)}
	deploys := []DeployEvent{
		{RepoFullName: "o/a", PRNumber: 1, Author: "ada", CreatedAt: day(2).Add(-1 * time.Hour), MergedAt: day(2)},
		{RepoFullName: "o/a", PRNumber: 2, Author: "ada", CreatedAt: day(3).Add(-1 * time.Hour), MergedAt: day(3)},
		{RepoFullName: "o/b", PRNumber: 9, Author: "grace", CreatedAt: day(4).Add(-9 * time.Hour), MergedAt: day(4)},
	}
	failures := []FailureEvent{{RepoFullName: "o/b", RevertSHA: "z", MatchedPRNumber: 9, RevertedAt: day(5)}}
	m := Compute(deploys, failures, w)

	if len(m.PerAuthor) != 2 {
		t.Fatalf("want a row per author, got %+v", m.PerAuthor)
	}
	ada, grace := m.PerAuthor["ada"], m.PerAuthor["grace"]
	if ada.DeployCount != 2 || grace.DeployCount != 1 {
		t.Fatalf("per-author deploy counts wrong: ada=%d grace=%d", ada.DeployCount, grace.DeployCount)
	}
	// grace's single deploy was reverted; ada's were not.
	if ada.FailureCount != 0 || grace.FailureCount != 1 {
		t.Fatalf("failures leaked across authors: ada=%d grace=%d", ada.FailureCount, grace.FailureCount)
	}
	if grace.ChangeFailureRate != 1 {
		t.Fatalf("grace cfr: got %v want 1", grace.ChangeFailureRate)
	}
	if ada.PerAuthor != nil || grace.PerAuthor != nil {
		t.Fatal("nested PerAuthor must be nil — it would recurse forever through JSON")
	}
	// An author bucket KEEPS its per-repo breakdown — the scoped view needs a
	// per-repo table too — but must not nest a further author level.
	if len(ada.PerRepo) != 1 || ada.PerRepo["o/a"].DeployCount != 2 {
		t.Fatalf("ada's bucket lost its per-repo table: %+v", ada.PerRepo)
	}
	if ada.PerRepo["o/a"].PerAuthor != nil || ada.PerRepo["o/a"].PerRepo != nil {
		t.Fatal("a repo inside an author bucket must not nest further")
	}
}

// A revert that could not be traced back to a known deploy belongs to nobody.
// It still counts in the profile-wide failure count, but charging it to an
// arbitrary author would invent a fact. The scope selector's caveat says so.
func TestUnattributableRevertCountsOverallButAgainstNoAuthor(t *testing.T) {
	w := Window{From: day(1), To: day(11)}
	deploys := []DeployEvent{
		{RepoFullName: "o/a", PRNumber: 1, Author: "ada", CreatedAt: day(2).Add(-1 * time.Hour), MergedAt: day(2)},
	}
	// MatchedPRNumber 0 == the revert matched no deploy we know about.
	failures := []FailureEvent{{RepoFullName: "o/a", RevertSHA: "z", MatchedPRNumber: 0, RevertedAt: day(5)}}
	m := Compute(deploys, failures, w)

	if m.FailureCount != 1 {
		t.Fatalf("profile-wide failure count: got %d want 1", m.FailureCount)
	}
	if got := m.PerAuthor["ada"].FailureCount; got != 0 {
		t.Fatalf("an untraceable revert must not be charged to ada, got %d", got)
	}
}

// A deploy whose provider gave us no author cannot be scoped to a person. It
// must not create an empty-string bucket the selector would render as a blank
// row, but it must still count in the profile-wide total.
func TestDeployWithNoAuthorMakesNoBucket(t *testing.T) {
	w := Window{From: day(1), To: day(11)}
	deploys := []DeployEvent{
		{RepoFullName: "o/a", PRNumber: 1, Author: "ada", CreatedAt: day(2).Add(-1 * time.Hour), MergedAt: day(2)},
		{RepoFullName: "o/a", PRNumber: 2, Author: "", CreatedAt: day(3).Add(-1 * time.Hour), MergedAt: day(3)},
	}
	m := Compute(deploys, nil, w)

	if m.DeployCount != 2 {
		t.Fatalf("profile-wide deploy count: got %d want 2", m.DeployCount)
	}
	if _, ok := m.PerAuthor[""]; ok {
		t.Fatal("an empty author must not create a bucket")
	}
	if len(m.PerAuthor) != 1 {
		t.Fatalf("want only ada's bucket, got %+v", m.PerAuthor)
	}
}

// PR numbers restart per repository, so #9 exists in both o/a and o/b. Keying
// the revert-to-author lookup on the number alone would charge o/b's revert to
// o/a's author, inventing a failure for someone who shipped nothing broken.
func TestRevertAttributionDoesNotCrossRepos(t *testing.T) {
	w := Window{From: day(1), To: day(11)}
	// o/a's #9 is listed LAST on purpose: a lookup keyed on the bare number
	// would end up holding ada, so the ordering must not decide attribution.
	deploys := []DeployEvent{
		{RepoFullName: "o/b", PRNumber: 9, Author: "grace", CreatedAt: day(3).Add(-1 * time.Hour), MergedAt: day(3)},
		{RepoFullName: "o/a", PRNumber: 9, Author: "ada", CreatedAt: day(2).Add(-1 * time.Hour), MergedAt: day(2)},
	}
	failures := []FailureEvent{{RepoFullName: "o/b", RevertSHA: "z", MatchedPRNumber: 9, RevertedAt: day(4)}}
	m := Compute(deploys, failures, w)

	if got := m.PerAuthor["ada"].FailureCount; got != 0 {
		t.Fatalf("o/b's revert was charged to o/a's author ada: got %d want 0", got)
	}
	if got := m.PerAuthor["grace"].FailureCount; got != 1 {
		t.Fatalf("grace owns o/b#9 and must carry its revert: got %d want 1", got)
	}
}
