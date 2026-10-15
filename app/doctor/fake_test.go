package doctor

import (
	"context"
	"fmt"
	"strings"
)

type fakeShell struct {
	out  string
	code int
	err  error
}

type fakeProbe struct {
	status int
	err    error
}

// fakeExec keys shell responses on the joined command so a test reads as a
// mapping from "what ran" to "what the host said".
type fakeExec struct {
	shellOut    map[string]fakeShell
	services    []Service
	servicesErr error
	probes      map[string]fakeProbe
}

func (f *fakeExec) Shell(_ context.Context, cmd []string) (string, int, error) {
	key := strings.Join(cmd, " ")
	r, ok := f.shellOut[key]
	if !ok {
		return "", 127, fmt.Errorf("fakeExec: no stub for %q", key)
	}
	return r.out, r.code, r.err
}

func (f *fakeExec) PlatformServices(context.Context) ([]Service, error) {
	return f.services, f.servicesErr
}

func (f *fakeExec) Probe(_ context.Context, url string) (int, error) {
	r, ok := f.probes[url]
	if !ok {
		return 0, fmt.Errorf("fakeExec: no probe stub for %q", url)
	}
	return r.status, r.err
}
