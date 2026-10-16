package doctor

import (
	"context"
	"errors"
	"fmt"
	"strings"
)

type Registry struct {
	checks []Check
	byID   map[string]Check
}

func NewRegistry() *Registry {
	return &Registry{byID: map[string]Check{}}
}

func (r *Registry) Register(c Check) error {
	if _, dup := r.byID[c.ID()]; dup {
		return fmt.Errorf("duplicate check id %q", c.ID())
	}
	r.byID[c.ID()] = c
	r.checks = append(r.checks, c)
	return nil
}

func (r *Registry) All() []Check { return r.checks }

func (r *Registry) ByID(id string) (Check, bool) {
	c, ok := r.byID[id]
	return c, ok
}

// Run executes every check whose scope is requested, sequentially. Sequential is
// deliberate: twelve checks are cheap, and a serial run keeps failure attribution
// obvious. Scope "" runs all of them.
func (r *Registry) Run(ctx context.Context, ex Executor, scope Scope) []Result {
	out := make([]Result, 0, len(r.checks))
	for _, c := range r.checks {
		if scope != "" && c.Scope() != scope {
			continue
		}
		out = append(out, c.Run(ctx, ex))
	}
	SortResults(out)
	return out
}

// ErrNotAutoSafe is returned when a caller asks Doctor to run a remedy that was
// not marked safe. The frontend's "allow fixes" switch only decides whether a
// button is drawn; this is the boundary that actually holds.
var ErrNotAutoSafe = errors.New("remedy is not auto-safe")

// ApplyRemedy runs one check's remedy, then re-runs the check and returns its
// fresh result. The returned Result is always the post-fix observation, so a
// remedy that did not work shows as still failing rather than flipping green.
func (r *Registry) ApplyRemedy(ctx context.Context, ex Executor, id string) (Result, error) {
	c, ok := r.ByID(id)
	if !ok {
		return Result{}, fmt.Errorf("unknown check %q", id)
	}
	pre := c.Run(ctx, ex)
	if pre.Remedy == nil {
		return pre, fmt.Errorf("check %q offers no remedy", id)
	}
	if !pre.Remedy.AutoSafe {
		return pre, ErrNotAutoSafe
	}
	if err := pre.Remedy.Validate(); err != nil {
		return pre, err
	}
	out, code, err := ex.Shell(ctx, pre.Remedy.Exec)
	if err != nil {
		// The remedy could not be run at all (no such binary, context
		// cancelled). There is nothing new to observe, so hand back the
		// pre-fix result unchanged.
		return pre, fmt.Errorf("remedy failed: %w", err)
	}
	if code != 0 {
		// Executor.Shell reports a non-zero exit as (out, code, nil) by
		// deliberate contract — a non-zero exit is an observation, not a
		// failure to observe. Everywhere else downstream that is exactly
		// right; here it is not. A remedy that RAN AND WAS REJECTED
		// ("Permission denied", "command not found") must not be
		// indistinguishable from one that succeeded, or the operator sees
		// only "still failing" and never learns the fix itself was refused.
		// The re-run result is still returned alongside the error, so the
		// panel shows what is true now as well as why the fix did not take.
		return c.Run(ctx, ex), fmt.Errorf("remedy exited %d: %s", code, strings.TrimSpace(out))
	}
	return c.Run(ctx, ex), nil
}
