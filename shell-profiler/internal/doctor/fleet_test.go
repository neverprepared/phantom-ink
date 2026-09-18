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
	created  []FleetSessionSpec
	execCmds []string
	deleted  []string
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

			if len(report.Results) != 1 {
				t.Fatalf("got %d results, want 1 (v1 probes GITHUB_TOKEN only)", len(report.Results))
			}
			got := report.Results[0]
			if got.Credential != "GITHUB_TOKEN" {
				t.Errorf("credential = %q, want GITHUB_TOKEN", got.Credential)
			}
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
			if got.Failed() && got.Fix == "" {
				t.Errorf("failing verdict %v carries no fix hint", got.Verdict)
			}
		})
	}
}

// Only a broken delivery or a broken local credential is the user's to fix.
// An inconclusive probe follows the skip rule and must not fail the run.
func TestFleetReport_FailedIgnoresInconclusive(t *testing.T) {
	r := FleetReport{Results: []FleetResult{
		{Verdict: VerdictPass}, {Verdict: VerdictInconclusive},
	}}
	if r.Failed() {
		t.Errorf("pass + inconclusive must not fail a run")
	}

	r.Results = append(r.Results, FleetResult{Verdict: VerdictDeliveryBroken})
	if !r.Failed() || r.FailCount() != 1 {
		t.Errorf("Failed=%v FailCount=%d, want true/1", r.Failed(), r.FailCount())
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

// A clean fleet run proves ONE credential is delivered. Letting it read as
// full coverage would be the more dangerous outcome.
func TestRemoteProbes_V1IsGitHubTokenOnly(t *testing.T) {
	probes := remoteProbes()
	if len(probes) != 1 || probes[0].credential != "GITHUB_TOKEN" {
		t.Fatalf("v1 must probe GITHUB_TOKEN only, got %d probes", len(probes))
	}
	if !strings.Contains(FleetCoverageNote, "GITHUB_TOKEN") {
		t.Error("the coverage note must name what is actually covered")
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
