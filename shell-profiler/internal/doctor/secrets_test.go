package doctor

import (
	"bytes"
	"strings"
	"testing"
)

// These are the fake values the hygiene tests assert never reach output.
const (
	fakeBrainToken  = "brn_FAKE_0123456789abcdef0123456789abcdef"
	fakeRouterKey   = "rtr_FAKE_fedcba9876543210fedcba9876543210"
	fakeSecretsOnly = "sec_FAKE_aaaaaaaabbbbbbbbccccccccdddddddd"
)

// secretProfile builds a profile whose env files carry the fake tokens above.
func secretProfile(t *testing.T, stub *stubRunner) *Profile {
	t.Helper()
	dir := fullProfile(t, strings.Join([]string{
		"CL_BRAIN_API=http://127.0.0.1:9998",
		"CL_BRAIN_API_TOKEN=" + fakeBrainToken,
		"CL_TODO_API_TOKEN=" + fakeBrainToken,
		"CL_ROUTER_API=http://127.0.0.1:8080",
		"CL_API_KEY=" + fakeRouterKey,
		"",
	}, "\n"))
	writeFile(t, dir, ".env.example", "CL_BRAIN_API=\nCL_BRAIN_API_TOKEN=\nCL_ROUTER_API=\nCL_API_KEY=\nCL_MISSING_KEY=\n")
	writeFile(t, dir, ".env.secrets", "EXTRA_SECRET="+fakeSecretsOnly+"\n")
	return load(t, dir, stub)
}

// The load-bearing hygiene test: run the full catalog against a profile full of
// tokens, with every external command echoing those tokens back, and assert no
// value appears in the text report, the JSON report, or any Result field.
func TestSecretValuesNeverAppearInOutput(t *testing.T) {
	// Hostile stubs: every tool prints the token it was given.
	leaky := stubResponse{out: "error: token " + fakeBrainToken + " rejected; key " + fakeRouterKey + " and " + fakeSecretsOnly, err: errFailed}
	stub := newStub(t, map[string]stubResponse{
		"direnv":     leaky,
		"ssh":        leaky,
		"gh":         leaky,
		"curl":       leaky,
		"pbrainctl":  leaky,
		"prouterctl": leaky,
		"aws":        leaky,
		"az":         leaky,
		"gcloud":     leaky,
	})

	p := secretProfile(t, stub)
	report := Report{Profiles: []ProfileReport{RunChecks(p, DefaultChecks())}}

	secrets := map[string]string{
		"CL_BRAIN_API_TOKEN": fakeBrainToken,
		"CL_API_KEY":         fakeRouterKey,
		"EXTRA_SECRET":       fakeSecretsOnly,
	}

	// 1. No Result field carries a value.
	for _, res := range report.Profiles[0].Results {
		for name, value := range secrets {
			if strings.Contains(res.Detail, value) {
				t.Errorf("check %q leaked %s into Detail", res.Name, name)
			}
			if strings.Contains(res.Fix, value) {
				t.Errorf("check %q leaked %s into Fix", res.Name, name)
			}
		}
	}

	// 2. Not in the rendered text report.
	var text bytes.Buffer
	FormatText(&text, report, true)
	for name, value := range secrets {
		if strings.Contains(text.String(), value) {
			t.Errorf("text report leaked %s", name)
		}
	}

	// 3. Not in the JSON report.
	var jsonOut bytes.Buffer
	if err := FormatJSON(&jsonOut, report); err != nil {
		t.Fatal(err)
	}
	for name, value := range secrets {
		if strings.Contains(jsonOut.String(), value) {
			t.Errorf("json report leaked %s", name)
		}
	}

	// 4. Not in the command lines doctor executed.
	for _, call := range stub.calls {
		for name, value := range secrets {
			if strings.Contains(call, value) {
				t.Errorf("%s appeared in a command line", name)
			}
		}
	}

	// Sanity: the report is not empty, so the assertions above ran against
	// real content.
	if len(report.Profiles[0].Results) != len(DefaultChecks()) {
		t.Fatalf("expected a full report, got %d results", len(report.Profiles[0].Results))
	}
	if text.Len() == 0 {
		t.Fatal("empty text report — the leak assertions proved nothing")
	}
}

