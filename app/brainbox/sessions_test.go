package brainbox

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// A FIFO must not be opened. `.env.secrets` is a named pipe in the real
// workspace layout -- the profile's .envrc streams 1Password secrets through
// it -- and os.Open on a FIFO blocks until a writer appears, which with no
// writer is forever. This hung the Agent Work panel on a permanent spinner
// with no error, because it blocks before any request is made.
//
// The bug was invisible from a terminal, where a writer is often attached; it
// only reproduced in the GUI app, which is why a user report found it and a
// local test did not.
func TestReadProfileEnvDoesNotBlockOnAFifo(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, ".env"),
		[]byte("REAL_KEY=value\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fifo := filepath.Join(dir, ".env.secrets")
	if err := syscall.Mkfifo(fifo, 0o600); err != nil {
		t.Skipf("cannot create a FIFO here: %v", err)
	}

	done := make(chan map[string]string, 1)
	go func() { done <- ReadProfileEnv(dir) }()

	select {
	case env := <-done:
		if env["REAL_KEY"] != "value" {
			t.Errorf("the regular .env was not read: %v", env)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("ReadProfileEnv blocked on the FIFO; it must skip non-regular files")
	}
}

// The ordinary path must still work.
func TestReadProfileEnvReadsBothRegularFiles(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, ".env"),
		[]byte("# comment\nexport A=1\nB=2\n\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, ".env.secrets"),
		[]byte("C=3\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	env := ReadProfileEnv(dir)
	for k, want := range map[string]string{"A": "1", "B": "2", "C": "3"} {
		if env[k] != want {
			t.Errorf("env[%q] = %q, want %q", k, env[k], want)
		}
	}
}

func TestReadProfileEnvHandlesAMissingDirectory(t *testing.T) {
	if got := ReadProfileEnv(filepath.Join(t.TempDir(), "nope")); len(got) != 0 {
		t.Errorf("got %v, want empty", got)
	}
	if got := ReadProfileEnv(""); len(got) != 0 {
		t.Errorf("got %v, want empty", got)
	}
}
