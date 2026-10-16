package doctor

import (
	"context"
	"errors"
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

func TestDockerSymlinkMissingIsFailWithAutoSafeRemedy(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"test -x /usr/local/bin/docker": {code: 1},
	}}
	got := DockerSymlinkCheck{}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
	if got.Remedy == nil || !got.Remedy.AutoSafe {
		t.Fatal("want an auto-safe remedy")
	}
	if err := got.Remedy.Validate(); err != nil {
		t.Fatalf("remedy invalid: %v", err)
	}
}

// A missing symlink is still positively observed even when the home
// directory needed to build the fix can't be resolved. The fault stays
// StateFail; only the auto-fix degrades, and it degrades to "operator must
// run it themselves" rather than Doctor executing an argv built from an
// unresolved path.
func TestDockerSymlinkMissingWithUnresolvableHomeIsFailWithManualRemedy(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"test -x /usr/local/bin/docker": {code: 1},
	}}
	c := DockerSymlinkCheck{homeDir: func() (string, error) {
		return "", errors.New("$HOME is not defined")
	}}
	got := c.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
	if got.Remedy == nil {
		t.Fatal("want a remedy even without an auto-fix")
	}
	if got.Remedy.AutoSafe {
		t.Fatal("remedy must not be auto-safe when the home directory could not be resolved")
	}
	if len(got.Remedy.Exec) != 0 {
		t.Fatalf("Exec = %v, want empty: must never build an argv from an unresolved path", got.Remedy.Exec)
	}
	if got.Remedy.Command == "" {
		t.Fatal("operator must still see a command to run by hand")
	}
	if err := got.Remedy.Validate(); err != nil {
		t.Fatalf("remedy invalid: %v", err)
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
		"printenv GH_TOKEN":     {out: "", code: 1},
		"printenv GITHUB_TOKEN": {out: "ghp_xxx\n", code: 0},
		"gh auth status":        {out: "Active account: false\n", code: 0},
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
		"printenv GH_TOKEN":     {out: "", code: 1},
		"printenv GITHUB_TOKEN": {out: "", code: 1},
		"gh auth status":        {out: "Active account: true\n", code: 0},
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
		"printenv GH_TOKEN": {out: "gho_yyy\n", code: 0},
		"gh auth status":    {out: "Active account: false\n", code: 0},
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
