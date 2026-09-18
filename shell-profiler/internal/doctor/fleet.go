package doctor

// Fleet credential-delivery checking: the differential half of doctor.
//
// A normal check answers "does this credential work HERE?". That is necessary
// but not sufficient: agents do not run here, they run in containers on fleet
// runners, and the credential they get is whatever the broker's env store
// delivers — a SEPARATE copy that drifts. A local check passing while the
// delivered copy is stale is exactly the failure this catches.
//
// The method is a differential. The local functional check is the ORACLE: it
// establishes that the credential itself is good. Only then is the same
// credential exercised remotely, through the EXISTING session-create →
// runner → container delivery path, by a thin shell probe over session.exec.
// Remote-vs-local is the verdict; a remote failure under a passing local check
// means delivery is broken, not the credential.
//
// Composition only: no new server endpoint, no work-kind, nothing baked into
// the container image. Every fleet operation is one of the three calls the
// FleetClient interface names, all of which already exist.
//
// Secret hygiene carries over from the local checks and gains one rule: the
// probe runs INSIDE the container, so the token must not reach any argv there
// either. See githubTokenProbe for how that is arranged.

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"os"
	"strconv"
	"strings"
)

// --- fleet types -------------------------------------------------------

// FleetRunner is a registered fleet runner, as reported by the orchestration
// API's runner listing.
type FleetRunner struct {
	Name          string          `json:"name"`
	Host          string          `json:"host"`
	Capabilities  map[string]bool `json:"capabilities"`
	InFlight      int             `json:"in_flight"`
	MaxConcurrent int             `json:"max_concurrent"`
}

// localRunnerHost marks the in-process runner. A credential-delivery test must
// land on a real remote node: the local runner shares this machine's
// environment, so it would confirm nothing about the broker's delivery path.
const localRunnerHost = "local-process"

// IsRemote reports whether the runner is a real fleet node rather than the
// in-process one.
func (r FleetRunner) IsRemote() bool {
	return r.Host != "" && r.Host != localRunnerHost
}

// CanHostSession reports whether the runner can host a container session.
func (r FleetRunner) CanHostSession() bool { return r.Capabilities["docker"] }

// HasCapacity reports whether the runner has a free slot. A runner with no
// declared maximum is treated as having capacity — an unknown limit is not
// evidence of saturation.
func (r FleetRunner) HasCapacity() bool {
	return r.MaxConcurrent <= 0 || r.InFlight < r.MaxConcurrent
}

// FleetSessionSpec describes the ephemeral session a delivery probe runs in.
// Profile is the load-bearing field: it is what makes the broker deliver THAT
// profile's credentials into the container.
type FleetSessionSpec struct {
	Name    string
	Runner  string
	Profile string
	Role    string
}

// FleetExecResult is the outcome of one session.exec call.
type FleetExecResult struct {
	Success  bool   `json:"success"`
	ExitCode int    `json:"exit_code"`
	Output   string `json:"output"`
}

// FleetClient is the seam between doctor and the orchestration API. It names
// exactly the three operations a delivery probe needs, so tests substitute a
// fake and no test ever touches a real fleet.
type FleetClient interface {
	// ListRunners returns the registered fleet runners.
	ListRunners() ([]FleetRunner, error)
	// CreateSession creates (and starts) an ephemeral session.
	CreateSession(spec FleetSessionSpec) error
	// Exec runs a shell command inside a session and returns its result.
	Exec(sessionName, command string) (FleetExecResult, error)
	// DeleteSession tears a session down. Called unconditionally, so it must
	// tolerate a session that was never fully created.
	DeleteSession(sessionName string) error
}

// --- verdicts ----------------------------------------------------------

// FleetStatus is what the remote probe observed. It is deliberately NOT a
// Status: "the container's copy of the token was rejected" is a different
// axis from "this check passed", and collapsing them loses the differential.
type FleetStatus string

