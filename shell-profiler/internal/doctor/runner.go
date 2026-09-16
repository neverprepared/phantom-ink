package doctor

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// CmdRunner executes an external command and returns its combined output.
//
// Every external tool a check touches (ssh, gh, pbrainctl, prouterctl, aws, az,
// gcloud, direnv) goes through this type so tests can stub it. Implementations
// are responsible for supplying any environment the command needs — callers
// never pass secrets as arguments.
type CmdRunner func(name string, args ...string) (stdout string, err error)

// ErrNotInstalled is returned by a CmdRunner when the binary is not on PATH.
var ErrNotInstalled = fmt.Errorf("command not installed")

// commandTimeout bounds every external command so a hung daemon cannot hang
// the doctor run.
const commandTimeout = 15 * time.Second

// ExecRunner returns a CmdRunner that runs commands for real, in dir, with the
// process environment plus the supplied extra variables.
//
// extraEnv is how token values reach subprocesses: values are held in memory
// and handed to the child process, never rendered into output or arguments.
func ExecRunner(dir string, extraEnv map[string]string) CmdRunner {
	return func(name string, args ...string) (string, error) {
		if _, err := exec.LookPath(name); err != nil {
			return "", fmt.Errorf("%w: %s", ErrNotInstalled, name)
		}

		ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
		defer cancel()

		cmd := exec.CommandContext(ctx, name, args...)
		cmd.Dir = dir
		cmd.Env = os.Environ()
		for k, v := range extraEnv {
			cmd.Env = append(cmd.Env, k+"="+v)
		}
		// Never let a child prompt for input; a blocked prompt looks like a hang.
		cmd.Stdin = nil

		var buf bytes.Buffer
		cmd.Stdout = &buf
		cmd.Stderr = &buf
		err := cmd.Run()
		if ctx.Err() == context.DeadlineExceeded {
			return buf.String(), fmt.Errorf("timed out after %s", commandTimeout)
		}
		return buf.String(), err
	}
}

// unreachableMarkers are substrings that indicate a service is down or
// unreachable rather than a credential being wrong. This is the load-bearing
// distinction between "the user must fix something" (fail) and "something is
// just offline" (skip).
var unreachableMarkers = []string{
	"connection refused",
	"connection reset",
	"no such host",
	"i/o timeout",
	"timed out",
	"deadline exceeded",
	"network is unreachable",
	"host is down",
	"no route to host",
	"could not connect",
	"failed to connect",
	"dial tcp",
	"connect: ",
	"unexpected eof",
	"server misbehaving",
	"temporary failure in name resolution",
	"service unavailable",
	"502 bad gateway",
	"503 service",
	"gateway timeout",
}

// isUnreachable reports whether command output/error describes a service that
// is offline or unreachable (as opposed to a rejected credential).
func isUnreachable(output string, err error) bool {
	hay := strings.ToLower(output)
	if err != nil {
		hay += " " + strings.ToLower(err.Error())
	}
	for _, m := range unreachableMarkers {
		if strings.Contains(hay, m) {
			return true
		}
	}
	return false
}

// isNotInstalled reports whether the failure was "binary missing from PATH".
func isNotInstalled(err error) bool {
	if err == nil {
		return false
	}
	if strings.Contains(err.Error(), ErrNotInstalled.Error()) {
		return true
	}
	return strings.Contains(strings.ToLower(err.Error()), "executable file not found")
}

// firstLine trims command output down to a single short line so details stay
// readable. Command output is external data — it is never assumed to be a
// single line and is length-capped.
func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	s = strings.TrimSpace(s)
	const max = 120
	if len(s) > max {
		s = s[:max] + "…"
	}
	return s
}
