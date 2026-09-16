package doctor

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// stubResponse is a canned reply for one external command.
type stubResponse struct {
	out string
	err error
}

// stubRunner records calls and replies from a table keyed by the command line
// ("ssh -T git@github.com"). A prefix match is used so tests can key on the
// binary alone. Unmatched commands report as not installed, which keeps checks
// in their "cannot evaluate" branch rather than failing spuriously.
type stubRunner struct {
	t         *testing.T
	responses map[string]stubResponse
	calls     []string
	// env captures the child environment the runner was built with.
	env map[string]string
}

func newStub(t *testing.T, responses map[string]stubResponse) *stubRunner {
	t.Helper()
	return &stubRunner{t: t, responses: responses}
}

// factory returns a runner constructor suitable for LoadProfile.
func (s *stubRunner) factory() func(dir string, env map[string]string) CmdRunner {
	return func(dir string, env map[string]string) CmdRunner {
		s.env = env
		return func(name string, args ...string) (string, error) {
			line := strings.TrimSpace(name + " " + strings.Join(args, " "))
			s.calls = append(s.calls, line)
			if resp, found := s.responses[line]; found {
				return resp.out, resp.err
			}
			for key, resp := range s.responses {
				if strings.HasPrefix(line, key) {
					return resp.out, resp.err
				}
			}
			return "", fmt.Errorf("%w: %s", ErrNotInstalled, name)
		}
	}
}

func (s *stubRunner) called(prefix string) bool {
	for _, c := range s.calls {
		if strings.HasPrefix(c, prefix) {
			return true
		}
	}
	return false
}

// writeFile writes a file inside dir, creating parents.
func writeFile(t *testing.T, dir, rel, content string) {
	t.Helper()
	path := filepath.Join(dir, rel)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

// runOne runs a single named check against a profile and returns its Result.
func runOne(t *testing.T, p *Profile, name string) Result {
	t.Helper()
	for _, c := range DefaultChecks() {
		if c.Name() == name {
			return c.Run(p)
		}
	}
	t.Fatalf("no check named %q in the catalog", name)
	return Result{}
}

// errFailed is a generic non-zero-exit error for stubs.
var errFailed = fmt.Errorf("exit status 1")

// timeoutAfter is the guard used by tests that must not block.
func timeoutAfter() <-chan time.Time { return time.After(3 * time.Second) }
