package doctor

import (
	"context"
	"strings"
	"testing"
)

func TestDockerSymlinkPresent(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"test -x /usr/local/bin/docker": {code: 0},
	}}
	got := DockerSymlinkCheck{}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
	if got.Remedy != nil {
		t.Fatal("a passing check must not offer a remedy")
	}
}

// The fault itself: a missing /usr/local/bin/docker is positively observed and
// stays StateFail, with a remedy the operator can read and copy.
func TestDockerSymlinkMissingIsFailWithRemedy(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"test -x /usr/local/bin/docker": {code: 1},
	}}
	got := DockerSymlinkCheck{}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
	if got.Remedy == nil || got.Remedy.Command == "" {
		t.Fatal("operator must see a command to run by hand")
	}
	if err := got.Remedy.Validate(); err != nil {
		t.Fatalf("remedy invalid: %v", err)
	}
}

// Regression: this remedy must never be offered as a one-click fix. /usr/local
// is root-owned on a default Apple Silicon macOS and /usr/local/bin may not
// exist, so `ln -sf` into it fails with EACCES on a likely-majority host —
// Doctor would claim a fix it cannot perform, on half of its entire auto-fix
// surface. The project's own CLAUDE.md records the fix as `sudo ln -sf`, and
// Doctor will not run sudo on the operator's behalf, so the sudo form belongs
// in the command text and nothing belongs in Exec.
func TestDockerSymlinkRemedyIsNotAutoSafeAndNeedsSudo(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"test -x /usr/local/bin/docker": {code: 1},
	}}
	got := DockerSymlinkCheck{}.Run(context.Background(), ex)
	if got.Remedy == nil {
		t.Fatal("want a remedy")
	}
	if got.Remedy.AutoSafe {
		t.Fatal("remedy must not be auto-safe: writing into root-owned /usr/local needs sudo, which Doctor will not acquire")
	}
	if len(got.Remedy.Exec) != 0 {
		t.Fatalf("Exec = %v, want empty: an argv that cannot succeed must not be carried at all", got.Remedy.Exec)
	}
	if !strings.HasPrefix(got.Remedy.Command, "sudo ") {
		t.Fatalf("Command = %q, want the sudo form the operator actually needs to run", got.Remedy.Command)
	}
}

// The regression that matters: an executor that could not answer must not be
// reported as a missing symlink.
func TestDockerSymlinkUnobservableIsUnknown(t *testing.T) {
	ex := &fakeExec{} // no stub => error
	got := DockerSymlinkCheck{}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown", got.State)
	}
	if got.Detail == "" {
		t.Fatal("unknown must explain why it could not tell")
	}
}

func TestAppDBMissingOrEmptyIsFail(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"stat -f %z /tmp/none.db": {out: "0\n", code: 0},
	}}
	got := AppDBCheck{Path: "/tmp/none.db"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail for a zero-byte db", got.State)
	}
	// The fix is an app restart, which Doctor cannot shell out to on itself.
	if got.Remedy == nil || got.Remedy.AutoSafe {
		t.Fatal("want a non-auto-safe remedy")
	}
}

func TestAppDBPopulatedIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"stat -f %z /tmp/db": {out: "40960\n", code: 0},
	}}
	got := AppDBCheck{Path: "/tmp/db"}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass", got.State)
	}
}

func TestAppDBUnparsableSizeIsUnknown(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"stat -f %z /tmp/db": {out: "not-a-number\n", code: 0},
	}}
	got := AppDBCheck{Path: "/tmp/db"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown", got.State)
	}
}

// Regression: an empty Path must never reach the stat call. The fixture below
// stubs "stat -f %z " (the exact command an empty Path would produce) to
// return a healthy non-zero size — simulating the real danger: some unrelated
// file happening to sit at the resolved (wrong) location. If the Path=="" guard
// in AppDBCheck.Run is removed, this test starts reporting StatePass off that
// fixture instead of StateUnknown, which is exactly the false pass this guard
// exists to prevent. Without the fixture, removing the guard would still
// surface as fakeExec's "no stub" error path — a weaker pin, since that hides
// the guard behind an unrelated plumbing failure instead of proving the
// dangerous case.
func TestAppDBEmptyPathIsUnknown(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"stat -f %z ": {out: "40960\n", code: 0},
	}}
	got := AppDBCheck{Path: ""}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown (detail %q)", got.State, got.Detail)
	}
}

