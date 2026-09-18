package doctor

import (
	"fmt"
	"strings"
	"testing"
)

// --- fake fleet --------------------------------------------------------

// fakeFleet is the FleetClient stand-in. No test in this package touches a
// real fleet: every remote outcome is expressed as a canned reply here.
type fakeFleet struct {
	runners []FleetRunner

	// canned replies
	createErr error
	execErr   error
	execRes   FleetExecResult
	listErr   error

	// recorded calls
	created      []FleetSessionSpec
	execCmds     []string
	execSessions []string
	deleted      []string
}

func (f *fakeFleet) ListRunners() ([]FleetRunner, error) {
	if f.listErr != nil {
		return nil, f.listErr
	}
	return f.runners, nil
}

func (f *fakeFleet) CreateSession(spec FleetSessionSpec) error {
	f.created = append(f.created, spec)
	return f.createErr
}

func (f *fakeFleet) Exec(name, command string) (FleetExecResult, error) {
	f.execCmds = append(f.execCmds, command)
	f.execSessions = append(f.execSessions, name)
	if f.execErr != nil {
		return FleetExecResult{}, f.execErr
	}
	return f.execRes, nil
}

func (f *fakeFleet) DeleteSession(name string) error {
	f.deleted = append(f.deleted, name)
	return nil
}

// remoteRunners is a fleet listing with one local and two remote nodes.
func remoteRunners() []FleetRunner {
	return []FleetRunner{
		{Name: "Local", Host: localRunnerHost, Capabilities: map[string]bool{"docker": true}},
		{Name: "m3-64", Host: "10.0.0.1", Capabilities: map[string]bool{"docker": true}, InFlight: 3, MaxConcurrent: 15},
		{Name: "m1-16", Host: "10.0.0.2", Capabilities: map[string]bool{"docker": true}, InFlight: 1, MaxConcurrent: 5},
	}
}

// --- oracle stand-ins --------------------------------------------------

// oracleCheck builds a one-check catalog standing in for the local
// GITHUB_TOKEN check, so tests drive the local half of the differential
// without reaching GitHub.
func oracleCheck(status Status) []Check {
	return []Check{NewCheck("github token", CatGitHub, func(_ *Profile) Result {
		switch status {
		case StatusOK:
			return ok("GitHub accepted the token")
		case StatusFail:
			return fail("GitHub rejected GITHUB_TOKEN", "regenerate the PAT")
		default:
			return skip("GITHUB_TOKEN not set")
		}
	})}
}

// execOutput builds an exec result carrying probe output.
func execOutput(out string) FleetExecResult {
	return FleetExecResult{Success: true, ExitCode: 0, Output: out}
}

// resultFor picks one credential's row out of a report.
func resultFor(t *testing.T, r FleetReport, credential string) FleetResult {
	t.Helper()
	for _, res := range r.Results {
		if res.Credential == credential {
			return res
		}
	}
	t.Fatalf("report has no row for %q", credential)
	return FleetResult{}
}

func fleetProfile(t *testing.T) *Profile {
	t.Helper()
	return load(t, fullProfile(t, "A=1\n"), newStub(t, nil))
}

// --- the classification matrix -----------------------------------------

// The whole point of the mode: local x remote -> verdict. A local pass with a
// remote failure must read as broken DELIVERY, never as a bad credential.
func TestRunFleetChecks_ClassificationMatrix(t *testing.T) {
	cases := []struct {
		name        string
		local       Status
		exec        FleetExecResult
		execErr     error
		wantFleet   FleetStatus
		wantVerdict Verdict
		wantFix     string
	}{
		{
			name:  "local ok + remote 200 -> pass",
			local: StatusOK, exec: execOutput(probeHTTP + "200"),
			wantFleet: FleetAccepted, wantVerdict: VerdictPass,
		},
		{
			name:  "local ok + remote 401 -> delivery broken (stale value)",
			local: StatusOK, exec: execOutput(probeHTTP + "401"),
			wantFleet: FleetRejected, wantVerdict: VerdictDeliveryBroken,
			wantFix: "re-curate",
		},
		{
			name:  "local ok + token unset in container -> delivery broken (not delivered)",
			local: StatusOK, exec: execOutput(probeUnset),
			wantFleet: FleetUnset, wantVerdict: VerdictDeliveryBroken,
			wantFix: "add GITHUB_TOKEN",
		},
		{
			name:  "local ok + exec error -> inconclusive",
			local: StatusOK, execErr: fmt.Errorf("runner unreachable"),
			wantFleet: FleetUnknown, wantVerdict: VerdictInconclusive,
		},
		{
			name:  "local ok + container cannot reach GitHub -> inconclusive",
			local: StatusOK, exec: execOutput(probeHTTP + "000"),
			wantFleet: FleetUnknown, wantVerdict: VerdictInconclusive,
		},
		{
			name:  "local ok + remote 403 -> inconclusive, not a verdict",
			local: StatusOK, exec: execOutput(probeHTTP + "403"),
			wantFleet: FleetUnknown, wantVerdict: VerdictInconclusive,
		},
		{
			name:  "local fail -> fix locally first, no remote verdict",
			local: StatusFail, exec: execOutput(probeHTTP + "200"),
			wantFleet: FleetNotRun, wantVerdict: VerdictLocalFirst,
			wantFix: "regenerate the PAT",
		},
		{
			name:  "local skip -> inconclusive, no remote verdict",
			local: StatusSkip, exec: execOutput(probeHTTP + "200"),
			wantFleet: FleetNotRun, wantVerdict: VerdictInconclusive,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fake := &fakeFleet{runners: remoteRunners(), execRes: tc.exec, execErr: tc.execErr}
			report := RunFleetChecks(fleetProfile(t), fake, "m3-64", oracleCheck(tc.local))

			// The catalog narrows to the GITHUB_TOKEN oracle, so every
			// other credential short-circuits on a missing check. This test
			// is about the GITHUB_TOKEN row.
			got := resultFor(t, report, "GITHUB_TOKEN")
			if got.Local != tc.local {
				t.Errorf("local = %v, want %v", got.Local, tc.local)
			}
			if got.Fleet != tc.wantFleet {
				t.Errorf("fleet = %v, want %v", got.Fleet, tc.wantFleet)
			}
			if got.Verdict != tc.wantVerdict {
				t.Errorf("verdict = %v, want %v (detail: %s)", got.Verdict, tc.wantVerdict, got.Detail)
			}
			if tc.wantFix != "" && !strings.Contains(got.Fix, tc.wantFix) {
				t.Errorf("fix = %q, want it to mention %q", got.Fix, tc.wantFix)
			}
			if got.Detail == "" {
				t.Error("every row must explain itself")
			}
			// A failing verdict without a fix leaves the user stuck.
			if got.NeedsAction() && got.Fix == "" {
				t.Errorf("failing verdict %v carries no fix hint", got.Verdict)
			}
		})
	}
}

