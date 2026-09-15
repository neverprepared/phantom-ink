package doctor

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fullProfile builds a profile directory that passes the structure checks.
func fullProfile(t *testing.T, env string) string {
	t.Helper()
	dir := t.TempDir()
	writeFile(t, dir, ".env", env)
	writeFile(t, dir, ".envrc", "#!/usr/bin/env bash\n")
	writeFile(t, dir, ".gitconfig", "[user]\n\tname = Ada\n\temail = ada@example.com\n")
	return dir
}

func load(t *testing.T, dir string, stub *stubRunner) *Profile {
	t.Helper()
	return LoadProfile("demo", dir, stub.factory())
}

// --- structure ---------------------------------------------------------

func TestCheckEnvFile(t *testing.T) {
	dir := fullProfile(t, "A=1\n")
	p := load(t, dir, newStub(t, nil))
	if got := runOne(t, p, "profile env file"); got.Status != StatusOK {
		t.Errorf("status = %v, want ok (%s)", got.Status, got.Detail)
	}

	empty := t.TempDir()
	p2 := load(t, empty, newStub(t, nil))
	got := runOne(t, p2, "profile env file")
	if got.Status != StatusFail {
		t.Errorf("status = %v, want fail", got.Status)
	}
	if got.Fix == "" {
		t.Error("a failing check must carry a fix hint")
	}
}

func TestCheckDirenvAllowed(t *testing.T) {
	dir := fullProfile(t, "A=1\n")

	allowed := newStub(t, map[string]stubResponse{
		"direnv status": {out: "Found RC path .envrc\nFound RC allowed true\n"},
	})
	if got := runOne(t, load(t, dir, allowed), "direnv allowed"); got.Status != StatusOK {
		t.Errorf("allowed: status = %v, want ok", got.Status)
	}

	denied := newStub(t, map[string]stubResponse{
		"direnv status": {out: "Found RC path .envrc\nFound RC allowed false\n"},
	})
	got := runOne(t, load(t, dir, denied), "direnv allowed")
	if got.Status != StatusFail {
		t.Fatalf("denied: status = %v, want fail", got.Status)
	}
	if !strings.Contains(got.Fix, "direnv allow") {
		t.Errorf("fix should tell the user to run direnv allow, got %q", got.Fix)
	}

	// direnv not installed is not the user's config problem -> skip.
	if got := runOne(t, load(t, dir, newStub(t, nil)), "direnv allowed"); got.Status != StatusSkip {
		t.Errorf("uninstalled: status = %v, want skip", got.Status)
	}
}

func TestCheckGitconfig(t *testing.T) {
	dir := fullProfile(t, "A=1\n")
	if got := runOne(t, load(t, dir, newStub(t, nil)), "gitconfig"); got.Status != StatusOK {
		t.Errorf("status = %v, want ok (%s)", got.Status, got.Detail)
	}

	partial := t.TempDir()
	writeFile(t, partial, ".gitconfig", "[user]\n\tname = Ada\n")
	got := runOne(t, load(t, partial, newStub(t, nil)), "gitconfig")
	if got.Status != StatusFail {
		t.Fatalf("status = %v, want fail", got.Status)
	}
	if !strings.Contains(got.Detail, "user.email") {
		t.Errorf("detail should name the unset key, got %q", got.Detail)
	}
}

// --- env diff ----------------------------------------------------------

func TestCheckExpectedKeys(t *testing.T) {
	dir := fullProfile(t, "SET=value\nEMPTY=\n")
	writeFile(t, dir, ".env.example", "SET=\nEMPTY=\nABSENT=\n")

	got := runOne(t, load(t, dir, newStub(t, nil)), "expected env keys")
	if got.Status != StatusFail {
		t.Fatalf("status = %v, want fail", got.Status)
	}
	if !strings.Contains(got.Detail, "missing: ABSENT") {
		t.Errorf("detail should report the absent key, got %q", got.Detail)
	}
	if !strings.Contains(got.Detail, "empty: EMPTY") {
		t.Errorf("detail should report the empty key, got %q", got.Detail)
	}
	if strings.Contains(got.Detail, "value") {
		t.Errorf("detail leaked a value: %q", got.Detail)
	}
}