// Redact is the last line of defence and must catch a value wherever it lands.
func TestRedact(t *testing.T) {
	p := &Profile{
		Env:     map[string]string{"TOKEN": fakeBrainToken, "SHORT": "ab"},
		Secrets: map[string]string{"OTHER": fakeSecretsOnly},
	}

	got := p.Redact("failed with " + fakeBrainToken + " and " + fakeSecretsOnly)
	if strings.Contains(got, fakeBrainToken) || strings.Contains(got, fakeSecretsOnly) {
		t.Errorf("Redact left a secret behind: %q", got)
	}
	if !strings.Contains(got, "[redacted]") {
		t.Errorf("Redact should mark what it removed, got %q", got)
	}

	// Short values are not treated as secrets — scrubbing them would mangle
	// ordinary text.
	if p.Redact("a stable string") != "a stable string" {
		t.Error("Redact must not rewrite text that holds no secret")
	}
}

// The env-diff check reports key NAMES; values must stay out of it.
func TestExpectedKeysReportsNamesNotValues(t *testing.T) {
	p := secretProfile(t, newStub(t, nil))
	res := runOne(t, p, "expected env keys")

	if res.Status != StatusFail {
		t.Fatalf("status = %v, want fail (CL_MISSING_KEY is absent)", res.Status)
	}
	if !strings.Contains(res.Detail, "CL_MISSING_KEY") {
		t.Errorf("detail should name the missing key, got %q", res.Detail)
	}
	for _, value := range []string{fakeBrainToken, fakeRouterKey} {
		if strings.Contains(res.Detail, value) {
			t.Error("env-diff detail leaked a token value")
		}
	}
}

// Values from the secrets env file are usable by checks but never printed.
func TestSecretsFileValuesAreUsableButNotPrinted(t *testing.T) {
	dir := fullProfile(t, "A=1\n")
	writeFile(t, dir, ".env.secrets", "CL_BRAIN_API_TOKEN="+fakeSecretsOnly+"\n")
	stub := newStub(t, map[string]stubResponse{"pbrainctl": {out: "ok"}})

	p := load(t, dir, stub)
	res := runOne(t, p, "brain token (memory)")
	if res.Status != StatusOK {
		t.Fatalf("status = %v, want ok — the secrets env file should supply the token", res.Status)
	}
	if strings.Contains(res.Detail, fakeSecretsOnly) {
		t.Error("detail leaked a value from the secrets env file")
	}
}

// A check that returns a raw secret must still be scrubbed by RunChecks. This
// covers the central guard independently of the per-check redaction, so a new
// check that forgets to call Redact cannot leak.
func TestRunChecksRedactsCarelessCheck(t *testing.T) {
	p := secretProfile(t, newStub(t, nil))

	careless := NewCheck("careless", "test", func(*Profile) Result {
		return Result{
			Status: StatusFail,
			Detail: "raw output: " + fakeBrainToken,
			Fix:    "replace " + fakeRouterKey,
		}
	})

	report := RunChecks(p, []Check{careless})
	res := report.Results[0]

	if strings.Contains(res.Detail, fakeBrainToken) {
		t.Errorf("RunChecks failed to redact Detail: %q", res.Detail)
	}
	if strings.Contains(res.Fix, fakeRouterKey) {
		t.Errorf("RunChecks failed to redact Fix: %q", res.Fix)
	}
	if !strings.Contains(res.Detail, "[redacted]") {
		t.Errorf("expected a redaction marker, got %q", res.Detail)
	}
}

func TestIsSecretKey(t *testing.T) {
	secret := []string{"CL_BRAIN_API_TOKEN", "CL_API_KEY", "GITHUB_PAT", "DB_PASSWORD", "MY_SECRET", "AUTH_HEADER"}
	for _, k := range secret {
		if !isSecretKey(k) {
			t.Errorf("isSecretKey(%q) = false, want true", k)
		}
	}
	// A URL, a path, or a plain name is not a secret — redacting these would
	// hide the detail the user needs.
	public := []string{"CL_BRAIN_API", "CL_ROUTER_API", "PATH", "WORKSPACE_HOME", "EDITOR", "GIT_AUTHOR_NAME"}
	for _, k := range public {
		if isSecretKey(k) {
			t.Errorf("isSecretKey(%q) = true, want false", k)
		}
	}
}

// A daemon URL must survive redaction: "unreachable at [redacted]" is useless.
func TestRedactKeepsNonSecretValues(t *testing.T) {
	p := &Profile{Env: map[string]string{
		"CL_BRAIN_API":       "http://127.0.0.1:9998",
		"CL_BRAIN_API_TOKEN": fakeBrainToken,
	}}

	got := p.Redact("brain daemon at http://127.0.0.1:9998 unreachable")
	if !strings.Contains(got, "http://127.0.0.1:9998") {
		t.Errorf("a non-secret URL must not be redacted, got %q", got)
	}
	if strings.Contains(p.Redact("token "+fakeBrainToken), fakeBrainToken) {
		t.Error("a secret-keyed value must still be redacted")
	}
}
