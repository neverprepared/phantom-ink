package doctor

import (
	"context"
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