const (
	// FleetAccepted means the delivered credential was accepted remotely.
	FleetAccepted FleetStatus = "accepted"
	// FleetRejected means the credential arrived but the service refused it.
	FleetRejected FleetStatus = "rejected"
	// FleetUnset means the credential never reached the container at all.
	FleetUnset FleetStatus = "unset"
	// FleetUnknown means the probe could not produce a verdict — the node was
	// unreachable, exec failed, or the container could not reach the service.
	FleetUnknown FleetStatus = "unknown"
	// FleetNotRun means no remote probe was attempted, because the local
	// oracle did not establish that the credential is good.
	FleetNotRun FleetStatus = "not-run"
)

// Verdict is the differential's conclusion.
type Verdict string

const (
	// VerdictPass means local and remote agree the credential works.
	VerdictPass Verdict = "pass"
	// VerdictDeliveryBroken means the credential works locally but the
	// delivered copy does not. This is the finding the whole mode exists for.
	VerdictDeliveryBroken Verdict = "delivery-broken"
	// VerdictLocalFirst means the local oracle failed, so delivery was never
	// tested — there is nothing to diff against a broken local credential.
	VerdictLocalFirst Verdict = "fix-locally-first"
	// VerdictInconclusive means no verdict could be reached. Like a skip, it
	// never fails a run: an offline node is not something the user can fix
	// from here.
	VerdictInconclusive Verdict = "inconclusive"
)

// FleetResult is one credential's row in a fleet report.
type FleetResult struct {
	// Credential is the env key under test (e.g. GITHUB_TOKEN).
	Credential string `json:"credential"`
	// Runner is the node the probe ran on; empty when none was used.
	Runner string `json:"runner,omitempty"`
	// Local is the local oracle's status.
	Local Status `json:"local"`
	// Fleet is what the delivered copy did.
	Fleet FleetStatus `json:"fleet"`
	// Verdict is the differential's conclusion.
	Verdict Verdict `json:"verdict"`
	Detail  string  `json:"detail,omitempty"`
	// Fix is a short, actionable hint. Only meaningful for a failing verdict.
	Fix string `json:"fix,omitempty"`
}

// Failed reports whether this row is something the user must act on. Only a
// broken delivery and a broken local credential qualify; an inconclusive probe
// follows the same rule as a skipped check and never fails a run.
func (r FleetResult) Failed() bool {
	return r.Verdict == VerdictDeliveryBroken || r.Verdict == VerdictLocalFirst
}

// FleetReport is the outcome of a whole --fleet run.
type FleetReport struct {
	Profile string `json:"profile"`
	// Runner is the node the probes ran on; empty when none was selected.
	Runner  string        `json:"runner,omitempty"`
	Results []FleetResult `json:"results"`
}

// Failed reports whether any row needs action.
func (r FleetReport) Failed() bool { return r.FailCount() > 0 }

// FailCount totals the rows needing action.
func (r FleetReport) FailCount() int {
	n := 0
	for _, res := range r.Results {
		if res.Failed() {
			n++
		}
	}
	return n
}

// --- the probe ---------------------------------------------------------

// probeMarker prefixes the probe's verdict line so it can be parsed out of
// container output deterministically. Shell profiles and container banners can
// prepend arbitrary text to exec output; scanning for this marker is robust
// where "read the last line" is not.
const probeMarker = "SPFLEET:"

// Probe verdict tokens, emitted by the probes and parsed by the classifiers.
//
// probeHTTP carries curl's status code and belongs to the GitHub probe alone.
// The other probes exercise a CLI that already knows whether the credential
// worked, so they report the verdict directly: probeOK / probeFail, with the
// same probeUnset case carved out first for the same reason.
const (
	probeUnset = probeMarker + "UNSET"
	probeHTTP  = probeMarker + "HTTP:"
	probeOK    = probeMarker + "OK"
	probeFail  = probeMarker + "FAIL"
)

// probeTimeout bounds every in-container probe. A container that cannot reach
// a service must produce an inconclusive verdict promptly rather than hold the
// ephemeral session — and with it a live copy of the credentials — open.
const probeTimeout = "20"