// Regression: a relative Path (e.g. produced by a caller falling back to
// filepath.Join("", ...) after os.UserHomeDir fails) must also be refused as
// unknown rather than stated against the process's cwd. The fixture stubs the
// exact relative stat command with a healthy non-zero size, so if the
// !filepath.IsAbs(c.Path) half of the guard in AppDBCheck.Run is removed, this
// test starts reporting StatePass — a plausible-looking "app database
// initialised" having never looked at the real ~/.phantom-ink.db.
func TestAppDBRelativePathIsUnknown(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"stat -f %z .phantom-ink.db": {out: "40960\n", code: 0},
	}}
	got := AppDBCheck{Path: ".phantom-ink.db"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown (detail %q)", got.State, got.Detail)
	}
}

// Regression: zero profile directories must never read as a pass. A caller
// that failed to enumerate profiles (a bad workspaces root, a permissions
// error) and so passes an empty ProfileDirs must get StateUnknown, not a
// confident "all 0 profiles approved" green over an observation of nothing.
func TestDirenvNoProfileDirsIsUnknownNotPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{}}
	got := DirenvApprovalCheck{ProfileDirs: nil}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown (detail %q)", got.State, got.Detail)
	}
}

func TestDirenvAllAllowedIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		`sh -c cd "$1" && direnv status sh /p/one`: {out: "Found RC allowed 0\n", code: 0},
	}}
	got := DirenvApprovalCheck{ProfileDirs: []string{"/p/one"}}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
}

func TestDirenvUnapprovedIsWarnNamingTheProfile(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		`sh -c cd "$1" && direnv status sh /p/one`: {out: "Found RC allowed 0\n", code: 0},
		`sh -c cd "$1" && direnv status sh /p/two`: {out: "Found RC allowed 2\n", code: 0},
	}}
	got := DirenvApprovalCheck{ProfileDirs: []string{"/p/one", "/p/two"}}.Run(context.Background(), ex)
	if got.State != StateWarn {
		t.Fatalf("state = %q, want warn", got.State)
	}
	if !strings.Contains(got.Detail, "/p/two") {
		t.Fatalf("detail %q must name the unapproved profile", got.Detail)
	}
	if strings.Contains(got.Detail, "/p/one") {
		t.Fatalf("detail %q must not list approved profiles", got.Detail)
	}
}

func TestDirenvMissingAllowedLineIsUnknown(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		`sh -c cd "$1" && direnv status sh /p/one`: {out: "some other output\n", code: 0},
	}}
	got := DirenvApprovalCheck{ProfileDirs: []string{"/p/one"}}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown", got.State)
	}
}

// Regression: the profile directory must be passed as a shell argument ($1),
// never interpolated into the script text — a space in the path used to break
// the command entirely. This pins that a space-containing directory is still
// queried correctly end to end.
func TestDirenvApprovalDirWithSpaceIsQueriedCorrectly(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		`sh -c cd "$1" && direnv status sh /p/with space`: {out: "Found RC allowed 0\n", code: 0},
	}}
	got := DirenvApprovalCheck{ProfileDirs: []string{"/p/with space"}}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
}

func TestGitHubTokenShadowDetected(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"zsh -lc printenv GH_TOKEN":     {out: "", code: 1},
		"zsh -lc printenv GITHUB_TOKEN": {out: "ghp_xxx\n", code: 0},
		"gh auth status":                {out: "Active account: false\n", code: 0},
	}}
	got := GitHubTokenShadowCheck{}.Run(context.Background(), ex)
	if got.State != StateWarn {
		t.Fatalf("state = %q, want warn", got.State)
	}
	if strings.Contains(got.Detail, "ghp_") {
		t.Fatalf("detail must never echo the token value: %q", got.Detail)
	}
	if !strings.Contains(got.Detail, "GITHUB_TOKEN") {
		t.Fatalf("detail %q must name GITHUB_TOKEN as the shadowing variable", got.Detail)
	}
}

func TestGitHubTokenAbsentIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"zsh -lc printenv GH_TOKEN":     {out: "", code: 1},
		"zsh -lc printenv GITHUB_TOKEN": {out: "", code: 1},
		"gh auth status":                {out: "Active account: true\n", code: 0},
	}}
	got := GitHubTokenShadowCheck{}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass", got.State)
	}
}

