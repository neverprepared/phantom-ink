package doctor

import (
	"context"
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