// githubTokenProbe is the shell one-liner run inside the container to exercise
// the DELIVERED GITHUB_TOKEN.
//
// The token never appears in any argv inside the container, which is the whole
// reason this is not the obvious `curl -H "Authorization: Bearer $GITHUB_TOKEN"`:
//
//   - printf is a shell BUILTIN, so expanding the token into its output forks
//     no process and puts nothing in `ps`.
//   - curl reads the header from stdin via `-H @-`, so the token is not in
//     curl's argv either.
//
// The unset case is tested FIRST and reported distinctly. Without it an absent
// token degrades into an unauthenticated request and reads as a rejection —
// which would point the user at rotating a token when the real fault is that
// nothing was delivered. Those are different bugs with different fixes.
//
// /rate_limit matches the local oracle deliberately: it returns 200 for ANY
// valid credential (classic and fine-grained PATs, and App installation
// tokens) where /user 403s for installation tokens. Probing a different
// endpoint than the oracle would make the two sides non-comparable, and the
// comparison is the entire point.
func githubTokenProbe(apiBase string) string {
	return "if [ -z \"${GITHUB_TOKEN}\" ]; then printf '" + probeUnset + "'; else " +
		"printf 'Authorization: Bearer %s' \"${GITHUB_TOKEN}\" | " +
		"curl -sS --max-time 15 -o /dev/null -w '" + probeHTTP + "%{http_code}' " +
		"-H @- " + apiBase + "/rate_limit; fi"
}

// parseGitHubProbe turns probe output into a fleet status.
//
// Container output is external data: it may carry banners, warnings on stderr,
// or nothing at all. Only the marker is trusted, and anything unrecognised is
// FleetUnknown rather than a guess — an inconclusive probe is honest, a
// fabricated verdict is not.
func parseGitHubProbe(res FleetExecResult) (FleetStatus, string) {
	out := res.Output
	if strings.Contains(out, probeUnset) {
		return FleetUnset, "GITHUB_TOKEN is not set inside the container"
	}

	idx := strings.Index(out, probeHTTP)
	if idx < 0 {
		// No marker: the probe did not run to completion. curl missing from
		// the image lands here, as does a container that died mid-exec.
		if !res.Success {
			return FleetUnknown, fmt.Sprintf("probe did not complete (exit %d)", res.ExitCode)
		}
		return FleetUnknown, "probe produced no recognisable result"
	}

	code := parseProbeCode(out[idx+len(probeHTTP):])
	switch code {
	case 200:
		return FleetAccepted, "GitHub accepted the delivered token"
	case 401:
		return FleetRejected, "GitHub rejected the delivered token (401 Bad credentials)"
	case 0:
		// curl's %{http_code} is 000 when no response was received. That is
		// the container's network, not the credential.
		return FleetUnknown, "the container could not reach GitHub"
	default:
		// 403 (rate-limited or blocked) and 5xx are not a clean verdict.
		return FleetUnknown, fmt.Sprintf("inconclusive (GitHub returned %d from the container)", code)
	}
}

// parseProbeCode reads the leading digits of curl's %{http_code} expansion.
// It stops at the first non-digit so trailing container output cannot corrupt
// the code, and returns 0 for anything unparseable.
func parseProbeCode(s string) int {
	end := 0
	for end < len(s) && s[end] >= '0' && s[end] <= '9' {
		end++
	}
	if end == 0 {
		return 0
	}
	code, err := strconv.Atoi(s[:end])
	if err != nil {
		return 0
	}
	return code
}

// --- CLI-backed probes -------------------------------------------------

// statusProbe renders a probe for a credential that a CLI reads out of the
// container's environment.
//
// The credential never reaches an argv: the tool picks it up from the env the
// broker delivered, and the shell only ever expands it into a `[ -z ]` test or
// an env assignment. Output is discarded wholesale — a tool's stdout can echo
// the very token it was handed, and only the marker is ever needed.
//
// The unset case is tested FIRST and reported distinctly, for the same reason
// the GitHub probe does it: an absent credential otherwise degrades into a
// generic tool failure and reads as a rejection, pointing the operator at
// rotating a token when nothing was ever delivered.
func statusProbe(envKey, command string) string {
	return "if [ -z \"${" + envKey + "}\" ]; then printf '" + probeUnset + "'; " +
		"elif timeout " + probeTimeout + " " + command + " >/dev/null 2>&1; then printf '" + probeOK + "'; " +
		"else printf '" + probeFail + "'; fi"
}