func TestCheckExpectedKeys_AllSet(t *testing.T) {
	dir := fullProfile(t, "A=1\nB=2\n")
	writeFile(t, dir, ".env.example", "A=\nB=\n")
	if got := runOne(t, load(t, dir, newStub(t, nil)), "expected env keys"); got.Status != StatusOK {
		t.Errorf("status = %v, want ok (%s)", got.Status, got.Detail)
	}
}

func TestCheckExpectedKeys_NoExampleSkips(t *testing.T) {
	dir := fullProfile(t, "A=1\n")
	if got := runOne(t, load(t, dir, newStub(t, nil)), "expected env keys"); got.Status != StatusSkip {
		t.Errorf("status = %v, want skip", got.Status)
	}
}

// --- github ------------------------------------------------------------

func TestCheckGitHubSSH(t *testing.T) {
	dir := fullProfile(t, "A=1\n")

	// GitHub exits 1 even on success; the greeting is the signal.
	authed := newStub(t, map[string]stubResponse{
		"ssh": {out: "Hi octocat! You've successfully authenticated, but GitHub does not provide shell access.", err: errFailed},
	})
	if got := runOne(t, load(t, dir, authed), "github ssh"); got.Status != StatusOK {
		t.Errorf("status = %v, want ok (%s)", got.Status, got.Detail)
	}

	denied := newStub(t, map[string]stubResponse{
		"ssh": {out: "git@github.com: Permission denied (publickey).", err: errFailed},
	})
	if got := runOne(t, load(t, dir, denied), "github ssh"); got.Status != StatusFail {
		t.Errorf("denied: status = %v, want fail", got.Status)
	}

	offline := newStub(t, map[string]stubResponse{
		"ssh": {out: "ssh: connect to host github.com port 22: Network is unreachable", err: errFailed},
	})
	if got := runOne(t, load(t, dir, offline), "github ssh"); got.Status != StatusSkip {
		t.Errorf("offline: status = %v, want skip", got.Status)
	}
}

// --- brain: the missing-vs-down distinction ----------------------------

func TestCheckBrainDaemon_UnsetURLFails(t *testing.T) {
	dir := fullProfile(t, "A=1\n")
	got := runOne(t, load(t, dir, newStub(t, nil)), "brain daemon")
	if got.Status != StatusFail {
		t.Fatalf("status = %v, want fail — an unset CL_BRAIN_API is the user's to fix", got.Status)
	}
	if !strings.Contains(got.Fix, EnvFileName) {
		t.Errorf("fix should point at the profile env file, got %q", got.Fix)
	}
}

func TestCheckBrainDaemon_DownSkips(t *testing.T) {
	dir := fullProfile(t, "CL_BRAIN_API=http://127.0.0.1:9998\n")
	stub := newStub(t, map[string]stubResponse{
		"curl": {out: "000", err: errFailed},
	})
	got := runOne(t, load(t, dir, stub), "brain daemon")
	if got.Status != StatusSkip {
		t.Fatalf("status = %v, want skip — a daemon that is down is not a config error", got.Status)
	}
	if !strings.Contains(got.Detail, "unreachable") {
		t.Errorf("detail should say unreachable, got %q", got.Detail)
	}
}

func TestCheckBrainDaemon_Reachable(t *testing.T) {
	dir := fullProfile(t, "CL_BRAIN_API=http://127.0.0.1:9998\n")
	stub := newStub(t, map[string]stubResponse{"curl": {out: "200"}})
	if got := runOne(t, load(t, dir, stub), "brain daemon"); got.Status != StatusOK {
		t.Errorf("status = %v, want ok (%s)", got.Status, got.Detail)
	}
}

func TestCheckBrainToken_MissingRequiredFails(t *testing.T) {
	dir := fullProfile(t, "CL_BRAIN_API=http://127.0.0.1:9998\n")
	got := runOne(t, load(t, dir, newStub(t, nil)), "brain token (memory)")
	if got.Status != StatusFail {
		t.Fatalf("status = %v, want fail", got.Status)
	}
	if !strings.Contains(got.Fix, "CL_BRAIN_API_TOKEN") {
		t.Errorf("fix should name the key, got %q", got.Fix)
	}
}

