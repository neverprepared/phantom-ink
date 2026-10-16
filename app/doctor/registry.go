package doctor

import (
	"context"
	"errors"
	"fmt"
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
	if _, _, err := ex.Shell(ctx, pre.Remedy.Exec); err != nil {
		return pre, fmt.Errorf("remedy failed: %w", err)
	}
	return c.Run(ctx, ex), nil
}