// brainVaultProbe renders the in-container probe for one brain vault's token.
//
// The local oracle hands pbrainctl the vault's token as CL_BRAIN_API_TOKEN
// whichever vault it came from (see checkBrainToken), so the probe aliases it
// the same way — a probe that read a different key than the oracle would make
// the two sides non-comparable, and the comparison is the point.
//
// pbrainctl also resolves CL_BRAIN_API from the environment, so this probe
// exercises ENDPOINT delivery as well as token delivery: a CL_BRAIN_API that
// points at a loopback address which is dead inside a container fails here
// while passing locally, which is exactly the class of bug this mode exists
// to surface.
func brainVaultProbe(tokenKey string) string {
	return statusProbe(tokenKey,
		"env CL_BRAIN_API_TOKEN=\"${"+tokenKey+"}\" pbrainctl client recall --limit 1 doctor")
}

// routerProbe renders the in-container probe for CL_API_KEY. prouterctl reads
// CL_ROUTER_API and CL_API_KEY from the environment, so a working prouterctl
// in the container is a delivered router credential.
func routerProbe() string {
	return statusProbe(RouterAPITokenKey, "prouterctl status")
}

// classifyStatusProbe turns statusProbe output into a fleet status.
//
// FAIL is reported as a rejection rather than as inconclusive: these tools
// fail for exactly two reasons the operator can act on — the delivered
// credential was refused, or the delivered ENDPOINT is unreachable from the
// container — and both are delivery faults. The detail names both so the fix
// is not mistaken for "rotate the token" when the endpoint is at fault.
func classifyStatusProbe(res FleetExecResult, credential, service string) (FleetStatus, string) {
	out := res.Output
	switch {
	case strings.Contains(out, probeUnset):
		return FleetUnset, credential + " is not set inside the container"
	case strings.Contains(out, probeOK):
		return FleetAccepted, "the " + service + " accepted the delivered " + credential
	case strings.Contains(out, probeFail):
		return FleetRejected, "the delivered " + credential + " did not work from the container — " +
			"the " + service + " refused it, or its endpoint is unreachable from there"
	}
	// No marker: the probe did not run to completion. A tool missing from the
	// image lands here, as does a container that died mid-exec.
	if !res.Success {
		return FleetUnknown, fmt.Sprintf("probe did not complete (exit %d)", res.ExitCode)
	}
	return FleetUnknown, "probe produced no recognisable result"
}

// --- cloud probes ------------------------------------------------------

// cloudProbe renders a probe for a cloud CLI.
//
// Cloud credentials have no single env key to test for, so the unset case
// cannot be carved out up front the way statusProbe does it: `aws` and `az`
// read from a spread of variables and files. Instead the CLI's own failure
// text comes back alongside the marker and is matched against the strings
// each tool uses to say "there are no credentials here at all".
//
// The text is matched, never reported: it is untrusted container output, and
// a CLI error can quote the argument that upset it.
func cloudProbe(command string) string {
	return "if out=$(timeout " + probeTimeout + " " + command + " 2>&1); then printf '" + probeOK + "'; " +
		"else printf '" + probeFail + ":%s' \"$out\"; fi"
}

// awsNotDelivered / azureNotDelivered are the phrases each CLI uses when no
// credential reached it at all, as opposed to one it could evaluate and
// refuse. Matched lowercase against the probe's captured failure text.
var (
	awsNotDelivered   = []string{"unable to locate credentials", "unable to locate"}
	azureNotDelivered = []string{"az login", "please run 'az login'"}
)

