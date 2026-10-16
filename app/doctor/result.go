// Package doctor holds phantom-ink's install health checks.
//
// Two rules shape everything here. First, a check may report StatePass only
// when it positively observed the good state: a probe that could not answer is
// StateUnknown, never a pass and never a fail. This exists because the app has
// shipped the opposite bug — a registry 401 rendering as "no image" — and the
// state model is where that class of defect gets prevented rather than
// remembered. Second, Run never mutates; remediation is a separate, gated call.
//
// The package imports nothing from package main so it stays testable and can be
// lifted into phantom-router later, the same shape as the dora package.
package doctor

import (
	"context"
	"errors"
	"sort"
)

type State string

const (
	StatePass    State = "pass"
	StateWarn    State = "warn"
	StateFail    State = "fail"
	StateUnknown State = "unknown"
)

type Scope string

const (
	ScopeLocal    Scope = "local"
	ScopePlatform Scope = "platform"
)

// Remedy describes how to repair what a check found. Command is always shown so
// the operator can run it themselves; AutoSafe additionally permits Doctor to
// run it. AutoSafe is reserved for remedies that are idempotent, non-destructive
// and need no secret — everything else stays copy-the-command.
type Remedy struct {
	// Command is for the operator to read and copy. It may be prose
	// ("restart the router service"), because plenty of real fixes are not a
	// single shell invocation.
	Command string `json:"command"`
	// Exec is the actual argv Doctor runs, and is required when AutoSafe. It
	// exists because Command is display text: splitting prose on whitespace
	// and executing it is how a "fix" button runs nonsense.
	Exec     []string `json:"-"`
	AutoSafe bool     `json:"auto_safe"`
	Confirm  string   `json:"confirm"`
}

// Validate guards the invariants an auto-runnable remedy must hold: it can tell
// the operator what it is about to do, and it has a real argv to run rather than
// prose. ApplyRemedy calls this before it executes anything, so an unconfirmable
// or unexecutable fix is refused rather than run.
func (r Remedy) Validate() error {
	if r.Command == "" {
		return errors.New("remedy must have a command")
	}
	if r.AutoSafe && r.Confirm == "" {
		return errors.New("auto-safe remedy must have confirm text")
	}
	if r.AutoSafe && len(r.Exec) == 0 {
		return errors.New("auto-safe remedy must have an exec argv")
	}
	return nil
}

type Result struct {
	ID     string  `json:"id"`
	Title  string  `json:"title"`
	Scope  Scope   `json:"scope"`
	State  State   `json:"state"`
	Detail string  `json:"detail"`
	Remedy *Remedy `json:"remedy,omitempty"`
	Docs   string  `json:"docs,omitempty"`
}

// Service is the doctor package's own view of a platform compose service. It
// deliberately duplicates a subset of main.PlatformService rather than importing
// it: main is package main and cannot be imported, and a narrow local type keeps
// this package dependency-free.
type Service struct {
	Name    string
	State   string
	Health  string
	Addr    string
	OneShot bool
}

// Executor is the only way a check touches the outside world. Three capabilities
// rather than one command-shaped Run: platform reads and HTTP probes are not
// shell, and forcing them through a cmd []string signature would be a lie in the
// type. There is deliberately no remote shell — see the spec.
type Executor interface {
	Shell(ctx context.Context, cmd []string) (stdout string, code int, err error)
	PlatformServices(ctx context.Context) ([]Service, error)
	Probe(ctx context.Context, url string) (status int, err error)
}

type Check interface {
	ID() string
	Scope() Scope
	Run(ctx context.Context, ex Executor) Result
}

// statePriority orders results worst-first. Unknown sorts above warn: "I could
// not tell" is more urgent than "I saw something mildly off".
func statePriority(s State) int {
	switch s {
	case StateFail:
		return 0
	case StateUnknown:
		return 1
	case StateWarn:
		return 2
	default:
		return 3
	}
}

// SortResults sorts in place, worst first, ties broken by ID for stability.
func SortResults(rs []Result) {
	sort.SliceStable(rs, func(i, j int) bool {
		pi, pj := statePriority(rs[i].State), statePriority(rs[j].State)
		if pi != pj {
			return pi < pj
		}
		return rs[i].ID < rs[j].ID
	})
}
