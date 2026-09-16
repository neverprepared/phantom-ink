// Package doctor implements the health-check framework behind the
// `shell-profiler doctor` command.
//
// A Check inspects one aspect of a profile and returns a Result. Checks never
// touch the process environment directly and never shell out on their own:
// everything external goes through the injectable CmdRunner on Profile, which
// makes every check unit-testable without live services.
//
// Secret hygiene: a Result must never carry a token value. Checks report only
// set/missing/valid/invalid. See formatSecret helpers and the tests in
// secrets_test.go which assert that no value ever reaches rendered output.
package doctor

// Status is the outcome of a single check.
type Status string

const (
	// StatusOK means the checked thing is present and working.
	StatusOK Status = "ok"
	// StatusFail means the user has something to fix (missing/invalid config).
	StatusFail Status = "fail"
	// StatusSkip means the check could not be evaluated — either it does not
	// apply to this profile, or a remote service is offline. Skips never fail
	// the run: the user cannot fix a daemon that is simply not running.
	StatusSkip Status = "skip"
)

// Result is the outcome of running a Check against a profile.
type Result struct {
	Name     string `json:"name"`
	Category string `json:"category"`
	Status   Status `json:"status"`
	Detail   string `json:"detail,omitempty"`
	// Fix is a short, actionable hint. Only meaningful for StatusFail.
	Fix string `json:"fix,omitempty"`
}

// Check inspects one aspect of a profile.
type Check interface {
	Name() string
	Category() string
	Run(p *Profile) Result
}

// checkFunc adapts a plain function into a Check.
type checkFunc struct {
	name     string
	category string
	fn       func(p *Profile) Result
}

func (c checkFunc) Name() string     { return c.name }
func (c checkFunc) Category() string { return c.category }
func (c checkFunc) Run(p *Profile) Result {
	r := c.fn(p)
	r.Name = c.name
	r.Category = c.category
	return r
}

// NewCheck builds a Check from a function. Name and Category are stamped onto
// the Result automatically, so check bodies only fill in Status/Detail/Fix.
func NewCheck(name, category string, fn func(p *Profile) Result) Check {
	return checkFunc{name: name, category: category, fn: fn}
}

func ok(detail string) Result   { return Result{Status: StatusOK, Detail: detail} }
func skip(detail string) Result { return Result{Status: StatusSkip, Detail: detail} }
func fail(detail, fix string) Result {
	return Result{Status: StatusFail, Detail: detail, Fix: fix}
}