// classifyCloudProbe turns cloudProbe output into a fleet status.
//
// "Nothing was delivered" is separated from "delivered and refused" because
// the two have completely different fixes on this platform: the broker
// delivers ENV VARS, while cloud credentials live locally in ~/.aws and
// ~/.azure FILES. A not-delivered verdict here is therefore a legitimate
// finding about how cloud credentials reach the fleet, not a broken probe.
func classifyCloudProbe(res FleetExecResult, credential, service string, notDelivered []string) (FleetStatus, string) {
	out := res.Output
	if strings.Contains(out, probeOK) {
		return FleetAccepted, service + " accepted the delivered " + credential
	}
	if idx := strings.Index(out, probeFail); idx >= 0 {
		reason := strings.ToLower(out[idx+len(probeFail):])
		// A missing CLI is not a credential verdict. Saying "rejected" here
		// would send the operator to re-curate a value that was never read.
		// Matched tightly: a plain "not found" also appears in service
		// errors like "the subscription was not found", which IS a verdict.
		for _, m := range []string{"command not found", ": not found", "no such file"} {
			if strings.Contains(reason, m) {
				return FleetUnknown, service + " is not available in the container image"
			}
		}
		for _, m := range notDelivered {
			if strings.Contains(reason, m) {
				return FleetUnset, "no " + credential + " reached the container — " +
					service + " found no credentials at all"
			}
		}
		return FleetRejected, service + " refused the delivered " + credential
	}
	if !res.Success {
		return FleetUnknown, fmt.Sprintf("probe did not complete (exit %d)", res.ExitCode)
	}
	return FleetUnknown, "probe produced no recognisable result"
}

// --- the differential --------------------------------------------------

// remoteProbe pairs a credential's local oracle with its in-container probe.
//
// Every credential doctor can check remotely is one entry here; nothing else
// in this file knows how many there are or what they test.
type remoteProbe struct {
	// credential is the env key under test, or — for the cloud CLIs, whose
	// credentials span several keys and files — a short name for the set.
	credential string
	// localCheck names the check in the default catalog that acts as the
	// oracle for this credential.
	localCheck string
	// buildProbe renders the in-container shell probe.
	buildProbe func() string
	// classify turns probe output into a fleet status and a detail line.
	classify func(FleetExecResult) (FleetStatus, string)
	// deliveryFix explains how to repair a broken delivery of this credential.
	deliveryFix func(reason FleetStatus) string
}

// envDeliveryFix is the repair hint for a credential the broker delivers as an
// env var: either it was never curated, or the curated copy has gone stale.
func envDeliveryFix(credential string) func(FleetStatus) string {
	return func(reason FleetStatus) string {
		if reason == FleetUnset {
			return "add " + credential + " to the profile's gateway env store, then re-run"
		}
		return "the delivered " + credential + " is stale or wrong — re-curate it in the " +
			"profile's gateway env store (and check the matching endpoint URL is reachable " +
			"from a container), then re-run"
	}
}

// cloudDeliveryFix is the repair hint for a cloud CLI's credentials.
//
// The not-delivered branch does NOT say "add it to the env store": on this
// platform cloud credentials are local FILES (~/.aws, ~/.azure) and the broker
// delivers env vars, so nothing is currently wired to ship them at all. That
// is a decision to make, not a value to paste, and saying otherwise would send
// the operator looking for a store entry that was never meant to exist.
func cloudDeliveryFix(credential, dir string) func(FleetStatus) string {
	return func(reason FleetStatus) string {
		if reason == FleetUnset {
			return credential + " are not shipped to fleet containers — the broker delivers " +
				"env vars, not " + dir + " files; decide how cloud credentials should reach the node"
		}
		return credential + " are delivered but the value is stale or wrong — re-curate them, then re-run"
	}
}