// Regression: GH_TOKEN alone (GITHUB_TOKEN absent) must still be caught. gh
// honors GH_TOKEN with precedence over GITHUB_TOKEN, and this project's own
// documented failure mode is exactly a stale GH_TOKEN/GITHUB_TOKEN shadowing
// a working keyring login — dropping GH_TOKEN from the probe would make this
// check confidently pass while the thing it exists to catch is happening.
func TestGitHubTokenShadowDetectedViaGHTokenAlone(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"zsh -lc printenv GH_TOKEN": {out: "gho_yyy\n", code: 0},
		"gh auth status":            {out: "Active account: false\n", code: 0},
	}}
	got := GitHubTokenShadowCheck{}.Run(context.Background(), ex)
	if got.State != StateWarn {
		t.Fatalf("state = %q, want warn", got.State)
	}
	if strings.Contains(got.Detail, "gho_") {
		t.Fatalf("detail must never echo the token value: %q", got.Detail)
	}
	if !strings.Contains(got.Detail, "GH_TOKEN") {
		t.Fatalf("detail %q must name GH_TOKEN as the shadowing variable", got.Detail)
	}
}

func TestPortShadowOccupiedIsFail(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"lsof -nP -iTCP:9910 -sTCP:LISTEN": {out: "node 123 cdowning 20u IPv4 TCP *:9910 (LISTEN)\n", code: 0},
	}}
	got := PortShadowCheck{Port: "9910"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
	if got.Remedy == nil || got.Remedy.AutoSafe {
		t.Fatal("killing a listener is destructive; remedy must not be auto-safe")
	}
}

func TestPortShadowFreeIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"lsof -nP -iTCP:9910 -sTCP:LISTEN": {out: "", code: 1},
	}}
	got := PortShadowCheck{Port: "9910"}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass", got.State)
	}
}

func TestGatewayMCPPinIntactIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"docker exec gw pip show mcp": {out: "Name: mcp\nVersion: 1.9.4\n", code: 0},
	}}
	got := GatewayMCPPinCheck{Container: "gw"}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass", got.State)
	}
}

func TestGatewayMCPPinBrokenIsFail(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"docker exec gw pip show mcp": {out: "Name: mcp\nVersion: 2.0.1\n", code: 0},
	}}
	got := GatewayMCPPinCheck{Container: "gw"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail for mcp 2.x", got.State)
	}
}

func TestGatewayMCPPinUnreadableIsUnknown(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"docker exec gw pip show mcp": {out: "no version line here\n", code: 0},
	}}
	got := GatewayMCPPinCheck{Container: "gw"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown", got.State)
	}
}

// lsof's real output, header and all. The nine-character COMMAND truncation is
// not incidental: com.docker.backend appears as "com.docke", so the docker
// predicate has to match on a prefix.
const lsofDockerPublisher = `COMMAND     PID     USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
com.docke 50216 cdowning   36u  IPv6 0x9f3a1c7e5b2d4068      0t0  TCP *:9910 (LISTEN)
`

// Regression, and the whole correctness of this check: in the local-platform
// configuration the platform's OWN router publishes 9910 to the host, and a
// published container port appears in lsof exactly like a shadowing process.
// Inferring a shadow from output non-emptiness therefore reported a hard fail —
// with a destructive `launchctl unload` remedy for a job that does not exist —
// on every correctly-working local install. The COMMAND column is what tells
// the two apart.
func TestPortShadowDockerPublishedPortIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"lsof -nP -iTCP:9910 -sTCP:LISTEN": {out: lsofDockerPublisher, code: 0},
	}}
	got := PortShadowCheck{Port: "9910"}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass — docker publishing the platform's own port is not a shadow (detail %q)", got.State, got.Detail)
	}
	if got.Remedy != nil {
		t.Fatalf("a passing check must not offer the launchctl remedy, got %+v", got.Remedy)
	}
}

// The converse must keep working: a non-docker listener is still a hard fail,
// and the detail must name it so the operator can find the job.
func TestPortShadowNonDockerListenerIsFailNamingIt(t *testing.T) {
	out := `COMMAND  PID     USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
prouter 7781 cdowning   20u  IPv4 0x1b4c8d2a6f0e3759      0t0  TCP *:9910 (LISTEN)
`
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"lsof -nP -iTCP:9910 -sTCP:LISTEN": {out: out, code: 0},
	}}
	got := PortShadowCheck{Port: "9910"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail for a host process on the port (detail %q)", got.State, got.Detail)
	}
	if !strings.Contains(got.Detail, "prouter") {
		t.Fatalf("detail %q must name the shadowing command", got.Detail)
	}
	if got.Remedy == nil || got.Remedy.AutoSafe {
		t.Fatal("killing a listener is destructive; remedy must not be auto-safe")
	}
}