// Only a broken DELIVERY fails the run. An inconclusive probe follows the skip
// rule, and a local-credential fault is a different axis entirely: the remote
// probe never ran, so the run measured nothing about delivery.
func TestFleetReport_OnlyDeliveryBrokenFailsTheRun(t *testing.T) {
	r := FleetReport{Results: []FleetResult{
		{Verdict: VerdictPass}, {Verdict: VerdictInconclusive},
	}}
	if r.Failed() {
		t.Errorf("pass + inconclusive must not fail a run")
	}

	r.Results = append(r.Results, FleetResult{Verdict: VerdictDeliveryBroken})
	if !r.Failed() || r.DeliveryFailCount() != 1 {
		t.Errorf("Failed=%v DeliveryFailCount=%d, want true/1", r.Failed(), r.DeliveryFailCount())
	}
}

// The live run that motivated this: AWS and Azure came back fix-locally-first
// (expired local creds, correctly never probed remotely) and were counted as
// delivery failures, so --fleet exited non-zero for a fault it never measured.
func TestFleetReport_LocalFirstIsNotADeliveryFailure(t *testing.T) {
	mixed := FleetReport{Results: []FleetResult{
		{Credential: "CL_AGENTS_API_TOKEN", Verdict: VerdictDeliveryBroken},
		{Credential: "AWS credentials", Verdict: VerdictLocalFirst},
		{Credential: "Azure credentials", Verdict: VerdictLocalFirst},
		{Credential: "GITHUB_TOKEN", Verdict: VerdictInconclusive},
	}}
	if got := mixed.DeliveryFailCount(); got != 1 {
		t.Errorf("DeliveryFailCount = %d, want 1 (only the delivery-broken row)", got)
	}
	if got := mixed.LocalFirstCount(); got != 2 {
		t.Errorf("LocalFirstCount = %d, want 2", got)
	}
	if !mixed.Failed() {
		t.Error("a delivery-broken row must fail the run")
	}

	// Drop the one real delivery failure: nothing is left that --fleet
	// measured, so the run must exit zero.
	localOnly := FleetReport{Results: mixed.Results[1:]}
	if localOnly.Failed() || localOnly.DeliveryFailCount() != 0 {
		t.Errorf("local-first + inconclusive must exit zero, got Failed=%v count=%d",
			localOnly.Failed(), localOnly.DeliveryFailCount())
	}
	if localOnly.LocalFirstCount() != 2 {
		t.Errorf("LocalFirstCount = %d, want 2", localOnly.LocalFirstCount())
	}

	// Both rows still need a fix hint rendered — not failing the run is not
	// the same as saying nothing.
	for _, res := range []FleetResult{
		{Verdict: VerdictDeliveryBroken}, {Verdict: VerdictLocalFirst},
	} {
		if !res.NeedsAction() {
			t.Errorf("verdict %v must still be reported as needing action", res.Verdict)
		}
	}
	if (FleetResult{Verdict: VerdictLocalFirst}).DeliveryFailed() {
		t.Error("fix-locally-first is not a delivery failure")
	}
	if (FleetResult{Verdict: VerdictInconclusive}).NeedsAction() {
		t.Error("inconclusive follows the skip rule")
	}
}

// --- teardown ----------------------------------------------------------

// A leaked session holds a runner slot and a live copy of the profile's
// credentials on a remote node. Teardown must fire on every path.
func TestRunFleetChecks_AlwaysTearsDown(t *testing.T) {
	cases := []struct {
		name string
		fake *fakeFleet
	}{
		{"exec succeeded", &fakeFleet{execRes: execOutput(probeHTTP + "200")}},
		{"exec returned an error", &fakeFleet{execErr: fmt.Errorf("session.exec failed")}},
		{"probe output was garbage", &fakeFleet{execRes: execOutput("bash: curl: not found")}},
		{"create failed", &fakeFleet{createErr: fmt.Errorf("runner saturated")}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			tc.fake.runners = remoteRunners()
			RunFleetChecks(fleetProfile(t), tc.fake, "m3-64", oracleCheck(StatusOK))

			if len(tc.fake.deleted) != 1 {
				t.Fatalf("delete-session fired %d times, want exactly 1", len(tc.fake.deleted))
			}
			if !strings.HasPrefix(tc.fake.deleted[0], ephemeralSessionNamePrefix) {
				t.Errorf("deleted %q, want the ephemeral session", tc.fake.deleted[0])
			}
			// The session torn down must be the one that was created.
			if len(tc.fake.created) == 1 && tc.fake.created[0].Name != tc.fake.deleted[0] {
				t.Errorf("deleted %q but created %q", tc.fake.deleted[0], tc.fake.created[0].Name)
			}
		})
	}
}