// remoteProbes is the catalog of credentials that have a remote probe.
//
// gcloud is deliberately absent: it is not installed in the container image,
// so a probe would report "not delivered" for every profile and say nothing
// about delivery. Adding it needs an image change, which this mode does not
// make. The coverage note states the exclusion.
func remoteProbes() []remoteProbe {
	probes := []remoteProbe{{
		credential: "GITHUB_TOKEN",
		localCheck: "github token",
		buildProbe: func() string { return githubTokenProbe(githubAPIBase) },
		classify:   parseGitHubProbe,
		deliveryFix: func(reason FleetStatus) string {
			if reason == FleetUnset {
				return "add GITHUB_TOKEN to the profile's gateway env store, then re-run"
			}
			return "re-curate GITHUB_TOKEN in the profile's gateway env store — " +
				"the delivered value is stale, then re-run"
		},
	}}

	// One entry per brain vault, driven by the same table the local checks
	// use, so a vault added there cannot be silently missed here.
	for _, v := range brainVaults {
		v := v
		probes = append(probes, remoteProbe{
			credential: v.tokenKey,
			localCheck: "brain token (" + v.label + ")",
			buildProbe: func() string { return brainVaultProbe(v.tokenKey) },
			classify: func(res FleetExecResult) (FleetStatus, string) {
				return classifyStatusProbe(res, v.tokenKey, "brain daemon ("+v.label+" vault)")
			},
			deliveryFix: envDeliveryFix(v.tokenKey),
		})
	}

	return append(probes,
		remoteProbe{
			credential: RouterAPITokenKey,
			localCheck: "router",
			buildProbe: routerProbe,
			classify: func(res FleetExecResult) (FleetStatus, string) {
				return classifyStatusProbe(res, RouterAPITokenKey, "router")
			},
			deliveryFix: envDeliveryFix(RouterAPITokenKey),
		},
		remoteProbe{
			credential: "AWS credentials",
			localCheck: "aws",
			buildProbe: func() string { return cloudProbe("aws sts get-caller-identity") },
			classify: func(res FleetExecResult) (FleetStatus, string) {
				return classifyCloudProbe(res, "AWS credentials", "AWS STS", awsNotDelivered)
			},
			deliveryFix: cloudDeliveryFix("AWS credentials", "~/.aws"),
		},
		remoteProbe{
			credential: "Azure credentials",
			localCheck: "azure",
			buildProbe: func() string { return cloudProbe("az account show") },
			classify: func(res FleetExecResult) (FleetStatus, string) {
				return classifyCloudProbe(res, "Azure credentials", "Azure CLI", azureNotDelivered)
			},
			deliveryFix: cloudDeliveryFix("Azure credentials", "~/.azure"),
		},
	)
}

// FleetCoverageNote states what a fleet run does and does not cover. Rendered
// with every report: a clean run proves these credentials are delivered, and
// letting it read as full coverage would be the more dangerous outcome.
const FleetCoverageNote = "probed remotely: GITHUB_TOKEN, the brain vault tokens " +
	"(CL_BRAIN_API_TOKEN, CL_TODO_API_TOKEN, CL_SKILLS_API_TOKEN, CL_AGENTS_API_TOKEN), " +
	"CL_API_KEY (router), AWS and Azure — gcloud is excluded (not in the container image), " +
	"and any other credential is not covered by this report"

// RunFleetChecks runs the credential-delivery differential for a profile.
//
// The caller has already resolved the runner: SelectRunner picks one, and the
// same node is used for every probe so a verdict cannot be confounded by two
// probes landing on differently-configured hosts.
//
// Every Detail and Fix goes through Profile.Redact, matching RunChecks — the
// probe echoes container output back to us and must not be trusted to be free
// of the credential it was handed.
func RunFleetChecks(p *Profile, fc FleetClient, runner string, checks []Check) FleetReport {
	probes := remoteProbes()
	results := make([]FleetResult, len(probes))

	// 1. Every oracle first, before any session exists. Without a known-good
	//    local credential there is no baseline, and a remote failure could
	//    mean either half is at fault. Running them all up front is also what
	//    lets the survivors share ONE container.
	var probed []int
	for i, probe := range probes {
		res, hasBaseline := localBaseline(p, probe, checks)
		results[i] = res
		if hasBaseline {
			probed = append(probed, i)
		}
	}

	// 2. One session for all of them. The container a probe needs is the same
	//    container for every credential — it is the PROFILE that decides what
	//    is delivered into it — so creating one per credential would pay a
	//    provisioning cost (up to a five-minute timeout) once per row for no
	//    additional signal. Zero survivors means no session at all.
	if len(probed) > 0 {
		execProbes(fc, runner, p.Name, probes, probed, results)
	}

	report := FleetReport{Profile: p.Name, Runner: runner}
	for _, res := range results {
		res.Detail = p.Redact(res.Detail)
		res.Fix = p.Redact(res.Fix)
		report.Results = append(report.Results, res)
	}
	return report
}

