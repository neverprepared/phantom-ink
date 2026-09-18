package commands

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/neverprepared/shell-profile-manager/internal/doctor"
)

// fakeFleetClient is the FleetClient stand-in for the command layer. No test
// here touches a real fleet.
type fakeFleetClient struct {
	execRes doctor.FleetExecResult
	execErr error

	created []doctor.FleetSessionSpec
	deleted []string
}

func (f *fakeFleetClient) ListRunners() ([]doctor.FleetRunner, error) {
	return []doctor.FleetRunner{
		{Name: "Local", Host: "local-process", Capabilities: map[string]bool{"docker": true}},
		{Name: "m3-64", Host: "10.0.0.1", Capabilities: map[string]bool{"docker": true}, InFlight: 1, MaxConcurrent: 15},
	}, nil
}

func (f *fakeFleetClient) CreateSession(spec doctor.FleetSessionSpec) error {
	f.created = append(f.created, spec)
	return nil
}

func (f *fakeFleetClient) Exec(name, command string) (doctor.FleetExecResult, error) {
	if f.execErr != nil {
		return doctor.FleetExecResult{}, f.execErr
	}
	return f.execRes, nil
}

func (f *fakeFleetClient) DeleteSession(name string) error {
	f.deleted = append(f.deleted, name)
	return nil
}

// fleetOracle builds a one-check catalog standing in for the local
// GITHUB_TOKEN check.
func fleetOracle(status doctor.Status) []doctor.Check {
	return []doctor.Check{doctor.NewCheck("github token", doctor.CatGitHub, func(_ *doctor.Profile) doctor.Result {
		return doctor.Result{Status: status, Detail: "stubbed oracle", Fix: "regenerate the PAT"}
	})}
}

// probeOutput builds container output carrying an HTTP verdict.
func probeOutput(code string) doctor.FleetExecResult {
	return doctor.FleetExecResult{Success: true, Output: "SPFLEET:HTTP:" + code}
}

func TestRunDoctorFleet_PassingDelivery(t *testing.T) {
	root := t.TempDir()
	dir := makeProfile(t, root, "demo", healthyFiles())
	fake := &fakeFleetClient{execRes: probeOutput("200")}

	var buf bytes.Buffer
	err := RunDoctorFleet(root, FleetOptions{
		Out: &buf, WorkspaceHome: dir, Fleet: fake, Checks: fleetOracle(doctor.StatusOK),
	})
	if err != nil {
		t.Fatalf("RunDoctorFleet() error: %v\n%s", err, buf.String())
	}

	out := buf.String()
	if !strings.Contains(out, "GITHUB_TOKEN") || !strings.Contains(out, "m3-64") {
		t.Errorf("report should name the credential and the runner\n%s", out)
	}
	if len(fake.created) != 1 || fake.created[0].Profile != "demo" {
		t.Errorf("session must be created for the profile under test: %+v", fake.created)
	}
	if len(fake.deleted) != 1 {
		t.Errorf("the ephemeral session must be torn down, deletes = %d", len(fake.deleted))
	}
}