// --- short-circuit -----------------------------------------------------

// Standing up a container to confirm that an already-broken credential is
// broken costs a node slot and tells the user nothing.
func TestRunFleetChecks_ShortCircuitsOnLocalFailure(t *testing.T) {
	for _, local := range []Status{StatusFail, StatusSkip} {
		t.Run(string(local), func(t *testing.T) {
			fake := &fakeFleet{runners: remoteRunners(), execRes: execOutput(probeHTTP + "200")}
			report := RunFleetChecks(fleetProfile(t), fake, "m3-64", oracleCheck(local))

			if len(fake.created) != 0 {
				t.Errorf("created %d session(s); a failing oracle must create none", len(fake.created))
			}
			if len(fake.execCmds) != 0 {
				t.Errorf("ran %d probe(s); a failing oracle must run none", len(fake.execCmds))
			}
			if len(fake.deleted) != 0 {
				t.Errorf("deleted %d session(s); none were created", len(fake.deleted))
			}
			if got := report.Results[0].Detail; !strings.Contains(got, "fix locally first") {
				t.Errorf("detail = %q, want it to say to fix locally first", got)
			}
			// The row must name which local status caused the short-circuit.
			if !strings.Contains(report.Results[0].Detail, string(local)) {
				t.Errorf("detail = %q, want it to name the local status %q",
					report.Results[0].Detail, local)
			}
		})
	}
}

// The session must be created FOR the profile under test — that is what makes
// the broker deliver that profile's credentials into the container.
func TestRunFleetChecks_SessionIsScopedToProfileAndRunner(t *testing.T) {
	fake := &fakeFleet{runners: remoteRunners(), execRes: execOutput(probeHTTP + "200")}
	p := fleetProfile(t)
	RunFleetChecks(p, fake, "m1-16", oracleCheck(StatusOK))

	if len(fake.created) != 1 {
		t.Fatalf("created %d sessions, want 1", len(fake.created))
	}
	spec := fake.created[0]
	if spec.Profile != p.Name {
		t.Errorf("session profile = %q, want %q", spec.Profile, p.Name)
	}
	if spec.Runner != "m1-16" {
		t.Errorf("session runner = %q, want m1-16", spec.Runner)
	}
	if !strings.HasPrefix(spec.Name, ephemeralSessionNamePrefix) {
		t.Errorf("session name = %q, want the %s prefix", spec.Name, ephemeralSessionNamePrefix)
	}
}

// Two runs must not collide on a session name — a collision would delete the
// other run's session out from under it.
func TestEphemeralSessionName_IsUnique(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 50; i++ {
		name, err := ephemeralSessionName()
		if err != nil {
			t.Fatalf("ephemeralSessionName() error: %v", err)
		}
		if seen[name] {
			t.Fatalf("duplicate session name %q", name)
		}
		seen[name] = true
		// Docker naming: <=64 chars, starts alphanumeric.
		if len(name) > 64 || name[0] < 'a' || name[0] > 'z' {
			t.Fatalf("name %q is not a valid session name", name)
		}
	}
}

// --- the probe ---------------------------------------------------------

// The probe must reference the DELIVERED env var and never carry a literal
// token — not in the command string, and not in any argv inside the container.
func TestGitHubTokenProbe_ShapeAndSecrecy(t *testing.T) {
	const secret = "ghp_averysecrettokenvalue"
	cmd := githubTokenProbe("https://api.github.com")

	if strings.Contains(cmd, secret) {
		t.Fatal("probe must never embed a token value")
	}
	if !strings.Contains(cmd, "$GITHUB_TOKEN") && !strings.Contains(cmd, "${GITHUB_TOKEN}") {
		t.Error("probe must read the DELIVERED GITHUB_TOKEN from the container env")
	}

	// printf is a shell builtin, so the token forks no process and never
	// reaches `ps`.
	if !strings.Contains(cmd, "printf 'Authorization: Bearer %s'") {
		t.Error("probe must build the header with the printf builtin")
	}
	// curl reads the header from stdin, so it is not in curl's argv either.
	if !strings.Contains(cmd, "-H @-") {
		t.Error("probe must pass the header to curl via -H @- (stdin), not argv")
	}
	// The token must not appear anywhere on curl's command line.
	curlIdx := strings.Index(cmd, "curl ")
	if curlIdx < 0 {
		t.Fatal("probe does not invoke curl")
	}
	if strings.Contains(cmd[curlIdx:], "GITHUB_TOKEN") {
		t.Error("GITHUB_TOKEN must not appear in curl's argv")
	}

	// The unset case is distinguished before any request is made: an absent
	// token would otherwise degrade into an unauthenticated 401 and be
	// misreported as a rejected token.
	if !strings.Contains(cmd, `[ -z "${GITHUB_TOKEN}"`) {
		t.Error("probe must test for an unset token first")
	}
	if !strings.Contains(cmd, "/rate_limit") {
		t.Error("probe must hit /rate_limit, matching the local oracle")
	}
}

// The probe's actual command is what reaches the container — assert on that,
// not only on the builder.
func TestRunFleetChecks_ExecutedCommandCarriesNoToken(t *testing.T) {
	fake := &fakeFleet{runners: remoteRunners(), execRes: execOutput(probeHTTP + "200")}
	dir := fullProfile(t, "A=1\n")
	writeFile(t, dir, ".env.secrets", "GITHUB_TOKEN=ghp_thisvaluemustneverleak\n")
	p := load(t, dir, newStub(t, nil))

	RunFleetChecks(p, fake, "m3-64", oracleCheck(StatusOK))

	if len(fake.execCmds) != 1 {
		t.Fatalf("ran %d probes, want 1", len(fake.execCmds))
	}
	if strings.Contains(fake.execCmds[0], "ghp_thisvaluemustneverleak") {
		t.Fatal("the token value reached the container command line")
	}
}