// localBaseline runs one credential's oracle. The bool reports whether a
// baseline was established — i.e. whether this credential is worth probing
// remotely at all.
//
// A credential that fails here is short-circuited: standing up a container to
// confirm that an already-broken credential is also broken remotely costs a
// node slot and tells the user nothing.
func localBaseline(p *Profile, probe remoteProbe, checks []Check) (FleetResult, bool) {
	res := FleetResult{Credential: probe.credential}

	local := runNamedCheck(p, probe.localCheck, checks)
	res.Local = local.Status
	if local.Status == StatusOK {
		return res, true
	}

	res.Fleet = FleetNotRun
	res.Detail = fmt.Sprintf("fix locally first (local check is %s: %s)", local.Status, local.Detail)
	if local.Status == StatusFail {
		res.Verdict = VerdictLocalFirst
		res.Fix = local.Fix
		return res, false
	}
	// A skipped oracle — the credential is unset, or the service was
	// unreachable — is not a user-fixable failure, so neither is this.
	res.Verdict = VerdictInconclusive
	return res, false
}

// execProbes stands up ONE ephemeral session, runs every surviving probe in
// it, and tears it down.
//
// Teardown is deferred so it runs on EVERY path, including an exec error, a
// panic, and a create that partially succeeded. A leaked session holds a
// runner slot and — far worse — holds a live copy of the profile's credentials
// on a remote node.
//
// A failing exec does not abort the batch: each credential gets its own
// verdict, and one probe tripping over a missing tool must not erase the
// answer for the others.
func execProbes(fc FleetClient, runner, profile string, probes []remoteProbe, probed []int, results []FleetResult) {
	name, err := ephemeralSessionName()
	if err != nil {
		markInconclusive(probes, probed, results, runner, "could not generate a session name")
		return
	}

	if err := fc.CreateSession(FleetSessionSpec{
		Name:    name,
		Runner:  runner,
		Profile: profile,
		Role:    "developer",
	}); err != nil {
		// A create can fail after provisioning started, so tear down anyway.
		defer func() { _ = fc.DeleteSession(name) }()
		markInconclusive(probes, probed, results, runner,
			"could not create a session on "+runner+": "+firstLine(err.Error()))
		return
	}
	defer func() { _ = fc.DeleteSession(name) }()

	for _, i := range probed {
		probe := probes[i]
		status, detail := FleetUnknown, ""
		if res, execErr := fc.Exec(name, probe.buildProbe()); execErr != nil {
			detail = "probe could not run on " + runner + ": " + firstLine(execErr.Error())
		} else {
			status, detail = probe.classify(res)
		}
		applyRemote(&results[i], runner, probe, status, detail)
	}
}

// markInconclusive records the same non-verdict against every credential that
// was going to be probed. A session that could not be created says nothing
// about any of them.
func markInconclusive(probes []remoteProbe, probed []int, results []FleetResult, runner, detail string) {
	for _, i := range probed {
		applyRemote(&results[i], runner, probes[i], FleetUnknown, detail)
	}
}

// applyRemote diffs one probe's remote outcome against its passing oracle.
func applyRemote(res *FleetResult, runner string, probe remoteProbe, status FleetStatus, detail string) {
	res.Runner = runner
	res.Fleet = status
	res.Detail = detail

	switch status {
	case FleetAccepted:
		res.Verdict = VerdictPass
		res.Detail = fmt.Sprintf("%s delivered and accepted on %s", probe.credential, runner)
	case FleetRejected, FleetUnset:
		// The finding the whole mode exists for: good here, broken there.
		res.Verdict = VerdictDeliveryBroken
		res.Fix = probe.deliveryFix(status)
	default:
		// The node was unreachable, exec failed, or the container could not
		// reach the service. None of those is a delivery verdict.
		res.Verdict = VerdictInconclusive
	}
}