func TestCheckBrainToken_MissingOptionalSkips(t *testing.T) {
	dir := fullProfile(t, "CL_BRAIN_API_TOKEN=tok\n")
	stub := newStub(t, map[string]stubResponse{"pbrainctl": {out: "no results"}})
	got := runOne(t, load(t, dir, stub), "brain token (todo)")
	if got.Status != StatusSkip {
		t.Errorf("status = %v, want skip — an unconfigured vault is not a failure", got.Status)
	}
}

func TestCheckBrainToken_Valid(t *testing.T) {
	dir := fullProfile(t, "CL_BRAIN_API_TOKEN=tok-abcdef\n")
	stub := newStub(t, map[string]stubResponse{"pbrainctl": {out: "0 results"}})
	got := runOne(t, load(t, dir, stub), "brain token (memory)")
	if got.Status != StatusOK {
		t.Fatalf("status = %v, want ok (%s)", got.Status, got.Detail)
	}
	if !stub.called("pbrainctl client recall") {
		t.Errorf("expected a pbrainctl recall probe, got calls %v", stub.calls)
	}
}

func TestCheckBrainToken_RejectedFails(t *testing.T) {
	dir := fullProfile(t, "CL_BRAIN_API_TOKEN=tok-abcdef\n")
	stub := newStub(t, map[string]stubResponse{
		"pbrainctl": {out: "401 Unauthorized", err: errFailed},
	})
	got := runOne(t, load(t, dir, stub), "brain token (memory)")
	if got.Status != StatusFail {
		t.Fatalf("status = %v, want fail — a rejected token is the user's to fix", got.Status)
	}
}

func TestCheckBrainToken_DaemonDownSkips(t *testing.T) {
	dir := fullProfile(t, "CL_BRAIN_API_TOKEN=tok-abcdef\n")
	stub := newStub(t, map[string]stubResponse{
		"pbrainctl": {out: "dial tcp 127.0.0.1:9998: connect: connection refused", err: errFailed},
	})
	got := runOne(t, load(t, dir, stub), "brain token (memory)")
	if got.Status != StatusSkip {
		t.Fatalf("status = %v, want skip — an offline daemon must not be reported as a bad token", got.Status)
	}
}

// The token must reach pbrainctl through the child environment, never argv.
func TestCheckBrainToken_TokenNeverOnCommandLine(t *testing.T) {
	const token = "tok-super-secret-value"
	dir := fullProfile(t, "CL_TODO_API_TOKEN="+token+"\n")
	stub := newStub(t, map[string]stubResponse{"pbrainctl": {out: "ok"}})

	p := load(t, dir, stub)
	if got := runOne(t, p, "brain token (todo)"); got.Status != StatusOK {
		t.Fatalf("status = %v, want ok (%s)", got.Status, got.Detail)
	}
	for _, call := range stub.calls {
		if strings.Contains(call, token) {
			t.Fatalf("token value appeared on a command line: %q", call)
		}
	}
	if stub.env["CL_BRAIN_API_TOKEN"] != token {
		t.Errorf("expected the vault token in the child environment, got %q", maskFound(stub.env["CL_BRAIN_API_TOKEN"], token))
	}
}

// maskFound avoids printing the token in a failure message.
func maskFound(got, want string) string {
	if got == want {
		return "<the token>"
	}
	if got == "" {
		return "<empty>"
	}
	return "<a different value>"
}

// --- router ------------------------------------------------------------

func TestCheckRouter(t *testing.T) {
	dir := fullProfile(t, "CL_ROUTER_API=http://127.0.0.1:8080\nCL_API_KEY=key-abcdef\n")

	up := newStub(t, map[string]stubResponse{"prouterctl status": {out: "healthy"}})
	if got := runOne(t, load(t, dir, up), "router"); got.Status != StatusOK {
		t.Errorf("status = %v, want ok (%s)", got.Status, got.Detail)
	}

	down := newStub(t, map[string]stubResponse{
		"prouterctl status": {out: "Get http://127.0.0.1:8080: dial tcp: connection refused", err: errFailed},
	})
	if got := runOne(t, load(t, dir, down), "router"); got.Status != StatusSkip {
		t.Errorf("down: status = %v, want skip", got.Status)
	}

	rejected := newStub(t, map[string]stubResponse{
		"prouterctl status": {out: "403 Forbidden", err: errFailed},
	})
	if got := runOne(t, load(t, dir, rejected), "router"); got.Status != StatusFail {
		t.Errorf("rejected: status = %v, want fail", got.Status)
	}
}