// Container output is external data. Anything unrecognised is inconclusive —
// a fabricated verdict is worse than no verdict.
func TestParseGitHubProbe_UntrustedOutput(t *testing.T) {
	cases := []struct {
		name string
		res  FleetExecResult
		want FleetStatus
	}{
		{"marker after a shell banner", execOutput("Welcome to the container\n" + probeHTTP + "200"), FleetAccepted},
		{"unset wins over any code", execOutput(probeUnset), FleetUnset},
		{"no marker, clean exit", execOutput("nothing useful"), FleetUnknown},
		{"no marker, failed exit", FleetExecResult{ExitCode: 127, Output: "curl: not found"}, FleetUnknown},
		{"truncated code", execOutput(probeHTTP), FleetUnknown},
		{"trailing junk after the code", execOutput(probeHTTP + "200\nbye"), FleetAccepted},
		{"empty output", execOutput(""), FleetUnknown},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, detail := parseGitHubProbe(tc.res)
			if got != tc.want {
				t.Errorf("status = %v, want %v", got, tc.want)
			}
			if detail == "" {
				t.Error("every parse outcome must explain itself")
			}
		})
	}
}

// --- runner selection --------------------------------------------------

func TestSelectRunner(t *testing.T) {
	fake := &fakeFleet{runners: remoteRunners()}

	// Auto-select takes the least-loaded remote node.
	got, err := SelectRunner(fake, "")
	if err != nil {
		t.Fatalf("SelectRunner() error: %v", err)
	}
	if got != "m1-16" {
		t.Errorf("auto-selected %q, want the least-loaded remote runner m1-16", got)
	}

	// A named runner is honoured.
	if got, err := SelectRunner(fake, "m3-64"); err != nil || got != "m3-64" {
		t.Errorf("SelectRunner(m3-64) = %q, %v", got, err)
	}

	// The local runner would exercise this machine's own environment and
	// prove nothing about delivery.
	if _, err := SelectRunner(fake, "Local"); err == nil {
		t.Error("the local runner must be rejected for a delivery check")
	}

	if _, err := SelectRunner(fake, "ghost"); err == nil {
		t.Error("an unregistered runner must be an error")
	}
}

func TestSelectRunner_NoRemoteRunner(t *testing.T) {
	localOnly := &fakeFleet{runners: []FleetRunner{
		{Name: "Local", Host: localRunnerHost, Capabilities: map[string]bool{"docker": true}},
	}}
	if _, err := SelectRunner(localOnly, ""); err == nil {
		t.Error("a local-only fleet must not yield a runner")
	}

	// A saturated or non-docker node is not a candidate either.
	unusable := &fakeFleet{runners: []FleetRunner{
		{Name: "full", Host: "10.0.0.3", Capabilities: map[string]bool{"docker": true}, InFlight: 5, MaxConcurrent: 5},
		{Name: "vm-only", Host: "10.0.0.4", Capabilities: map[string]bool{"utm": true}},
	}}
	if _, err := SelectRunner(unusable, ""); err == nil {
		t.Error("saturated and non-session-capable runners must not be selected")
	}

	if _, err := SelectRunner(&fakeFleet{listErr: fmt.Errorf("router offline")}, ""); err == nil {
		t.Error("a failed runner listing must be an error")
	}
}

// --- coverage honesty --------------------------------------------------

// A clean fleet run proves the probed credentials are delivered. Letting it
// read as FULL coverage would be the more dangerous outcome, so the note must
// name exactly what was probed and what was not.
func TestRemoteProbes_CoverageIsStatedHonestly(t *testing.T) {
	probes := remoteProbes()

	want := []string{
		"GITHUB_TOKEN",
		"CL_BRAIN_API_TOKEN", "CL_TODO_API_TOKEN", "CL_SKILLS_API_TOKEN", "CL_AGENTS_API_TOKEN",
		"AWS credentials", "Azure credentials",
	}
	got := map[string]bool{}
	for _, pr := range probes {
		if got[pr.credential] {
			t.Errorf("credential %q is probed twice", pr.credential)
		}
		got[pr.credential] = true
	}
	for _, c := range want {
		if !got[c] {
			t.Errorf("remoteProbes() is missing %q", c)
		}
		// The cloud rows are named "AWS credentials"/"Azure credentials";
		// the note names the provider. Match on the leading word.
		if name := strings.Fields(c)[0]; !strings.Contains(FleetCoverageNote, name) {
			t.Errorf("the coverage note does not name %q", name)
		}
	}
	if len(probes) != len(want) {
		t.Errorf("got %d probes, want %d", len(probes), len(want))
	}

	// gcloud is not in the container image; a probe would report "not
	// delivered" for every profile and say nothing about delivery.
	if !strings.Contains(FleetCoverageNote, "gcloud is excluded") {
		t.Error("the coverage note must state the gcloud exclusion")
	}
	if !strings.Contains(FleetCoverageNote, "CL_API_KEY is") ||
		!strings.Contains(FleetCoverageNote, "excluded") {
		t.Errorf("the coverage note must state the CL_API_KEY exclusion: %q", FleetCoverageNote)
	}
	for _, pr := range probes {
		if strings.Contains(strings.ToLower(pr.credential), "gcloud") {
			t.Error("gcloud is deferred and must not be probed")
		}
	}

	// Every entry must be wired end to end — a half-registered probe would
	// panic at run time rather than report.
	for _, pr := range probes {
		if pr.localCheck == "" || pr.buildProbe == nil || pr.classify == nil || pr.deliveryFix == nil {
			t.Errorf("probe %q is incompletely wired", pr.credential)
		}
	}
}

