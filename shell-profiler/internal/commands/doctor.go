package commands

import (
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/neverprepared/shell-profile-manager/internal/doctor"
)

// DoctorOptions controls a doctor run.
type DoctorOptions struct {
	// Profile is the profile to check. Empty means the current profile.
	Profile string
	// All checks every profile under the profiles directory.
	All bool
	// JSON emits machine-readable output instead of the text report.
	JSON bool

	// Out is where the report goes. Defaults to stdout.
	Out io.Writer
	// WorkspaceHome overrides the WORKSPACE_HOME lookup (tests set this).
	WorkspaceHome string
	// NewRunner overrides command execution (tests stub this).
	NewRunner func(dir string, env map[string]string) doctor.CmdRunner
	// Checks overrides the catalog (tests narrow this).
	Checks []doctor.Check
}

// RunDoctor checks that a profile is fully set up and reports what is missing.
// It returns an error when any check FAILS, so the CLI exits non-zero. Skipped
// checks — an offline daemon, a tool that is not installed — never fail a run.
func RunDoctor(profilesDir string, opts DoctorOptions) error {
	out := opts.Out
	if out == nil {
		out = os.Stdout
	}
	checks := opts.Checks
	if checks == nil {
		checks = doctor.DefaultChecks()
	}

	names, err := targetProfiles(profilesDir, opts)
	if err != nil {
		return err
	}

	report := doctor.Report{}
	for _, name := range names {
		dir := filepath.Join(profilesDir, name)
		if _, statErr := os.Stat(dir); statErr != nil {
			return fmt.Errorf("profile %q not found at %s", name, dir)
		}
		p := doctor.LoadProfile(name, dir, opts.NewRunner)
		report.Profiles = append(report.Profiles, doctor.RunChecks(p, checks))
	}

	if opts.JSON {
		if err := doctor.FormatJSON(out, report); err != nil {
			return err
		}
	} else {
		doctor.FormatText(out, report, colorEnabled())
	}

	if report.Failed() {
		return fmt.Errorf("doctor: %d check(s) failed", report.FailCount())
	}
	return nil
}

// targetProfiles resolves which profiles a run covers.
func targetProfiles(profilesDir string, opts DoctorOptions) ([]string, error) {
	if opts.All {
		names, err := doctor.ListProfileNames(profilesDir)
		if err != nil {
			return nil, err
		}
		if len(names) == 0 {
			return nil, fmt.Errorf("no profiles found in %s", profilesDir)
		}
		return names, nil
	}

	if opts.Profile != "" {
		return []string{opts.Profile}, nil
	}

	workspaceHome := opts.WorkspaceHome
	if workspaceHome == "" {
		workspaceHome = os.Getenv("WORKSPACE_HOME")
	}
	current := doctor.CurrentProfileName(workspaceHome)
	if current == "" {
		return nil, fmt.Errorf("no profile specified and WORKSPACE_HOME is not set\n\n  Usage:\n    shell-profiler doctor <profile>\n    shell-profiler doctor --all")
	}
	return []string{current}, nil
}

// colorEnabled reports whether ANSI colors should be used. Honours NO_COLOR.
func colorEnabled() bool {
	return os.Getenv("NO_COLOR") == ""
}
