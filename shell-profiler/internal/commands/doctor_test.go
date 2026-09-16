package commands

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/neverprepared/shell-profile-manager/internal/doctor"
)

// --- fixtures ----------------------------------------------------------

// makeProfile creates a minimal profile directory under root.
func makeProfile(t *testing.T, root, name string, files map[string]string) string {
	t.Helper()
	dir := filepath.Join(root, name)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	for rel, content := range files {
		path := filepath.Join(dir, rel)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// healthyFiles is a profile that passes the structure checks.
func healthyFiles() map[string]string {
	return map[string]string{
		".env":       "A=1\n",
		".envrc":     "#!/usr/bin/env bash\n",
		".gitconfig": "[user]\n\tname = Ada\n\temail = ada@example.com\n",
	}
}

// stubFactory returns a runner constructor whose commands all behave the same,
// so these tests never touch a live service.
func stubFactory(out string, err error) func(dir string, env map[string]string) doctor.CmdRunner {
	return func(dir string, env map[string]string) doctor.CmdRunner {
		return func(name string, args ...string) (string, error) { return out, err }
	}
}

// happyFactory returns a runner where every external tool reports success, so
// a fully-configured profile passes the whole catalog offline.
func happyFactory() func(dir string, env map[string]string) doctor.CmdRunner {
	replies := map[string]string{
		"direnv":     "Found RC path .envrc\nFound RC allowed true\n",
		"ssh":        "Hi ada! You've successfully authenticated.",
		"gh":         "Logged in to github.com",
		"curl":       "200",
		"pbrainctl":  "0 results",
		"prouterctl": "healthy",
	}
	return func(dir string, env map[string]string) doctor.CmdRunner {
		return func(name string, args ...string) (string, error) {
			out, found := replies[name]
			if !found {
				return "", fmt.Errorf("%w: %s", doctor.ErrNotInstalled, name)
			}
			if name == "ssh" {
				// github always exits 1 on `ssh -T`.
				return out, fmt.Errorf("exit status 1")
			}
			return out, nil
		}
	}
}

// configuredFiles is a profile whose env file satisfies the live checks.
func configuredFiles() map[string]string {
	files := healthyFiles()
	files[".env"] = strings.Join([]string{
		"CL_BRAIN_API=http://127.0.0.1:9998",
		"CL_BRAIN_API_TOKEN=brain-token-value",
		"CL_ROUTER_API=http://127.0.0.1:8080",
		"CL_API_KEY=router-key-value",
		"",
	}, "\n")
	files[".env.example"] = "CL_BRAIN_API=\nCL_BRAIN_API_TOKEN=\nCL_ROUTER_API=\nCL_API_KEY=\n"
	return files
}

// passingChecks is a narrow catalog used where the point of the test is the
// command plumbing rather than the checks themselves.
func passingChecks() []doctor.Check {
	return []doctor.Check{
		doctor.NewCheck("always ok", "test", func(*doctor.Profile) doctor.Result {
			return doctor.Result{Status: doctor.StatusOK, Detail: "fine"}
		}),
	}
}

func failingChecks() []doctor.Check {
	return []doctor.Check{
		doctor.NewCheck("always fails", "test", func(*doctor.Profile) doctor.Result {
			return doctor.Result{Status: doctor.StatusFail, Detail: "broken", Fix: "fix it"}
		}),
	}
}

func skippingChecks() []doctor.Check {
	return []doctor.Check{
		doctor.NewCheck("always skips", "test", func(*doctor.Profile) doctor.Result {
			return doctor.Result{Status: doctor.StatusSkip, Detail: "daemon offline"}
		}),
	}
}

// --- tests -------------------------------------------------------------

// A fully configured profile passes the entire real catalog with every
// external tool stubbed — no live service involved.
func TestRunDoctor_NamedProfile(t *testing.T) {
	root := t.TempDir()
	makeProfile(t, root, "work", configuredFiles())

	var buf bytes.Buffer
	err := RunDoctor(root, DoctorOptions{
		Profile:   "work",
		Out:       &buf,
		NewRunner: happyFactory(),
	})
	if err != nil {
		t.Fatalf("RunDoctor() error: %v\n%s", err, buf.String())
	}
	out := buf.String()
	if !strings.Contains(out, "=== work ===") {
		t.Errorf("report should be grouped under the profile name:\n%s", out)
	}
	if !strings.Contains(out, "All checks passed") {
		t.Errorf("a fully configured profile should pass:\n%s", out)
	}
	if strings.Contains(out, "brain-token-value") || strings.Contains(out, "router-key-value") {
		t.Error("the report leaked a token value")
	}
}

// An unconfigured profile fails on the keys the user has to add — and says so
// with a fix for each.
func TestRunDoctor_UnconfiguredProfileFailsWithFixes(t *testing.T) {
	root := t.TempDir()
	makeProfile(t, root, "bare", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctor(root, DoctorOptions{
		Profile:   "bare",
		Out:       &buf,
		NewRunner: happyFactory(),
	})
	if err == nil {
		t.Fatal("expected failures for a profile with no tokens configured")
	}
	out := buf.String()
	for _, key := range []string{"CL_BRAIN_API", "CL_BRAIN_API_TOKEN", "CL_ROUTER_API", "CL_API_KEY"} {
		if !strings.Contains(out, key) {
			t.Errorf("report should name the unset key %s:\n%s", key, out)
		}
	}
	if !strings.Contains(out, "-> fix:") {
		t.Errorf("every failure needs a fix hint:\n%s", out)
	}
}

func TestRunDoctor_UnknownProfile(t *testing.T) {
	root := t.TempDir()
	var buf bytes.Buffer
	err := RunDoctor(root, DoctorOptions{Profile: "ghost", Out: &buf, Checks: passingChecks()})
	if err == nil {
		t.Fatal("expected an error for a profile that does not exist")
	}
	if !strings.Contains(err.Error(), "ghost") {
		t.Errorf("error should name the profile, got %v", err)
	}
}

// No argument means the current profile, derived from WORKSPACE_HOME.
func TestRunDoctor_CurrentProfileFromWorkspaceHome(t *testing.T) {
	root := t.TempDir()
	dir := makeProfile(t, root, "current", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctor(root, DoctorOptions{
		Out:           &buf,
		WorkspaceHome: dir,
		Checks:        passingChecks(),
	})
	if err != nil {
		t.Fatalf("RunDoctor() error: %v", err)
	}
	if !strings.Contains(buf.String(), "=== current ===") {
		t.Errorf("expected the current profile to be checked:\n%s", buf.String())
	}
}

func TestRunDoctor_NoProfileAndNoWorkspaceHome(t *testing.T) {
	t.Setenv("WORKSPACE_HOME", "")
	var buf bytes.Buffer
	err := RunDoctor(t.TempDir(), DoctorOptions{Out: &buf, Checks: passingChecks()})
	if err == nil {
		t.Fatal("expected an error when there is no profile to check")
	}
	if !strings.Contains(err.Error(), "WORKSPACE_HOME") {
		t.Errorf("error should explain the missing WORKSPACE_HOME, got %v", err)
	}
}

func TestRunDoctor_AllIteratesEveryProfile(t *testing.T) {
	root := t.TempDir()
	makeProfile(t, root, "alpha", healthyFiles())
	makeProfile(t, root, "beta", healthyFiles())
	makeProfile(t, root, "gamma", healthyFiles())
	// Not a profile: no .envrc and no .env.
	makeProfile(t, root, "notes", map[string]string{"README.md": "hi"})

	var buf bytes.Buffer
	err := RunDoctor(root, DoctorOptions{All: true, Out: &buf, Checks: passingChecks()})
	if err != nil {
		t.Fatalf("RunDoctor() error: %v", err)
	}
	out := buf.String()
	for _, name := range []string{"alpha", "beta", "gamma"} {
		if !strings.Contains(out, "=== "+name+" ===") {
			t.Errorf("--all should cover %s:\n%s", name, out)
		}
	}
	if strings.Contains(out, "=== notes ===") {
		t.Error("--all should skip directories that are not profiles")
	}
}

func TestRunDoctor_AllWithNoProfiles(t *testing.T) {
	var buf bytes.Buffer
	err := RunDoctor(t.TempDir(), DoctorOptions{All: true, Out: &buf, Checks: passingChecks()})
	if err == nil {
		t.Fatal("expected an error when there are no profiles")
	}
}

// A failing check must produce an error, which the CLI turns into a non-zero
// exit code.
func TestRunDoctor_FailureIsNonZeroExit(t *testing.T) {
	root := t.TempDir()
	makeProfile(t, root, "work", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctor(root, DoctorOptions{Profile: "work", Out: &buf, Checks: failingChecks()})
	if err == nil {
		t.Fatal("a failing check must return an error so the CLI exits non-zero")
	}
	if !strings.Contains(err.Error(), "1 check(s) failed") {
		t.Errorf("error should count the failures, got %v", err)
	}
	if !strings.Contains(buf.String(), "-> fix: fix it") {
		t.Errorf("the report should still be printed with its fix hint:\n%s", buf.String())
	}
}

// Skips are informational: an offline daemon must not fail the run.
func TestRunDoctor_SkipsDoNotFail(t *testing.T) {
	root := t.TempDir()
	makeProfile(t, root, "work", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctor(root, DoctorOptions{Profile: "work", Out: &buf, Checks: skippingChecks()})
	if err != nil {
		t.Fatalf("skips must not fail the run, got %v", err)
	}
	if !strings.Contains(buf.String(), "daemon offline") {
		t.Errorf("the skip should still be reported:\n%s", buf.String())
	}
}

func TestRunDoctor_FailureAcrossAllProfiles(t *testing.T) {
	root := t.TempDir()
	makeProfile(t, root, "alpha", healthyFiles())
	makeProfile(t, root, "beta", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctor(root, DoctorOptions{All: true, Out: &buf, Checks: failingChecks()})
	if err == nil {
		t.Fatal("expected an error")
	}
	if !strings.Contains(err.Error(), "2 check(s) failed") {
		t.Errorf("failures should total across profiles, got %v", err)
	}
}

func TestRunDoctor_JSONOutput(t *testing.T) {
	root := t.TempDir()
	makeProfile(t, root, "work", healthyFiles())

	var buf bytes.Buffer
	err := RunDoctor(root, DoctorOptions{
		Profile: "work",
		JSON:    true,
		Out:     &buf,
		Checks:  failingChecks(),
	})
	if err == nil {
		t.Fatal("expected a non-nil error for a failing check")
	}

	var report doctor.Report
	if jerr := json.Unmarshal(buf.Bytes(), &report); jerr != nil {
		t.Fatalf("--json must emit valid JSON: %v\n%s", jerr, buf.String())
	}
	if len(report.Profiles) != 1 || report.Profiles[0].Profile != "work" {
		t.Fatalf("unexpected JSON payload: %+v", report)
	}
	if report.Profiles[0].Results[0].Status != doctor.StatusFail {
		t.Errorf("status did not survive JSON: %+v", report.Profiles[0].Results[0])
	}
	if strings.Contains(buf.String(), "\033[") {
		t.Error("JSON output must not contain ANSI colors")
	}
}

// End-to-end through the real catalog with every external command stubbed: no
// live service is required, and a token in the env file never reaches output.
func TestRunDoctor_RealCatalogOffline(t *testing.T) {
	const token = "tok_FAKE_ffffffffffffffffffffffffffffffff"
	root := t.TempDir()
	files := healthyFiles()
	files[".env"] = "CL_BRAIN_API=http://127.0.0.1:9998\nCL_BRAIN_API_TOKEN=" + token + "\n"
	files[".env.example"] = "CL_BRAIN_API=\nCL_BRAIN_API_TOKEN=\n"
	makeProfile(t, root, "work", files)

	var buf bytes.Buffer
	// Every command reports a refused connection: services are down, so the
	// live checks skip rather than fail.
	_ = RunDoctor(root, DoctorOptions{
		Profile:   "work",
		Out:       &buf,
		NewRunner: stubFactory("dial tcp 127.0.0.1:9998: connect: connection refused", fmt.Errorf("exit status 7")),
	})

	out := buf.String()
	if strings.Contains(out, token) {
		t.Fatal("the report leaked a token value")
	}
	if !strings.Contains(out, "unreachable") {
		t.Errorf("an offline daemon should be reported as unreachable:\n%s", out)
	}
}