// Every localCheck must name a check that actually exists, or the credential
// silently degrades to "no baseline" and is never probed at all.
func TestRemoteProbes_LocalChecksExistInTheCatalog(t *testing.T) {
	names := map[string]bool{}
	for _, c := range DefaultChecks() {
		names[c.Name()] = true
	}
	for _, pr := range remoteProbes() {
		if !names[pr.localCheck] {
			t.Errorf("probe %q names local check %q, which is not in the catalog",
				pr.credential, pr.localCheck)
		}
	}
}

// A renamed local check must degrade to "no baseline", never to a false
// delivery verdict.
func TestRunNamedCheck_MissingCheckSkips(t *testing.T) {
	got := runNamedCheck(fleetProfile(t), "no such check", []Check{})
	if got.Status != StatusSkip {
		t.Errorf("status = %v, want skip", got.Status)
	}
}

// --- redaction ---------------------------------------------------------

// Last line of defence: even if a container echoed the token back at us, it
// must not reach the terminal, a log, or the JSON output.
func TestRunFleetChecks_RedactsEchoedToken(t *testing.T) {
	const token = "ghp_containerechoedthisback"
	dir := fullProfile(t, "A=1\n")
	writeFile(t, dir, ".env.secrets", "GITHUB_TOKEN="+token+"\n")
	p := load(t, dir, newStub(t, nil))

	// A hostile/buggy container that echoes the token instead of a verdict.
	fake := &fakeFleet{
		runners: remoteRunners(),
		execErr: fmt.Errorf("session.exec failed for token %s", token),
	}
	report := RunFleetChecks(p, fake, "m3-64", oracleCheck(StatusOK))

	for _, res := range report.Results {
		if strings.Contains(res.Detail, token) || strings.Contains(res.Fix, token) {
			t.Fatalf("token leaked into the report: %+v", res)
		}
	}
}

// --- multi-credential coverage -----------------------------------------

// allOracles builds a catalog in which every credential's local oracle
// returns the given status, so the batching behaviour can be driven without
// reaching any real service.
func allOracles(status Status) []Check {
	var checks []Check
	for _, pr := range remoteProbes() {
		name := pr.localCheck
		checks = append(checks, NewCheck(name, "test", func(_ *Profile) Result {
			switch status {
			case StatusOK:
				return ok(name + " is valid")
			case StatusFail:
				return fail(name+" is broken", "fix "+name)
			default:
				return skip(name + " is not configured")
			}
		}))
	}
	return checks
}

// The efficiency contract. A session per credential would pay the provisioning
// cost — up to a five-minute create timeout — once per row, for a container
// that is identical every time: it is the PROFILE that decides what is
// delivered into it, not the credential being tested.
func TestRunFleetChecks_BatchesEveryProbeIntoOneSession(t *testing.T) {
	fake := &fakeFleet{runners: remoteRunners(), execRes: execOutput(probeOK)}
	report := RunFleetChecks(fleetProfile(t), fake, "m3-64", allOracles(StatusOK))

	wantProbes := len(remoteProbes())
	if len(fake.created) != 1 {
		t.Fatalf("created %d sessions, want exactly 1 for %d credentials", len(fake.created), wantProbes)
	}
	if len(fake.deleted) != 1 {
		t.Fatalf("deleted %d sessions, want exactly 1", len(fake.deleted))
	}
	if fake.created[0].Name != fake.deleted[0] {
		t.Errorf("deleted %q but created %q", fake.deleted[0], fake.created[0].Name)
	}
	if len(fake.execCmds) != wantProbes {
		t.Errorf("ran %d probes, want one per credential (%d)", len(fake.execCmds), wantProbes)
	}
	if len(report.Results) != wantProbes {
		t.Errorf("got %d rows, want one per credential (%d)", len(report.Results), wantProbes)
	}
	// Every probe ran in the ONE session that was created.
	for _, name := range fake.execSessions {
		if name != fake.created[0].Name {
			t.Errorf("probe ran in session %q, want %q", name, fake.created[0].Name)
		}
	}
}

// Teardown is unconditional. A leaked session holds a runner slot and a live
// copy of the profile's credentials on a remote node — with a batch, one
// failing probe must not strand the container for all the others.
func TestRunFleetChecks_BatchTearsDownOnce(t *testing.T) {
	cases := []struct {
		name string
		fake *fakeFleet
	}{
		{"every probe succeeded", &fakeFleet{execRes: execOutput(probeOK)}},
		{"exec returned an error", &fakeFleet{execErr: fmt.Errorf("session.exec failed")}},
		{"probe output was garbage", &fakeFleet{execRes: execOutput("bash: pbrainctl: not found")}},
		{"create failed", &fakeFleet{createErr: fmt.Errorf("runner saturated")}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			tc.fake.runners = remoteRunners()
			report := RunFleetChecks(fleetProfile(t), tc.fake, "m3-64", allOracles(StatusOK))

			if len(tc.fake.deleted) != 1 {
				t.Fatalf("delete-session fired %d times, want exactly 1", len(tc.fake.deleted))
			}
			if len(tc.fake.created) > 1 {
				t.Errorf("created %d sessions, want at most 1", len(tc.fake.created))
			}
			// Even a session that never came up must leave every credential
			// with an explained row rather than an empty verdict.
			for _, res := range report.Results {
				if res.Verdict == "" || res.Detail == "" {
					t.Errorf("row %q has no verdict/detail: %+v", res.Credential, res)
				}
			}
		})
	}
}

