package commands

// The `doctor --fleet` entry point: a credential-delivery differential.
//
// Where RunDoctor asks "does this profile work here?", this asks "do this
// profile's credentials survive the trip to a fleet node?" — a separate
// question, because agents run in containers whose credentials come from the
// broker's env store, not from this machine.

import (
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/neverprepared/shell-profile-manager/internal/doctor"
)

// FleetOptions controls a `doctor --fleet` run.
type FleetOptions struct {
	// Profile is the profile to check. Empty means the current profile.
	Profile string
	// Runner names the fleet node to probe on. Empty auto-selects the
	// least-loaded remote session-capable runner.
	Runner string
	// JSON emits machine-readable output instead of the text report.
	JSON bool

	// Out is where the report goes. Defaults to stdout.
	Out io.Writer
	// WorkspaceHome overrides the WORKSPACE_HOME lookup (tests set this).
	WorkspaceHome string
	// NewRunner overrides command execution (tests stub this).
	NewRunner func(dir string, env map[string]string) doctor.CmdRunner
	// Checks overrides the local-oracle catalog (tests narrow this).
	Checks []doctor.Check
	// Fleet overrides the orchestration client. Tests ALWAYS set this — no
	// test may touch a real fleet.
	Fleet doctor.FleetClient
}

// RunDoctorFleet checks that a profile's credentials reach a fleet node intact
// and functional. It returns an error when any credential fails the delivery
// check, so the CLI exits non-zero. An inconclusive probe — an unreachable
// node, a container that could not reach the service — never fails a run, the
// same rule a skipped check follows.
func RunDoctorFleet(profilesDir string, opts FleetOptions) error {
	out := opts.Out
	if out == nil {
		out = os.Stdout
	}

	name, err := fleetTargetProfile(opts)
	if err != nil {
		return err
	}

	dir := filepath.Join(profilesDir, name)
	if _, statErr := os.Stat(dir); statErr != nil {
		return fmt.Errorf("profile %q not found at %s", name, dir)
	}

	p := doctor.LoadProfile(name, dir, opts.NewRunner)
	p.IsCurrent = true

	fc := opts.Fleet
	if fc == nil {
		baseURL, apiKey, cfgErr := doctor.ResolveFleetConfig(p)
		if cfgErr != nil {
			return fmt.Errorf("doctor --fleet: %w", cfgErr)
		}
		fc = doctor.NewHTTPFleetClient(baseURL, apiKey)
	}

	runner, err := doctor.SelectRunner(fc, opts.Runner)
	if err != nil {
		return fmt.Errorf("doctor --fleet: %w", err)
	}

	report := doctor.RunFleetChecks(p, fc, runner, opts.Checks)

	if opts.JSON {
		if err := doctor.FormatFleetJSON(out, report); err != nil {
			return err
		}
	} else {
		doctor.FormatFleetText(out, report, colorEnabled())
	}

	if report.Failed() {
		return fmt.Errorf("doctor --fleet: %d credential(s) failed the delivery check", report.FailCount())
	}
	return nil
}

// fleetTargetProfile resolves which profile a fleet run covers.
//
// Unlike RunDoctor, a fleet run is meaningful ONLY for the current profile,
// and for the same reason the live-env oracle is: the local half of the
// differential reads the loaded environment. Run against another profile it
// would diff THIS profile's live credential against THAT profile's delivered
// one and call the mismatch a delivery bug. Naming a different profile is
// therefore an error, not a silent reinterpretation.
func fleetTargetProfile(opts FleetOptions) (string, error) {
	current := currentProfileName(FleetToDoctorOptions(opts))
	if current == "" {
		return "", fmt.Errorf("doctor --fleet needs the current profile, but WORKSPACE_HOME is not set\n\n  Usage:\n    shell-profiler doctor --fleet")
	}
	if opts.Profile != "" && opts.Profile != current {
		return "", fmt.Errorf("doctor --fleet only works for the current profile (%s), not %q\n\n  The local half of the check reads the loaded environment, which belongs to %s.",
			current, opts.Profile, current)
	}
	return current, nil
}

// FleetToDoctorOptions adapts fleet options to the shared profile-resolution
// helpers, which key off WorkspaceHome.
func FleetToDoctorOptions(opts FleetOptions) DoctorOptions {
	return DoctorOptions{Profile: opts.Profile, WorkspaceHome: opts.WorkspaceHome}
}
