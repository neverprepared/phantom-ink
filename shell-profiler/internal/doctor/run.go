package doctor

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

// ProfileReport is the outcome of running the catalog against one profile.
type ProfileReport struct {
	Profile string   `json:"profile"`
	Path    string   `json:"path"`
	Results []Result `json:"results"`
}

// Counts summarises a report.
func (r ProfileReport) Counts() (okCount, failCount, skipCount int) {
	for _, res := range r.Results {
		switch res.Status {
		case StatusOK:
			okCount++
		case StatusFail:
			failCount++
		case StatusSkip:
			skipCount++
		}
	}
	return
}

// Report is the outcome of a whole doctor run.
type Report struct {
	Profiles []ProfileReport `json:"profiles"`
}

// Failed reports whether any check in any profile failed. Skips never fail a
// run — a service being offline is not something the user can fix here.
func (r Report) Failed() bool {
	for _, pr := range r.Profiles {
		if _, failCount, _ := pr.Counts(); failCount > 0 {
			return true
		}
	}
	return false
}

// FailCount totals failing checks across every profile.
func (r Report) FailCount() int {
	total := 0
	for _, pr := range r.Profiles {
		_, failCount, _ := pr.Counts()
		total += failCount
	}
	return total
}

// RunChecks executes checks against a profile and collects the results.
// Every Detail and Fix is passed through Profile.Redact, so a token value can
// never escape into a report even if a subprocess echoed one back.
func RunChecks(p *Profile, checks []Check) ProfileReport {
	report := ProfileReport{Profile: p.Name, Path: p.Dir}
	for _, c := range checks {
		res := c.Run(p)
		res.Detail = p.Redact(res.Detail)
		res.Fix = p.Redact(res.Fix)
		report.Results = append(report.Results, res)
	}
	return report
}

// ListProfileNames returns the profile directories under profilesDir, sorted.
// A directory counts as a profile when it carries a direnv file or a profile
// env file.
func ListProfileNames(profilesDir string) ([]string, error) {
	entries, err := os.ReadDir(profilesDir)
	if err != nil {
		return nil, fmt.Errorf("failed to read profiles directory: %w", err)
	}
	var names []string
	for _, e := range entries {
		if !e.IsDir() || e.Name() == ".git" {
			continue
		}
		dir := filepath.Join(profilesDir, e.Name())
		if _, err := os.Stat(filepath.Join(dir, EnvrcFileName)); err == nil {
			names = append(names, e.Name())
			continue
		}
		if _, err := os.Stat(filepath.Join(dir, EnvFileName)); err == nil {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	return names, nil
}

// CurrentProfileName derives the active profile from WORKSPACE_HOME, matching
// how the rest of the CLI identifies the current profile.
func CurrentProfileName(workspaceHome string) string {
	if workspaceHome == "" {
		return ""
	}
	return filepath.Base(filepath.Clean(workspaceHome))
}