// Zero locally-passing credentials must cost nothing: no node slot, no
// container holding a copy of the profile's credentials.
func TestRunFleetChecks_NoSessionWhenNothingPassesLocally(t *testing.T) {
	for _, local := range []Status{StatusFail, StatusSkip} {
		t.Run(string(local), func(t *testing.T) {
			fake := &fakeFleet{runners: remoteRunners(), execRes: execOutput(probeOK)}
			report := RunFleetChecks(fleetProfile(t), fake, "m3-64", allOracles(local))

			if len(fake.created) != 0 || len(fake.execCmds) != 0 || len(fake.deleted) != 0 {
				t.Errorf("created=%d execs=%d deleted=%d, want all zero",
					len(fake.created), len(fake.execCmds), len(fake.deleted))
			}
			wantVerdict := VerdictLocalFirst
			if local == StatusSkip {
				wantVerdict = VerdictInconclusive
			}
			for _, res := range report.Results {
				if res.Fleet != FleetNotRun {
					t.Errorf("%s: fleet = %v, want not-run", res.Credential, res.Fleet)
				}
				if res.Verdict != wantVerdict {
					t.Errorf("%s: verdict = %v, want %v", res.Credential, res.Verdict, wantVerdict)
				}
			}
		})
	}
}

// The short-circuit is PER CREDENTIAL: a broken local credential must not
// suppress the delivery verdict for the ones that are fine.
func TestRunFleetChecks_ShortCircuitIsPerCredential(t *testing.T) {
	checks := []Check{
		NewCheck("github token", CatGitHub, func(_ *Profile) Result {
			return fail("GitHub rejected GITHUB_TOKEN", "regenerate the PAT")
		}),
		NewCheck("brain token (memory)", CatBrain, func(_ *Profile) Result {
			return ok("memory vault reachable")
		}),
	}
	fake := &fakeFleet{runners: remoteRunners(), execRes: execOutput(probeOK)}
	report := RunFleetChecks(fleetProfile(t), fake, "m3-64", checks)

	gh := resultFor(t, report, "GITHUB_TOKEN")
	if gh.Verdict != VerdictLocalFirst || gh.Fleet != FleetNotRun {
		t.Errorf("GITHUB_TOKEN = %v/%v, want fix-locally-first/not-run", gh.Verdict, gh.Fleet)
	}

	brain := resultFor(t, report, "CL_BRAIN_API_TOKEN")
	if brain.Verdict != VerdictPass {
		t.Errorf("CL_BRAIN_API_TOKEN verdict = %v, want pass (detail: %s)", brain.Verdict, brain.Detail)
	}

	// One passing oracle still means exactly one session, and only its probe.
	if len(fake.created) != 1 || len(fake.execCmds) != 1 {
		t.Errorf("created=%d execs=%d, want 1/1", len(fake.created), len(fake.execCmds))
	}
}

// --- probe shape and secrecy -------------------------------------------

// No probe may carry a credential VALUE. Assert it on the command string that
// actually reaches Exec, with every credential the registry covers present in
// the profile as a distinctive value.
func TestRunFleetChecks_NoProbeCarriesASecretValue(t *testing.T) {
	secrets := map[string]string{
		"GITHUB_TOKEN":        "ghp_leak0",
		"CL_BRAIN_API_TOKEN":  "brainleak1",
		"CL_TODO_API_TOKEN":   "todoleak2",
		"CL_SKILLS_API_TOKEN": "skillsleak3",
		"CL_AGENTS_API_TOKEN": "agentsleak4",
		"CL_API_KEY":          "routerleak5",
	}
	var body strings.Builder
	for k, v := range secrets {
		fmt.Fprintf(&body, "%s=%s\n", k, v)
	}
	dir := fullProfile(t, "A=1\n")
	writeFile(t, dir, ".env.secrets", body.String())
	p := load(t, dir, newStub(t, nil))

	fake := &fakeFleet{runners: remoteRunners(), execRes: execOutput(probeOK)}
	RunFleetChecks(p, fake, "m3-64", allOracles(StatusOK))

	if len(fake.execCmds) == 0 {
		t.Fatal("no probes ran")
	}
	for _, cmd := range fake.execCmds {
		for key, value := range secrets {
			if strings.Contains(cmd, value) {
				t.Fatalf("the value of %s reached the container command line", key)
			}
		}
	}
}

// Each probe must reference the DELIVERED env var, guard the unset case before
// doing any work, and bound itself with a timeout so a container that cannot
// reach a service does not hold the session open.
func TestStatusProbes_ShapeAndSecrecy(t *testing.T) {
	envKeyed := []string{
		"CL_BRAIN_API_TOKEN", "CL_TODO_API_TOKEN", "CL_SKILLS_API_TOKEN", "CL_AGENTS_API_TOKEN",
	}
	byCredential := map[string]remoteProbe{}
	for _, pr := range remoteProbes() {
		byCredential[pr.credential] = pr
	}

	for _, key := range envKeyed {
		t.Run(key, func(t *testing.T) {
			pr, found := byCredential[key]
			if !found {
				t.Fatalf("no probe for %s", key)
			}
			cmd := pr.buildProbe()

			if !strings.Contains(cmd, "${"+key+"}") {
				t.Errorf("probe must read the DELIVERED %s from the container env", key)
			}
			if !strings.Contains(cmd, `[ -z "${`+key+`}" ]`) {
				t.Error("probe must test for an unset credential first")
			}
			if !strings.Contains(cmd, "timeout "+probeTimeout) {
				t.Error("probe must be bounded by a timeout")
			}
			if !strings.Contains(cmd, probeUnset) || !strings.Contains(cmd, probeOK) || !strings.Contains(cmd, probeFail) {
				t.Error("probe must report all three marker outcomes")
			}
			// Output is discarded: a tool's stdout can echo the very token it
			// was handed, and only the marker is ever needed.
			if !strings.Contains(cmd, ">/dev/null 2>&1") {
				t.Error("probe must discard tool output")
			}
		})
	}

	// The brain probe aliases the vault's token to the key pbrainctl reads,
	// exactly as the local oracle does — via an env assignment, never an argv.
	brain := byCredential["CL_TODO_API_TOKEN"].buildProbe()
	if !strings.Contains(brain, `env CL_BRAIN_API_TOKEN="${CL_TODO_API_TOKEN}"`) {
		t.Errorf("brain probe must alias the vault token into CL_BRAIN_API_TOKEN: %s", brain)
	}
	if !strings.Contains(brain, "pbrainctl client recall --limit 1 doctor") {
		t.Error("brain probe must run the same recall the local oracle does")
	}
}