// A docker listener alongside a foreign one is still a shadow — the foreign
// process is the one answering for some connections, which is the whole fault.
func TestPortShadowDockerPlusForeignIsFail(t *testing.T) {
	out := `COMMAND     PID     USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
com.docke 50216 cdowning   36u  IPv6 0x9f3a1c7e5b2d4068      0t0  TCP *:9910 (LISTEN)
node      7781 cdowning   20u  IPv4 0x1b4c8d2a6f0e3759      0t0  TCP *:9910 (LISTEN)
`
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"lsof -nP -iTCP:9910 -sTCP:LISTEN": {out: out, code: 0},
	}}
	got := PortShadowCheck{Port: "9910"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail when a foreign listener shares the port (detail %q)", got.State, got.Detail)
	}
	if !strings.Contains(got.Detail, "node") {
		t.Fatalf("detail %q must name the foreign listener", got.Detail)
	}
}

// Output arrived but carries no data row. That is "I could not tell", and
// guessing either way would be a verdict over an unread observation.
func TestPortShadowUnparseableOutputIsUnknown(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"lsof -nP -iTCP:9910 -sTCP:LISTEN": {
			out:  "COMMAND     PID     USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n",
			code: 0,
		},
	}}
	got := PortShadowCheck{Port: "9910"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown for output with no data row (detail %q)", got.State, got.Detail)
	}
}

// Regression: the detail must say WHOSE environment was observed. The old
// wording — "no GH_TOKEN or GITHUB_TOKEN in the environment" — described the
// app process's own environment, which a packaged .app inherits empty from
// Finder or launchd, and read to the operator as "my shell is clean". That is
// what made it a false pass rather than a stated limitation.
func TestGitHubTokenAbsentPassNamesTheLoginShell(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"zsh -lc printenv GH_TOKEN":     {out: "", code: 1},
		"zsh -lc printenv GITHUB_TOKEN": {out: "", code: 1},
	}}
	got := GitHubTokenShadowCheck{}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
	if !strings.Contains(got.Detail, "login shell") {
		t.Fatalf("detail %q must name whose environment it observed, not just \"the environment\"", got.Detail)
	}
}

// Regression: the probe must go through the operator's login shell. A bare
// printenv reads the app process's own environment — nothing at all in a
// packaged .app — so the check would pass while the operator's shell carried
// the stale token. This asserts on the argv that ran, because a check reading
// the wrong subject still returns a plausible-looking state.
func TestGitHubTokenProbeGoesThroughTheLoginShell(t *testing.T) {
	ex := &recordingExec{fakeExec: fakeExec{shellOut: map[string]fakeShell{
		"zsh -lc printenv GH_TOKEN":     {out: "", code: 1},
		"zsh -lc printenv GITHUB_TOKEN": {out: "", code: 1},
	}}}
	if got := (GitHubTokenShadowCheck{}).Run(context.Background(), ex); got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
	var sawLoginShell bool
	for _, call := range ex.shellCalls {
		joined := strings.Join(call, " ")
		if call[0] != "zsh" {
			t.Fatalf("probed %v — that reads the app's own environment, not the operator's shell", call)
		}
		if strings.HasPrefix(joined, "zsh -lc printenv ") {
			sawLoginShell = true
		}
	}
	if !sawLoginShell {
		t.Fatalf("no login-shell probe among %v", ex.shellCalls)
	}
}

// If the login shell cannot be run, the operator's environment was not
// observed. Unknown, naming that — never a pass.
func TestGitHubTokenLoginShellUnrunnableIsUnknown(t *testing.T) {
	ex := &fakeExec{} // no stub => error
	got := GitHubTokenShadowCheck{}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown when the login shell could not be run (detail %q)", got.State, got.Detail)
	}
	if !strings.Contains(got.Detail, "login shell") {
		t.Fatalf("detail %q must say it could not read the login shell's environment", got.Detail)
	}
}