// A broken delivery is the user's to fix, so it must exit non-zero.
func TestRunDoctorFleet_BrokenDeliveryFailsTheRun(t *testing.T) {
	root := t.TempDir()
	dir := makeProfile(t, root, "demo", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctorFleet(root, FleetOptions{
		Out: &buf, WorkspaceHome: dir,
		Fleet:  &fakeFleetClient{execRes: probeOutput("401")},
		Checks: fleetOracle(doctor.StatusOK),
	})
	if err == nil {
		t.Fatalf("a rejected delivered token must fail the run\n%s", buf.String())
	}
	if !strings.Contains(buf.String(), string(doctor.VerdictDeliveryBroken)) {
		t.Errorf("report should carry the delivery-broken verdict\n%s", buf.String())
	}
}

// An unreachable node is not something the user can fix from here.
func TestRunDoctorFleet_InconclusiveDoesNotFailTheRun(t *testing.T) {
	root := t.TempDir()
	dir := makeProfile(t, root, "demo", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctorFleet(root, FleetOptions{
		Out: &buf, WorkspaceHome: dir,
		Fleet:  &fakeFleetClient{execErr: fmt.Errorf("connection refused")},
		Checks: fleetOracle(doctor.StatusOK),
	})
	if err != nil {
		t.Fatalf("an inconclusive probe must not fail the run: %v\n%s", err, buf.String())
	}
}

func TestRunDoctorFleet_JSON(t *testing.T) {
	root := t.TempDir()
	dir := makeProfile(t, root, "demo", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctorFleet(root, FleetOptions{
		Out: &buf, WorkspaceHome: dir, JSON: true,
		Fleet:  &fakeFleetClient{execRes: probeOutput("200")},
		Checks: fleetOracle(doctor.StatusOK),
	})
	if err != nil {
		t.Fatalf("RunDoctorFleet() error: %v", err)
	}

	var decoded struct {
		Profile  string `json:"profile"`
		Runner   string `json:"runner"`
		Coverage string `json:"coverage"`
		Results  []struct {
			Credential string `json:"credential"`
			Local      string `json:"local"`
			Fleet      string `json:"fleet"`
			Verdict    string `json:"verdict"`
		} `json:"results"`
	}
	if err := json.Unmarshal(buf.Bytes(), &decoded); err != nil {
		t.Fatalf("output is not valid JSON: %v\n%s", err, buf.String())
	}
	// The narrowed catalog carries the GITHUB_TOKEN oracle only, so that is
	// the row with a delivery verdict; the rest report no baseline.
	if len(decoded.Results) == 0 {
		t.Fatalf("JSON output carries no results: %+v", decoded)
	}
	if decoded.Results[0].Credential != "GITHUB_TOKEN" ||
		decoded.Results[0].Verdict != string(doctor.VerdictPass) {
		t.Errorf("unexpected JSON payload: %+v", decoded)
	}
	if decoded.Coverage == "" {
		t.Error("JSON output must carry the coverage caveat")
	}
}

// The local half reads the loaded environment, which belongs to the current
// profile. Naming a different one must be an error, not a silent mismatch.
func TestRunDoctorFleet_RejectsNonCurrentProfile(t *testing.T) {
	root := t.TempDir()
	dir := makeProfile(t, root, "demo", healthyFiles())
	makeProfile(t, root, "other", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctorFleet(root, FleetOptions{
		Profile: "other", Out: &buf, WorkspaceHome: dir,
		Fleet:  &fakeFleetClient{execRes: probeOutput("200")},
		Checks: fleetOracle(doctor.StatusOK),
	})
	if err == nil {
		t.Fatal("a non-current profile must be rejected")
	}
	if !strings.Contains(err.Error(), "current profile") {
		t.Errorf("error should explain the constraint, got %q", err)
	}
}

// Naming the current profile explicitly is fine.
func TestRunDoctorFleet_AcceptsCurrentProfileByName(t *testing.T) {
	root := t.TempDir()
	dir := makeProfile(t, root, "demo", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctorFleet(root, FleetOptions{
		Profile: "demo", Out: &buf, WorkspaceHome: dir,
		Fleet:  &fakeFleetClient{execRes: probeOutput("200")},
		Checks: fleetOracle(doctor.StatusOK),
	})
	if err != nil {
		t.Fatalf("RunDoctorFleet() error: %v\n%s", err, buf.String())
	}
}

func TestRunDoctorFleet_NoWorkspaceHome(t *testing.T) {
	t.Setenv("WORKSPACE_HOME", "")
	t.Setenv("WORKSPACE_PROFILE", "")

	var buf bytes.Buffer
	err := RunDoctorFleet(t.TempDir(), FleetOptions{
		Out: &buf, Fleet: &fakeFleetClient{}, Checks: fleetOracle(doctor.StatusOK),
	})
	if err == nil {
		t.Fatal("want an error when the current profile cannot be resolved")
	}
	if !strings.Contains(err.Error(), "WORKSPACE_HOME") {
		t.Errorf("error should name the missing variable, got %q", err)
	}
}

// A local failure short-circuits: no session is created, and the report points
// at the local fix — but it does NOT fail the run. `--fleet` exits non-zero
// for a broken DELIVERY only, and a credential that is broken here was never
// delivered anywhere to be measured.
func TestRunDoctorFleet_LocalFailureShortCircuits(t *testing.T) {
	root := t.TempDir()
	dir := makeProfile(t, root, "demo", healthyFiles())
	fake := &fakeFleetClient{execRes: probeOutput("200")}

	var buf bytes.Buffer
	err := RunDoctorFleet(root, FleetOptions{
		Out: &buf, WorkspaceHome: dir, Fleet: fake, Checks: fleetOracle(doctor.StatusFail),
	})
	if err != nil {
		t.Fatalf("a local-only fault must not fail a delivery check: %v\n%s", err, buf.String())
	}
	if len(fake.created) != 0 {
		t.Errorf("no session may be created when the oracle fails, got %d", len(fake.created))
	}
	out := buf.String()
	if !strings.Contains(out, "fix locally first") {
		t.Errorf("report should point at the local fix\n%s", out)
	}
	if !strings.Contains(out, "need a local fix first") {
		t.Errorf("report should count the local-first rows separately\n%s", out)
	}
}

// A broken DELIVERY is still a non-zero exit, and the error names the count.
func TestRunDoctorFleet_DeliveryFailureFailsTheRun(t *testing.T) {
	root := t.TempDir()
	dir := makeProfile(t, root, "demo", healthyFiles())
	// 401: the delivered token reached the container and was refused.
	fake := &fakeFleetClient{execRes: probeOutput("401")}

	var buf bytes.Buffer
	err := RunDoctorFleet(root, FleetOptions{
		Out: &buf, WorkspaceHome: dir, Fleet: fake, Checks: fleetOracle(doctor.StatusOK),
	})
	if err == nil {
		t.Fatalf("a broken delivery must fail the run\n%s", buf.String())
	}
	if !strings.Contains(err.Error(), "1 credential(s) failed the delivery check") {
		t.Errorf("error should count exactly the delivery failures, got %q", err)
	}
}

// Without a reachable orchestration API there is nothing to check; the error
// must name the missing configuration rather than failing obscurely.
func TestRunDoctorFleet_MissingRouterConfig(t *testing.T) {
	root := t.TempDir()
	dir := makeProfile(t, root, "demo", healthyFiles())
	t.Setenv(doctor.RouterAPIKey, "")
	t.Setenv(doctor.RouterAPITokenKey, "")

	var buf bytes.Buffer
	err := RunDoctorFleet(root, FleetOptions{Out: &buf, WorkspaceHome: dir, Checks: fleetOracle(doctor.StatusOK)})
	if err == nil {
		t.Fatal("want an error when the orchestration API is not configured")
	}
	if !strings.Contains(err.Error(), doctor.RouterAPIKey) {
		t.Errorf("error should name %s, got %q", doctor.RouterAPIKey, err)
	}
}