// The cloud probes capture the CLI's failure text so "no credentials at all"
// can be told apart from "credentials present but refused" — the two have
// completely different fixes.
func TestCloudProbes_Shape(t *testing.T) {
	for _, tc := range []struct{ credential, want string }{
		{"AWS credentials", "aws sts get-caller-identity"},
		{"Azure credentials", "az account show"},
	} {
		t.Run(tc.credential, func(t *testing.T) {
			var cmd string
			for _, pr := range remoteProbes() {
				if pr.credential == tc.credential {
					cmd = pr.buildProbe()
				}
			}
			if cmd == "" {
				t.Fatalf("no probe for %s", tc.credential)
			}
			if !strings.Contains(cmd, tc.want) {
				t.Errorf("probe must run %q, got %q", tc.want, cmd)
			}
			if !strings.Contains(cmd, "timeout "+probeTimeout) {
				t.Error("probe must be bounded by a timeout")
			}
			if !strings.Contains(cmd, probeOK) || !strings.Contains(cmd, probeFail) {
				t.Error("probe must report a marker outcome")
			}
			// The CLIs read credentials from env and files; the probe must
			// not attempt to pass any value itself.
			if strings.Contains(cmd, "AWS_SECRET") || strings.Contains(cmd, "--password") {
				t.Error("probe must not handle credential values itself")
			}
		})
	}
}

// --- classification ----------------------------------------------------

func TestClassifyStatusProbe(t *testing.T) {
	cases := []struct {
		name string
		res  FleetExecResult
		want FleetStatus
	}{
		{"ok", execOutput(probeOK), FleetAccepted},
		{"unset", execOutput(probeUnset), FleetUnset},
		{"fail", execOutput(probeFail), FleetRejected},
		{"marker after a shell banner", execOutput("motd\n" + probeOK), FleetAccepted},
		{"unset wins over a later marker", execOutput(probeUnset), FleetUnset},
		{"no marker, clean exit", execOutput("nothing useful"), FleetUnknown},
		{"no marker, failed exit", FleetExecResult{ExitCode: 127, Output: "pbrainctl: not found"}, FleetUnknown},
		{"empty output", execOutput(""), FleetUnknown},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, detail := classifyStatusProbe(tc.res, "CL_TODO_API_TOKEN", "brain daemon (todo vault)")
			if got != tc.want {
				t.Errorf("status = %v, want %v", got, tc.want)
			}
			if detail == "" {
				t.Error("every parse outcome must explain itself")
			}
		})
	}
}

// A cloud CLI that finds no credentials AT ALL is a different finding from one
// that finds credentials and refuses them: the broker delivers env vars, while
// these credentials live locally in files, so "not delivered" is a legitimate
// statement about the platform, not a broken probe.
func TestClassifyCloudProbe(t *testing.T) {
	cases := []struct {
		name         string
		res          FleetExecResult
		notDelivered []string
		want         FleetStatus
	}{
		{
			name: "aws ok", res: execOutput(probeOK),
			notDelivered: awsNotDelivered, want: FleetAccepted,
		},
		{
			name:         "aws has no credentials at all -> not delivered",
			res:          execOutput(probeFail + ":Unable to locate credentials. You can configure credentials by running \"aws configure\"."),
			notDelivered: awsNotDelivered, want: FleetUnset,
		},
		{
			name:         "aws expired token -> rejected",
			res:          execOutput(probeFail + ":An error occurred (ExpiredToken) when calling the GetCallerIdentity operation"),
			notDelivered: awsNotDelivered, want: FleetRejected,
		},
		{
			name: "azure ok", res: execOutput(probeOK),
			notDelivered: azureNotDelivered, want: FleetAccepted,
		},
		{
			name:         "azure not logged in -> not delivered",
			res:          execOutput(probeFail + ":ERROR: Please run 'az login' to setup account."),
			notDelivered: azureNotDelivered, want: FleetUnset,
		},
		{
			name:         "azure subscription error -> rejected",
			res:          execOutput(probeFail + ":ERROR: The subscription was not found"),
			notDelivered: azureNotDelivered, want: FleetRejected,
		},
		{
			name:         "no marker, failed exit -> inconclusive",
			res:          FleetExecResult{ExitCode: 127, Output: "az: not found"},
			notDelivered: azureNotDelivered, want: FleetUnknown,
		},
		{
			name:         "no marker, clean exit -> inconclusive",
			res:          execOutput("banner only"),
			notDelivered: awsNotDelivered, want: FleetUnknown,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, detail := classifyCloudProbe(tc.res, "AWS credentials", "AWS STS", tc.notDelivered)
			if got != tc.want {
				t.Errorf("status = %v, want %v", got, tc.want)
			}
			if detail == "" {
				t.Error("every parse outcome must explain itself")
			}
			// Untrusted container output must never be quoted back verbatim.
			if strings.Contains(detail, "aws configure") || strings.Contains(detail, "ExpiredToken") {
				t.Errorf("detail quotes container output verbatim: %q", detail)
			}
		})
	}
}