// runNamedCheck runs one check from the catalog by name. A name that is not in
// the catalog yields a skip rather than a panic: a renamed check must degrade
// to "could not establish a baseline", never to a false delivery verdict.
func runNamedCheck(p *Profile, name string, checks []Check) Result {
	if checks == nil {
		checks = DefaultChecks()
	}
	for _, c := range checks {
		if c.Name() == name {
			return c.Run(p)
		}
	}
	return skip("no local check named " + name + " — cannot establish a baseline")
}

// ephemeralSessionNamePrefix marks doctor's throwaway sessions so a leaked one
// is identifiable on the node.
const ephemeralSessionNamePrefix = "doctor-credcheck-"

// ephemeralSessionName builds a unique session name. Random rather than
// derived from the profile: two doctor runs against the same profile must not
// collide on a name, and a collision would delete the other run's session.
func ephemeralSessionName() (string, error) {
	buf := make([]byte, 4)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return ephemeralSessionNamePrefix + hex.EncodeToString(buf), nil
}

// --- runner selection --------------------------------------------------

// SelectRunner resolves which node the probes run on.
//
// A named runner is honoured as given but still must be remote: pointing a
// delivery test at the local runner would exercise this machine's own
// environment and report a pass that proves nothing about the fleet.
//
// With no name, the least-loaded remote session-capable runner wins, and the
// caller logs which one was chosen — a verdict that does not say which node it
// came from is not actionable.
func SelectRunner(fc FleetClient, requested string) (string, error) {
	runners, err := fc.ListRunners()
	if err != nil {
		return "", fmt.Errorf("could not list fleet runners: %w", err)
	}

	if requested != "" {
		for _, r := range runners {
			if r.Name != requested {
				continue
			}
			if !r.IsRemote() {
				return "", fmt.Errorf("runner %q is the local runner — a delivery check must run on a remote node", requested)
			}
			if !r.CanHostSession() {
				return "", fmt.Errorf("runner %q cannot host container sessions", requested)
			}
			return r.Name, nil
		}
		return "", fmt.Errorf("runner %q is not registered", requested)
	}

	best := ""
	bestLoad := 0
	for _, r := range runners {
		if !r.IsRemote() || !r.CanHostSession() || !r.HasCapacity() {
			continue
		}
		if best == "" || r.InFlight < bestLoad {
			best, bestLoad = r.Name, r.InFlight
		}
	}
	if best == "" {
		return "", fmt.Errorf("no remote session-capable runner is available")
	}
	return best, nil
}

// --- configuration -----------------------------------------------------

// Env keys the fleet mode reads, matching what prouterctl uses so a working
// prouterctl is a working `doctor --fleet`.
const (
	// RouterAPIKey names the orchestration API base URL.
	RouterAPIKey = "CL_ROUTER_API"
	// RouterAPITokenKey names the orchestration API auth key.
	RouterAPITokenKey = "CL_API_KEY"
)

// ResolveFleetConfig finds the orchestration API base URL and auth key for a
// profile, using the same file-then-live resolution the functional checks use:
// some profiles inject these at direnv-load rather than storing them in a file.
//
// The key goes through effectiveSecret so a live value is registered for
// redaction; the URL does not, because a redacted URL would hide the one
// detail a user needs to fix a misconfigured router.
func ResolveFleetConfig(p *Profile) (baseURL, apiKey string, err error) {
	baseURL = effectiveValue(p, RouterAPIKey)
	if baseURL == "" {
		return "", "", fmt.Errorf("%s is not set — it names the orchestration API", RouterAPIKey)
	}
	apiKey, _, found := effectiveSecret(p, RouterAPITokenKey)
	if !found {
		return "", "", fmt.Errorf("%s is not set — it authenticates to the orchestration API", RouterAPITokenKey)
	}
	return baseURL, apiKey, nil
}

// effectiveValue resolves a NON-secret key the same way effectiveSecret
// resolves a secret one: the profile file first, then the live environment and
// only for the current profile.
func effectiveValue(p *Profile, key string) string {
	if v, found := p.Lookup(key); found {
		return v
	}
	if !p.IsCurrent {
		return ""
	}
	return strings.TrimSpace(os.Getenv(key))
}
