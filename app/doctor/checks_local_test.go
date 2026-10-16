package doctor

import (
	"context"
	"errors"
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