func TestCheckRouter_UnsetKeysFail(t *testing.T) {
	dir := fullProfile(t, "CL_ROUTER_API=http://127.0.0.1:8080\n")
	got := runOne(t, load(t, dir, newStub(t, nil)), "router")
	if got.Status != StatusFail {
		t.Fatalf("status = %v, want fail", got.Status)
	}
	if !strings.Contains(got.Detail, "CL_API_KEY") {
		t.Errorf("detail should name the unset key, got %q", got.Detail)
	}
}

// --- cloud (conditional) -----------------------------------------------

func TestCheckCloud_SkippedWithoutDirectory(t *testing.T) {
	dir := fullProfile(t, "A=1\n")
	p := load(t, dir, newStub(t, map[string]stubResponse{
		"aws": {out: "{}"}, "az": {out: "{}"}, "gcloud": {out: "me@example.com"},
	}))
	for _, name := range []string{"aws", "azure", "gcloud"} {
		got := runOne(t, p, name)
		if got.Status != StatusSkip {
			t.Errorf("%s: status = %v, want skip when the profile has no directory", name, got.Status)
		}
	}
}

func TestCheckAWS_WithDirectory(t *testing.T) {
	dir := fullProfile(t, "A=1\n")
	if err := os.MkdirAll(filepath.Join(dir, ".aws"), 0o755); err != nil {
		t.Fatal(err)
	}

	valid := newStub(t, map[string]stubResponse{"aws": {out: `{"Account":"1"}`}})
	if got := runOne(t, load(t, dir, valid), "aws"); got.Status != StatusOK {
		t.Errorf("status = %v, want ok", got.Status)
	}

	expired := newStub(t, map[string]stubResponse{
		"aws": {out: "An error occurred (ExpiredToken)", err: errFailed},
	})
	if got := runOne(t, load(t, dir, expired), "aws"); got.Status != StatusFail {
		t.Errorf("expired: status = %v, want fail", got.Status)
	}
}

func TestCheckAzure_WithDirectory(t *testing.T) {
	dir := fullProfile(t, "A=1\n")
	if err := os.MkdirAll(filepath.Join(dir, ".azure-profiles"), 0o755); err != nil {
		t.Fatal(err)
	}
	loggedOut := newStub(t, map[string]stubResponse{
		"az": {out: "Please run 'az login' to setup account.", err: errFailed},
	})
	got := runOne(t, load(t, dir, loggedOut), "azure")
	if got.Status != StatusFail {
		t.Fatalf("status = %v, want fail", got.Status)
	}
	if !strings.Contains(got.Fix, "az login") {
		t.Errorf("fix should mention az login, got %q", got.Fix)
	}
}

func TestCheckGCloud_WithDirectory(t *testing.T) {
	dir := fullProfile(t, "A=1\n")
	if err := os.MkdirAll(filepath.Join(dir, ".gcloud"), 0o755); err != nil {
		t.Fatal(err)
	}
	active := newStub(t, map[string]stubResponse{"gcloud": {out: "me@example.com"}})
	if got := runOne(t, load(t, dir, active), "gcloud"); got.Status != StatusOK {
		t.Errorf("status = %v, want ok", got.Status)
	}
	none := newStub(t, map[string]stubResponse{"gcloud": {out: ""}})
	if got := runOne(t, load(t, dir, none), "gcloud"); got.Status != StatusFail {
		t.Errorf("status = %v, want fail", got.Status)
	}
}

// --- classification ----------------------------------------------------

func TestIsUnreachable(t *testing.T) {
	cases := map[string]bool{
		"dial tcp 127.0.0.1:9998: connect: connection refused": true,
		"Network is unreachable":                               true,
		"context deadline exceeded":                            true,
		"401 Unauthorized":                                     false,
		"invalid token":                                        false,
		"":                                                     false,
	}
	for out, want := range cases {
		if got := isUnreachable(out, nil); got != want {
			t.Errorf("isUnreachable(%q) = %v, want %v", out, got, want)
		}
	}
}

func TestIsNotInstalled(t *testing.T) {
	if !isNotInstalled(errors.New("command not installed: pbrainctl")) {
		t.Error("expected the ErrNotInstalled text to classify as not installed")
	}
	if isNotInstalled(errFailed) {
		t.Error("a plain exit status must not classify as not installed")
	}
}