// --- not-delivered vs rejected -----------------------------------------

// The two failure modes must read distinctly. "Nothing was ever delivered" and
// "the delivered value is stale" send the operator to different places, and a
// fix hint that conflated them would send them to the wrong one.
func TestDeliveryFix_DistinguishesNotDeliveredFromRejected(t *testing.T) {
	for _, pr := range remoteProbes() {
		t.Run(pr.credential, func(t *testing.T) {
			unset := pr.deliveryFix(FleetUnset)
			rejected := pr.deliveryFix(FleetRejected)

			if unset == "" || rejected == "" {
				t.Fatal("both failure modes need a fix hint")
			}
			if unset == rejected {
				t.Error("not-delivered and rejected must not share a fix hint")
			}
			if !strings.Contains(strings.ToLower(rejected), "stale") {
				t.Errorf("the rejected hint must say the value is stale: %q", rejected)
			}
		})
	}
}

// CL_API_KEY is the OPERATOR hub key. A session container deliberately does
// NOT get it — it authenticates to the hub with a hub-issued, session-scoped
// BRAINBOX_TOKEN — so probing it reported "delivery-broken" for a credential
// whose absence is correct by design. It must not be in the registry at all.
func TestRemoteProbes_ExcludesOperatorHubKey(t *testing.T) {
	for _, pr := range remoteProbes() {
		if pr.credential == RouterAPITokenKey {
			t.Fatalf("%s must not be probed remotely: its absence in a session is by design",
				RouterAPITokenKey)
		}
	}
	if strings.Contains(FleetCoverageNote, "CL_API_KEY (router)") {
		t.Error("the coverage note must no longer claim CL_API_KEY is probed")
	}
}

// The brain vault tokens are provisioned and forwarded by phantom-router's
// brain binding, NOT curated in the profile's gateway env store (which holds a
// couple of keys and never held these). The hint must name the real place.
func TestBrainDeliveryFix_PointsAtTheBrainBinding(t *testing.T) {
	for _, v := range brainVaults {
		t.Run(v.tokenKey, func(t *testing.T) {
			for _, reason := range []FleetStatus{FleetUnset, FleetRejected} {
				fix := brainDeliveryFix(v.tokenKey, v.label)(reason)
				if strings.Contains(fix, "gateway env store") &&
					!strings.Contains(fix, "not the gateway env store") {
					t.Errorf("%s hint must not send the operator to the gateway env store: %q", reason, fix)
				}
				if !strings.Contains(fix, "brain binding") || !strings.Contains(fix, "phantom-router") {
					t.Errorf("%s hint must name the brain binding (phantom-router): %q", reason, fix)
				}
				if !strings.Contains(fix, v.label) {
					t.Errorf("%s hint must name the %s vault: %q", reason, v.label, fix)
				}
			}
		})
	}

	// And the registry actually uses it — the defect was a correct helper
	// wired to the wrong rows.
	for _, pr := range remoteProbes() {
		if pr.credential == "CL_AGENTS_API_TOKEN" {
			if fix := pr.deliveryFix(FleetUnset); !strings.Contains(fix, "brain binding") {
				t.Errorf("the registry row must use the brain-binding hint, got %q", fix)
			}
		}
	}
}

// GITHUB_TOKEN is the one credential the gateway env store really does
// deliver, so its hint keeps pointing there.
func TestGitHubDeliveryFix_KeepsTheGatewayStoreHint(t *testing.T) {
	for _, pr := range remoteProbes() {
		if pr.credential != "GITHUB_TOKEN" {
			continue
		}
		for _, reason := range []FleetStatus{FleetUnset, FleetRejected} {
			if fix := pr.deliveryFix(reason); !strings.Contains(fix, "gateway env store") {
				t.Errorf("%s hint must still name the gateway env store: %q", reason, fix)
			}
		}
		return
	}
	t.Fatal("GITHUB_TOKEN is missing from the probe registry")
}

// Cloud credentials are FILES locally and the broker delivers ENV VARS, so
// nothing is currently wired to ship them. The hint must say that, not send
// the operator looking for a store entry that was never meant to exist.
func TestCloudDeliveryFix_NamesTheRealGap(t *testing.T) {
	for _, tc := range []struct{ credential, dir string }{
		{"AWS credentials", "~/.aws"},
		{"Azure credentials", "~/.azure"},
	} {
		fix := cloudDeliveryFix(tc.credential, tc.dir)(FleetUnset)
		if !strings.Contains(fix, tc.dir) {
			t.Errorf("%s: hint must name %s, got %q", tc.credential, tc.dir, fix)
		}
		if !strings.Contains(fix, "env vars") {
			t.Errorf("%s: hint must contrast env-var delivery with files, got %q", tc.credential, fix)
		}
		if strings.Contains(fix, "add "+tc.credential+" to the profile's gateway env store") {
			t.Errorf("%s: hint must not imply a store entry exists, got %q", tc.credential, fix)
		}
	}
}

// A missing CLI is not a credential verdict: reporting "rejected" would send
// the operator to re-curate a value that was never read.
func TestClassifyCloudProbe_MissingCLIIsInconclusive(t *testing.T) {
	res := execOutput(probeFail + ":/bin/sh: az: command not found")
	got, detail := classifyCloudProbe(res, "Azure credentials", "Azure CLI", azureNotDelivered)
	if got != FleetUnknown {
		t.Errorf("status = %v, want unknown", got)
	}
	if !strings.Contains(detail, "not available in the container image") {
		t.Errorf("detail = %q, want it to name the missing tool", detail)
	}
}
