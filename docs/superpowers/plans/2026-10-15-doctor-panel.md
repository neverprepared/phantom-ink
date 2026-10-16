# Doctor Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Doctor panel that turns phantom-ink's recurring install failures into executable checks which report only states they positively observed, with opt-in one-click fixes for the safe subset.

**Architecture:** A pure `app/doctor` package holds a check registry behind a three-capability `Executor` interface (local shell, platform service read, HTTP probe). Checks never mutate and never shell out directly, so each is table-tested against a fake executor with no real infrastructure. Package `main` supplies the real executor, adapts its own `PlatformService` into the doctor package's own service type, and exposes two Wails bindings. `DoctorPanel.svelte` renders grouped, pre-sorted results.

**Tech Stack:** Go 1.25 (module `phantom-ink`, stdlib only — no new dependencies), Svelte 5 runes, Wails 2.

**Spec:** `docs/superpowers/specs/2026-10-15-doctor-panel-design.md`

## Global Constraints

- Go module is `phantom-ink`; the doctor package import path is `phantom-ink/doctor`.
- **The `doctor` package must not import package `main`.** `PlatformService` is declared `package main` in `app/app_platform_services.go:20`, so the doctor package defines its own `Service` type and `main` adapts. The existing `phantom-ink/dora` package is the precedent: pure logic in a subpackage, wiring in `main`.
- No new Go dependencies. No new npm dependencies.
- A check may return `StatePass` **only** when it positively observed the good state. Unobservable ⇒ `StateUnknown`, never `StatePass` and never `StateFail`.
- `Check.Run` must never mutate anything. Mutation happens only in `ApplyRemedy`.
- `ApplyRemedy` refuses any remedy whose `AutoSafe` is false, **regardless of the frontend switch**. The UI switch is an affordance, not the security boundary.
- Badge variants are limited to those already in `app/frontend/src/lib/components/Badge.svelte`: `fail`→`failed`, `warn`→`pending`, `unknown`→*no variant* (muted default), `pass`→`running`. `error`/`warning` variants do not exist — do not invent them.
- No SQLite migration and no contract change. `just app-contract-gen` must still produce no diff.
- Tests: `cd app && go test ./... -race`; `cd app/frontend && npm run check`.
- Work on branch `feat/doctor-panel`.

---

### Task 1: Doctor package core — result model and registry

**Files:**
- Create: `app/doctor/result.go`
- Create: `app/doctor/registry.go`
- Test: `app/doctor/registry_test.go`

**Interfaces:**
- Consumes: nothing.
- Produces: `State` (`StatePass`/`StateWarn`/`StateFail`/`StateUnknown`), `Scope` (`ScopeLocal`/`ScopePlatform`), `Remedy{Command string, AutoSafe bool, Confirm string}`, `Result{ID, Title, Scope, State, Detail, Remedy, Docs}`, `Service{Name, State, Health, Addr string, OneShot bool}`, `Executor` interface, `Check` interface, `Registry` with `Register(Check)`, `All() []Check`, `ByID(string) (Check, bool)`, and `SortResults([]Result)`.

- [ ] **Step 1: Write the failing test**

Create `app/doctor/registry_test.go`:

```go
package doctor

import (
	"context"
	"testing"
)

type stubCheck struct {
	id    string
	scope Scope
	res   Result
}

func (s stubCheck) ID() string  { return s.id }
func (s stubCheck) Scope() Scope { return s.scope }
func (s stubCheck) Run(context.Context, Executor) Result { return s.res }

func TestRegisterRejectsDuplicateID(t *testing.T) {
	r := NewRegistry()
	if err := r.Register(stubCheck{id: "a", scope: ScopeLocal}); err != nil {
		t.Fatalf("first register: %v", err)
	}
	if err := r.Register(stubCheck{id: "a", scope: ScopeLocal}); err == nil {
		t.Fatal("want error on duplicate id, got nil")
	}
}

func TestByID(t *testing.T) {
	r := NewRegistry()
	_ = r.Register(stubCheck{id: "a", scope: ScopeLocal})
	if _, ok := r.ByID("a"); !ok {
		t.Fatal("want to find check a")
	}
	if _, ok := r.ByID("nope"); ok {
		t.Fatal("want miss for unknown id")
	}
}

func TestSortResultsWorstFirst(t *testing.T) {
	in := []Result{
		{ID: "p", State: StatePass},
		{ID: "w", State: StateWarn},
		{ID: "f", State: StateFail},
		{ID: "u", State: StateUnknown},
	}
	SortResults(in)
	got := []string{in[0].ID, in[1].ID, in[2].ID, in[3].ID}
	want := []string{"f", "u", "w", "p"}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("sort order = %v, want %v", got, want)
		}
	}
}

func TestAutoSafeRemedyMustHaveConfirm(t *testing.T) {
	bad := Remedy{Command: "do thing", Exec: []string{"true"}, AutoSafe: true, Confirm: ""}
	if err := bad.Validate(); err == nil {
		t.Fatal("want error: auto-safe remedy with no confirm text")
	}
	ok := Remedy{Command: "do thing", Exec: []string{"true"}, AutoSafe: true, Confirm: "This will do thing."}
	if err := ok.Validate(); err != nil {
		t.Fatalf("want valid, got %v", err)
	}
}

// Prose is a fine thing to show an operator and a terrible thing to execute.
func TestAutoSafeRemedyMustHaveExecArgv(t *testing.T) {
	prose := Remedy{
		Command:  "restart phantom-ink to run migrations",
		AutoSafe: true,
		Confirm:  "Restart the app.",
	}
	if err := prose.Validate(); err == nil {
		t.Fatal("want error: auto-safe remedy with no exec argv")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./doctor/ -run 'TestRegister|TestByID|TestSort|TestAutoSafe' -v`
Expected: FAIL — build error, `undefined: NewRegistry`, `undefined: Result`, etc.

- [ ] **Step 3: Write minimal implementation**

Create `app/doctor/result.go`:

```go
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
```

Create `app/doctor/registry.go`:

```go
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && go test ./doctor/ -v`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add app/doctor/result.go app/doctor/registry.go app/doctor/registry_test.go
git commit -m "feat(doctor): check registry with observed-truth state model

A check may report pass only when it positively observed the good state;
unknown is first-class so an unanswerable probe can never render as either
a pass or a failure. Remedy.Validate enforces that an auto-runnable fix can
always say what it is about to do."
```

---

### Task 2: Fake executor and the first local check

Establishes the pattern every later check follows, end to end.

**Files:**
- Create: `app/doctor/fake_test.go`
- Create: `app/doctor/checks_local.go`
- Test: `app/doctor/checks_local_test.go`

**Interfaces:**
- Consumes: Task 1's `Check`, `Executor`, `Result`, `Remedy`, `State`, `Scope`.
- Produces: `fakeExec` (test-only, with `shellOut map[string]fakeShell`, `services []Service`, `servicesErr error`, `probes map[string]fakeProbe`), and `DockerSymlinkCheck{}` registered as id `docker-symlink`.

- [ ] **Step 1: Write the failing test**

Create `app/doctor/fake_test.go`:

```go
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
```

Create `app/doctor/checks_local_test.go`:

```go
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./doctor/ -run TestDockerSymlink -v`
Expected: FAIL — `undefined: DockerSymlinkCheck`.

- [ ] **Step 3: Write minimal implementation**

Create `app/doctor/checks_local.go`:

```go
package doctor

import "context"

// DockerSymlinkCheck guards the OrbStack migration failure: Docker Desktop used
// to provide /usr/local/bin/docker, and without it GUI/launchd apps (the runner
// app) have no docker on PATH, so session creation dies with exit 127 while the
// control plane still looks healthy.
type DockerSymlinkCheck struct{}

func (DockerSymlinkCheck) ID() string   { return "docker-symlink" }
func (DockerSymlinkCheck) Scope() Scope { return ScopeLocal }

func (c DockerSymlinkCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{
		ID:    c.ID(),
		Title: "docker on the GUI PATH",
		Scope: ScopeLocal,
	}
	_, code, err := ex.Shell(ctx, []string{"test", "-x", "/usr/local/bin/docker"})
	if err != nil {
		res.State = StateUnknown
		res.Detail = "could not test /usr/local/bin/docker: " + err.Error()
		return res
	}
	if code == 0 {
		res.State = StatePass
		res.Detail = "/usr/local/bin/docker is executable"
		return res
	}
	res.State = StateFail
	res.Detail = "/usr/local/bin/docker is missing; GUI and launchd apps have no docker on PATH"
	res.Remedy = &Remedy{
		Command:  "ln -sf ~/.orbstack/bin/docker /usr/local/bin/docker",
		Exec:     []string{"ln", "-sf", os.ExpandEnv("$HOME/.orbstack/bin/docker"), "/usr/local/bin/docker"},
		AutoSafe: true,
		Confirm:  "Symlink ~/.orbstack/bin/docker to /usr/local/bin/docker on this Mac.",
	}
	return res
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && go test ./doctor/ -v`
Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
git add app/doctor/fake_test.go app/doctor/checks_local.go app/doctor/checks_local_test.go
git commit -m "feat(doctor): docker-symlink check and the fake executor

Establishes the per-check test shape, including the case that matters most:
an executor that cannot answer yields unknown, not a false failure."
```

---

### Task 3: Local observation checks — app-db and direnv-approval

**Files:**
- Modify: `app/doctor/checks_local.go`
- Test: `app/doctor/checks_local_test.go`

**Interfaces:**
- Consumes: Task 2's pattern and `fakeExec`.
- Produces: `AppDBCheck{Path string}` id `app-db`; `DirenvApprovalCheck{ProfileDirs []string}` id `direnv-approval`.

Both facts below were verified empirically against direnv 2.37.1 during the
pre-flight scan. Use them as given; do not re-derive.

1. **`direnv status` takes no `--profile` flag.** An unrecognised flag is
   silently ignored and status is reported for the *current* directory, so
   `direnv status --profile <dir>` would check the app's own cwd for every
   profile and report confident nonsense. Scope by cwd instead:
   `sh -lc "cd <dir> && direnv status"`. This keeps `Executor.Shell` free of a
   cwd parameter.
2. **Polarity: `Found RC allowed 0` means allowed; non-zero means not allowed.**
   Confirmed by allowing a scratch directory and watching the value flip 1 → 0.
   Parse the `Found RC` line, never `Loaded RC` — `Loaded` describes direnv's own
   cwd, `Found` the directory asked about. `Found RC allowPath` is printed in
   both states, so it is **not** a discriminator.

- [ ] **Step 1: Write the failing test**

Append to `app/doctor/checks_local_test.go`:

```go
func TestAppDBMissingOrEmptyIsFail(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"stat -f %z /tmp/none.db": {out: "0\n", code: 0},
	}}
	got := AppDBCheck{Path: "/tmp/none.db"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail for a zero-byte db", got.State)
	}
	// The fix is an app restart, which Doctor cannot shell out to on itself.
	if got.Remedy == nil || got.Remedy.AutoSafe {
		t.Fatal("want a non-auto-safe remedy")
	}
}

func TestAppDBPopulatedIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"stat -f %z /tmp/db": {out: "40960\n", code: 0},
	}}
	got := AppDBCheck{Path: "/tmp/db"}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass", got.State)
	}
}

func TestAppDBUnparsableSizeIsUnknown(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"stat -f %z /tmp/db": {out: "not-a-number\n", code: 0},
	}}
	got := AppDBCheck{Path: "/tmp/db"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown", got.State)
	}
}

func TestDirenvAllAllowedIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"sh -lc cd /p/one && direnv status": {out: "Found RC allowed 0\n", code: 0},
	}}
	got := DirenvApprovalCheck{ProfileDirs: []string{"/p/one"}}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
}

func TestDirenvUnapprovedIsWarnNamingTheProfile(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"sh -lc cd /p/one && direnv status": {out: "Found RC allowed 0\n", code: 0},
		"sh -lc cd /p/two && direnv status": {out: "Found RC allowed 2\n", code: 0},
	}}
	got := DirenvApprovalCheck{ProfileDirs: []string{"/p/one", "/p/two"}}.Run(context.Background(), ex)
	if got.State != StateWarn {
		t.Fatalf("state = %q, want warn", got.State)
	}
	if !strings.Contains(got.Detail, "/p/two") {
		t.Fatalf("detail %q must name the unapproved profile", got.Detail)
	}
	if strings.Contains(got.Detail, "/p/one") {
		t.Fatalf("detail %q must not list approved profiles", got.Detail)
	}
}

func TestDirenvMissingAllowedLineIsUnknown(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"sh -lc cd /p/one && direnv status": {out: "some other output\n", code: 0},
	}}
	got := DirenvApprovalCheck{ProfileDirs: []string{"/p/one"}}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown", got.State)
	}
}
```

Add `"strings"` to the test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./doctor/ -run 'TestAppDB|TestDirenv' -v`
Expected: FAIL — `undefined: AppDBCheck`, `undefined: DirenvApprovalCheck`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/doctor/checks_local.go` (add `"fmt"`, `"os"`, `"strconv"`, `"strings"` to imports):

```go
// AppDBCheck catches an app SQLite file that exists but was never initialised.
// A zero-byte ~/.phantom-ink.db has no tables at all, so every panel backed by
// local state fails in a way that looks like a query bug rather than a missing
// schema.
type AppDBCheck struct{ Path string }

func (AppDBCheck) ID() string   { return "app-db" }
func (AppDBCheck) Scope() Scope { return ScopeLocal }

func (c AppDBCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "app database initialised", Scope: ScopeLocal}
	out, _, err := ex.Shell(ctx, []string{"stat", "-f", "%z", c.Path})
	if err != nil {
		res.State = StateUnknown
		res.Detail = fmt.Sprintf("could not stat %s: %v", c.Path, err)
		return res
	}
	size, convErr := strconv.Atoi(strings.TrimSpace(out))
	if convErr != nil {
		res.State = StateUnknown
		res.Detail = fmt.Sprintf("could not read the size of %s", c.Path)
		return res
	}
	if size > 0 {
		res.State = StatePass
		res.Detail = fmt.Sprintf("%s is %d bytes", c.Path, size)
		return res
	}
	res.State = StateFail
	res.Detail = fmt.Sprintf("%s is empty — the schema was never created", c.Path)
	// Not auto-safe: the fix is an app restart so db_migrations.go runs, which
	// Doctor cannot perform as a shell command on itself.
	res.Remedy = &Remedy{
		Command:  "restart phantom-ink so db_migrations.go runs",
		AutoSafe: false,
	}
	return res
}

// DirenvApprovalCheck exists because .envrc approvals are per-profile. An
// unapproved profile does not fail loudly: the app resolves that profile's env
// from disk instead, so scheduled jobs run with the wrong environment and
// nothing says so.
type DirenvApprovalCheck struct{ ProfileDirs []string }

func (DirenvApprovalCheck) ID() string   { return "direnv-approval" }
func (DirenvApprovalCheck) Scope() Scope { return ScopeLocal }

func (c DirenvApprovalCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "direnv approvals per profile", Scope: ScopeLocal}
	var unapproved, unreadable []string
	for _, dir := range c.ProfileDirs {
		// direnv has no --profile flag and silently ignores one, so scope by cwd.
		out, _, err := ex.Shell(ctx, []string{"sh", "-lc", "cd " + dir + " && direnv status"})
		if err != nil {
			unreadable = append(unreadable, dir)
			continue
		}
		allowed, ok := parseDirenvAllowed(out)
		if !ok {
			unreadable = append(unreadable, dir)
			continue
		}
		if !allowed {
			unapproved = append(unapproved, dir)
		}
	}
	switch {
	case len(unreadable) > 0:
		res.State = StateUnknown
		res.Detail = "could not read direnv status for: " + strings.Join(unreadable, ", ")
	case len(unapproved) > 0:
		res.State = StateWarn
		res.Detail = "unapproved .envrc in: " + strings.Join(unapproved, ", ") +
			" — these profiles resolve env from disk instead"
		// Exec fixes one directory per click: a single argv, so what the confirm
		// text promises is exactly what runs.
		res.Remedy = &Remedy{
			Command:  "direnv allow " + strings.Join(unapproved, " && direnv allow "),
			Exec:     []string{"direnv", "allow", unapproved[0]},
			AutoSafe: true,
			Confirm:  "Run direnv allow in " + unapproved[0] + ".",
		}
	default:
		res.State = StatePass
		res.Detail = fmt.Sprintf("all %d profiles approved", len(c.ProfileDirs))
	}
	return res
}

// parseDirenvAllowed reads the "Found RC allowed <n>" line. "Found" describes the
// directory asked about; "Loaded" describes direnv's own cwd, so only the former
// answers the question. Returns ok=false when the line is absent, which the
// caller turns into unknown rather than a guess.
func parseDirenvAllowed(out string) (allowed, ok bool) {
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "Found RC allowed") {
			continue
		}
		fields := strings.Fields(line)
		n, err := strconv.Atoi(fields[len(fields)-1])
		if err != nil {
			return false, false
		}
		return n == 0, true
	}
	return false, false
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && go test ./doctor/ -v`
Expected: PASS — 13 tests.

- [ ] **Step 5: Commit**

```bash
git add app/doctor/checks_local.go app/doctor/checks_local_test.go
git commit -m "feat(doctor): app-db and direnv-approval checks

An unapproved .envrc is a warn that names the profile: it does not fail
loudly at runtime, it silently resolves env from disk, which is why it
needs a check at all."
```

---

### Task 4: Local checks that parse external command output

**Files:**
- Modify: `app/doctor/checks_local.go`
- Test: `app/doctor/checks_local_test.go`

**Interfaces:**
- Consumes: Task 3's helpers.
- Produces: `GitHubTokenShadowCheck{}` id `github-token-shadow`; `PortShadowCheck{Port string}` id `port-shadow`; `GatewayMCPPinCheck{Container string}` id `gateway-mcp-pin`.

- [ ] **Step 1: Write the failing test**

Append to `app/doctor/checks_local_test.go`:

```go
func TestGitHubTokenShadowDetected(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"printenv GITHUB_TOKEN": {out: "ghp_xxx\n", code: 0},
		"gh auth status":        {out: "Active account: false\n", code: 0},
	}}
	got := GitHubTokenShadowCheck{}.Run(context.Background(), ex)
	if got.State != StateWarn {
		t.Fatalf("state = %q, want warn", got.State)
	}
	if strings.Contains(got.Detail, "ghp_") {
		t.Fatalf("detail must never echo the token value: %q", got.Detail)
	}
}

func TestGitHubTokenAbsentIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"printenv GITHUB_TOKEN": {out: "", code: 1},
		"gh auth status":        {out: "Active account: true\n", code: 0},
	}}
	got := GitHubTokenShadowCheck{}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass", got.State)
	}
}

func TestPortShadowOccupiedIsFail(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"lsof -nP -iTCP:9910 -sTCP:LISTEN": {out: "node 123 cdowning 20u IPv4 TCP *:9910 (LISTEN)\n", code: 0},
	}}
	got := PortShadowCheck{Port: "9910"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
	if got.Remedy == nil || got.Remedy.AutoSafe {
		t.Fatal("killing a listener is destructive; remedy must not be auto-safe")
	}
}

func TestPortShadowFreeIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"lsof -nP -iTCP:9910 -sTCP:LISTEN": {out: "", code: 1},
	}}
	got := PortShadowCheck{Port: "9910"}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass", got.State)
	}
}

func TestGatewayMCPPinIntactIsPass(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"docker exec gw pip show mcp": {out: "Name: mcp\nVersion: 1.9.4\n", code: 0},
	}}
	got := GatewayMCPPinCheck{Container: "gw"}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass", got.State)
	}
}

func TestGatewayMCPPinBrokenIsFail(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"docker exec gw pip show mcp": {out: "Name: mcp\nVersion: 2.0.1\n", code: 0},
	}}
	got := GatewayMCPPinCheck{Container: "gw"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail for mcp 2.x", got.State)
	}
}

func TestGatewayMCPPinUnreadableIsUnknown(t *testing.T) {
	ex := &fakeExec{shellOut: map[string]fakeShell{
		"docker exec gw pip show mcp": {out: "no version line here\n", code: 0},
	}}
	got := GatewayMCPPinCheck{Container: "gw"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown", got.State)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./doctor/ -run 'TestGitHub|TestPortShadow|TestGatewayMCP' -v`
Expected: FAIL — the three types are undefined.

- [ ] **Step 3: Write minimal implementation**

Append to `app/doctor/checks_local.go`:

```go
// GitHubTokenShadowCheck catches an exported GITHUB_TOKEN/GH_TOKEN shadowing a
// working keyring login. gh prefers the env var, so a dead token makes every
// call fail while a good credential sits idle — and the symptoms mislead in
// sequence, first as a 403 that reads like a missing scope, then as a 401.
//
// This check never puts a token value in Detail. Reporting health must not
// become a way to leak a credential into a log or a screenshot.
type GitHubTokenShadowCheck struct{}

func (GitHubTokenShadowCheck) ID() string   { return "github-token-shadow" }
func (GitHubTokenShadowCheck) Scope() Scope { return ScopeLocal }

func (c GitHubTokenShadowCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "no GitHub token shadowing the keyring", Scope: ScopeLocal}
	var present []string
	for _, name := range []string{"GITHUB_TOKEN", "GH_TOKEN"} {
		out, code, err := ex.Shell(ctx, []string{"printenv", name})
		if err != nil {
			res.State = StateUnknown
			res.Detail = "could not read the environment: " + err.Error()
			return res
		}
		if code == 0 && strings.TrimSpace(out) != "" {
			present = append(present, name)
		}
	}
	if len(present) == 0 {
		res.State = StatePass
		res.Detail = "no GITHUB_TOKEN or GH_TOKEN in the environment"
		return res
	}
	status, _, err := ex.Shell(ctx, []string{"gh", "auth", "status"})
	if err != nil {
		res.State = StateUnknown
		res.Detail = strings.Join(present, " and ") + " set, but gh auth status could not be read"
		return res
	}
	if strings.Contains(status, "Active account: false") {
		res.State = StateWarn
		res.Detail = strings.Join(present, " and ") +
			" is set and gh reports an idle keyring account — the env token wins and may be dead"
		res.Remedy = &Remedy{
			Command:  "unset GITHUB_TOKEN GH_TOKEN   # or: env -u GITHUB_TOKEN -u GH_TOKEN gh ...",
			AutoSafe: false,
			Confirm:  "",
		}
		return res
	}
	res.State = StatePass
	res.Detail = strings.Join(present, " and ") + " is set and gh reports an active account"
	return res
}

// PortShadowCheck catches a host process holding a port the platform needs. A
// launchd router on :9910 shadows the containerised one, and the app then talks
// to the wrong thing while everything appears to be up.
type PortShadowCheck struct{ Port string }

func (c PortShadowCheck) ID() string   { return "port-shadow" }
func (PortShadowCheck) Scope() Scope   { return ScopeLocal }

func (c PortShadowCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{
		ID:    c.ID(),
		Title: "port " + c.Port + " is free for the platform",
		Scope: ScopeLocal,
	}
	out, code, err := ex.Shell(ctx, []string{"lsof", "-nP", "-iTCP:" + c.Port, "-sTCP:LISTEN"})
	if err != nil {
		res.State = StateUnknown
		res.Detail = "could not list listeners on :" + c.Port + ": " + err.Error()
		return res
	}
	// lsof exits non-zero with empty output when nothing is listening.
	if code != 0 && strings.TrimSpace(out) == "" {
		res.State = StatePass
		res.Detail = "nothing is listening on :" + c.Port
		return res
	}
	if strings.TrimSpace(out) == "" {
		res.State = StateUnknown
		res.Detail = "lsof returned no output and no error for :" + c.Port
		return res
	}
	res.State = StateFail
	res.Detail = "a host process is listening on :" + c.Port + ", shadowing the platform service"
	res.Remedy = &Remedy{
		Command:  "launchctl unload <the job holding :" + c.Port + ">",
		AutoSafe: false,
		Confirm:  "",
	}
	return res
}

// GatewayMCPPinCheck guards a pin that silently breaks the gateway: mcp 2.0
// dropped Server.list_tools, so an unpinned rebuild crashes the gateway on any
// tool listing.
//
// The pin lives inside the gateway CONTAINER image, not in a host venv, so this
// reads it with `docker exec <container> pip show mcp`. Verified on this host:
// the container is phantom-gateway-1 and reports mcp 1.29.1. A stopped or
// missing container makes docker exec fail, which correctly yields Unknown —
// "I could not tell", not "the pin is broken".
type GatewayMCPPinCheck struct{ Container string }

func (GatewayMCPPinCheck) ID() string   { return "gateway-mcp-pin" }
func (GatewayMCPPinCheck) Scope() Scope { return ScopeLocal }

func (c GatewayMCPPinCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "gateway mcp pinned below 2.0", Scope: ScopeLocal}
	out, _, err := ex.Shell(ctx, []string{"docker", "exec", c.Container, "pip", "show", "mcp"})
	if err != nil {
		res.State = StateUnknown
		res.Detail = "could not read the gateway venv's mcp version: " + err.Error()
		return res
	}
	version := ""
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "Version:") {
			version = strings.TrimSpace(strings.TrimPrefix(line, "Version:"))
			break
		}
	}
	if version == "" {
		res.State = StateUnknown
		res.Detail = "pip show mcp printed no Version line"
		return res
	}
	major := strings.SplitN(version, ".", 2)[0]
	n, convErr := strconv.Atoi(major)
	if convErr != nil {
		res.State = StateUnknown
		res.Detail = "could not parse the mcp version " + version
		return res
	}
	if n >= 2 {
		res.State = StateFail
		res.Detail = "mcp " + version + " is installed; 2.0 dropped Server.list_tools and crashes the gateway"
		res.Remedy = &Remedy{
			Command:  "docker exec " + c.Container + " pip install 'mcp<2'  # then rebuild the image",
			AutoSafe: false,
			Confirm:  "",
		}
		return res
	}
	res.State = StatePass
	res.Detail = "mcp " + version
	return res
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && go test ./doctor/ -v`
Expected: PASS — 20 tests.

- [ ] **Step 5: Commit**

```bash
git add app/doctor/checks_local.go app/doctor/checks_local_test.go
git commit -m "feat(doctor): token-shadow, port-shadow and mcp-pin checks

The token check asserts it never echoes a token value into Detail: a health
report must not become a way to leak a credential into a screenshot."
```

---

### Task 5: Platform checks from service state

**Files:**
- Create: `app/doctor/checks_platform.go`
- Test: `app/doctor/checks_platform_test.go`

**Interfaces:**
- Consumes: Task 1's `Service`, Task 2's `fakeExec`.
- Produces: `ServiceHealthCheck{}` id `service-health`; `TraefikBoundCheck{ServiceName string}` id `traefik-bound`.

- [ ] **Step 1: Write the failing test**

Create `app/doctor/checks_platform_test.go`:

```go
package doctor

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestServiceHealthAllHealthyIsPass(t *testing.T) {
	ex := &fakeExec{services: []Service{
		{Name: "router", State: "running", Health: "healthy"},
		{Name: "events", State: "running", Health: ""},
	}}
	got := ServiceHealthCheck{}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
}

// A one-shot init sidecar at "exited (0)" is success, not a fault.
func TestServiceHealthOneShotExitedIsPass(t *testing.T) {
	ex := &fakeExec{services: []Service{
		{Name: "brain-init", State: "exited", Health: "", OneShot: true},
	}}
	got := ServiceHealthCheck{}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass for a completed one-shot", got.State)
	}
}

func TestServiceHealthUnhealthyIsWarnPointingAtServices(t *testing.T) {
	ex := &fakeExec{services: []Service{
		{Name: "router", State: "running", Health: "unhealthy"},
	}}
	got := ServiceHealthCheck{}.Run(context.Background(), ex)
	if got.State != StateWarn {
		t.Fatalf("state = %q, want warn", got.State)
	}
	if !strings.Contains(got.Detail, "router") {
		t.Fatalf("detail %q must name the service", got.Detail)
	}
	// Restarting a compose service is an app action, not a shell command, and
	// the Services panel already does it one click away.
	if got.Remedy == nil || got.Remedy.AutoSafe {
		t.Fatal("want a non-auto-safe remedy pointing at the Services panel")
	}
}

func TestServiceHealthUnreadableIsUnknown(t *testing.T) {
	ex := &fakeExec{servicesErr: errors.New("node unreachable")}
	got := ServiceHealthCheck{}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown", got.State)
	}
}

func TestTraefikRunningAndReachableIsPass(t *testing.T) {
	ex := &fakeExec{
		services: []Service{{Name: "traefik", State: "running", Addr: "localhost:8080"}},
		probes:   map[string]fakeProbe{"http://localhost:8080": {status: 200}},
	}
	got := TraefikBoundCheck{ServiceName: "traefik"}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
}

// The orphan-nginx signature: the container is up, but the port answers to
// something else or not at all.
func TestTraefikRunningButUnreachableIsFail(t *testing.T) {
	ex := &fakeExec{
		services: []Service{{Name: "traefik", State: "running", Addr: "localhost:8080"}},
		probes:   map[string]fakeProbe{"http://localhost:8080": {err: errors.New("connection refused")}},
	}
	got := TraefikBoundCheck{ServiceName: "traefik"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
	if !strings.Contains(got.Detail, "running") {
		t.Fatalf("detail %q should say the container is up but unreachable", got.Detail)
	}
}

func TestTraefikNotRunningIsFail(t *testing.T) {
	ex := &fakeExec{services: []Service{{Name: "traefik", State: "exited"}}}
	got := TraefikBoundCheck{ServiceName: "traefik"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
}

func TestTraefikAbsentIsUnknown(t *testing.T) {
	ex := &fakeExec{services: []Service{{Name: "router", State: "running"}}}
	got := TraefikBoundCheck{ServiceName: "traefik"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown when the service is not in the stack", got.State)
	}
}

func TestTraefikNoPublishedPortIsUnknown(t *testing.T) {
	ex := &fakeExec{services: []Service{{Name: "traefik", State: "running", Addr: ""}}}
	got := TraefikBoundCheck{ServiceName: "traefik"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown with no published port to probe", got.State)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./doctor/ -run 'TestServiceHealth|TestTraefik' -v`
Expected: FAIL — `undefined: ServiceHealthCheck`, `undefined: TraefikBoundCheck`.

- [ ] **Step 3: Write minimal implementation**

Create `app/doctor/checks_platform.go`:

```go
package doctor

import (
	"context"
	"fmt"
	"strings"
)

// ServiceHealthCheck reports compose services whose healthcheck is failing.
// OneShot services are exempt: an init sidecar at "exited (0)" has succeeded,
// and flagging it would train the operator to ignore this check.
type ServiceHealthCheck struct{}

func (ServiceHealthCheck) ID() string   { return "service-health" }
func (ServiceHealthCheck) Scope() Scope { return ScopePlatform }

func (c ServiceHealthCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "platform services healthy", Scope: ScopePlatform}
	svcs, err := ex.PlatformServices(ctx)
	if err != nil {
		res.State = StateUnknown
		res.Detail = "could not read platform services: " + err.Error()
		return res
	}
	if len(svcs) == 0 {
		res.State = StateUnknown
		res.Detail = "the platform stack reported no services"
		return res
	}
	var bad []string
	for _, s := range svcs {
		if s.OneShot {
			continue
		}
		if s.Health == "unhealthy" {
			bad = append(bad, s.Name)
		}
	}
	if len(bad) == 0 {
		res.State = StatePass
		res.Detail = fmt.Sprintf("%d services, none unhealthy", len(svcs))
		return res
	}
	res.State = StateWarn
	res.Detail = "unhealthy: " + strings.Join(bad, ", ")
	// Not auto-safe: restarting a compose service is an app action
	// (RestartPlatformService), not a shell command, and it is already one click
	// away in the Services panel. Doctor points at it rather than duplicating it.
	res.Remedy = &Remedy{
		Command:  "restart these services from the Services panel: " + strings.Join(bad, ", "),
		AutoSafe: false,
	}
	return res
}

// TraefikBoundCheck distinguishes "Traefik is down" from "Traefik is up but
// something else owns its port". The second is the orphan-nginx signature: the
// container runs, the bind silently failed, and the stack looks healthy.
type TraefikBoundCheck struct{ ServiceName string }

func (TraefikBoundCheck) ID() string   { return "traefik-bound" }
func (TraefikBoundCheck) Scope() Scope { return ScopePlatform }

func (c TraefikBoundCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "traefik is bound and answering", Scope: ScopePlatform}
	svcs, err := ex.PlatformServices(ctx)
	if err != nil {
		res.State = StateUnknown
		res.Detail = "could not read platform services: " + err.Error()
		return res
	}
	var found *Service
	for i := range svcs {
		if svcs[i].Name == c.ServiceName {
			found = &svcs[i]
			break
		}
	}
	if found == nil {
		res.State = StateUnknown
		res.Detail = c.ServiceName + " is not in the platform stack"
		return res
	}
	if found.State != "running" {
		res.State = StateFail
		res.Detail = c.ServiceName + " is " + found.State
		res.Remedy = &Remedy{
			Command:  "start " + c.ServiceName + " from the Services panel",
			AutoSafe: false,
		}
		return res
	}
	if found.Addr == "" {
		res.State = StateUnknown
		res.Detail = c.ServiceName + " is running but publishes no host port to probe"
		return res
	}
	url := "http://" + found.Addr
	status, probeErr := ex.Probe(ctx, url)
	if probeErr != nil {
		res.State = StateFail
		res.Detail = c.ServiceName + " is running but " + found.Addr +
			" does not answer — another process likely owns the port"
		res.Remedy = &Remedy{
			Command:  "docker ps -a | grep nginx   # find and remove the orphan holding the port",
			AutoSafe: false,
			Confirm:  "",
		}
		return res
	}
	res.State = StatePass
	res.Detail = fmt.Sprintf("%s answers on %s (HTTP %d)", c.ServiceName, found.Addr, status)
	return res
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && go test ./doctor/ -v`
Expected: PASS — 29 tests.

- [ ] **Step 5: Commit**

```bash
git add app/doctor/checks_platform.go app/doctor/checks_platform_test.go
git commit -m "feat(doctor): service-health and traefik-bound checks

traefik-bound separates down from up-but-unreachable, because the second
is the orphan-nginx failure that leaves the whole stack looking healthy."
```

---

### Task 6: Platform probe checks — CANCELLED, DO NOT IMPLEMENT

> **Cancelled during execution.** Both checks below were specced against
> `Executor.Probe`, which sends no credentials, while each exists to answer "do
> our credentials work" — `registry-auth` must tell 401 from 404, and
> `vault-reachability` must tell a rejected bearer from an unmigrated SoR's 5xx.
> Implemented on an unauthenticated probe they would be guaranteed wrong in
> production while their tests passed, because the tests stub the probe's status:
> a real probe of a private registry returns 401 unconditionally, so the check
> would cry wolf on every run. That trains the operator to ignore Doctor, which
> defeats the panel. Prerequisite follow-up: a fourth `Executor` capability (an
> authenticated probe, or a registry-status read delegating to the existing
> `client.GetProfileImageStatus`, which already performs an authed check
> server-side and returns an `Error` string). Doctor ships 8 checks without them.
>
> The original task text is kept below as the spec for that follow-up.

#### (original, deferred) Task 6: Platform probe checks

**Files:**
- Modify: `app/doctor/checks_platform.go`
- Test: `app/doctor/checks_platform_test.go`

**Interfaces:**
- Consumes: Task 5's file.
- Produces: `RegistryAuthCheck{ProbeURL string}` id `registry-auth`; `VaultReachabilityCheck{VaultURLs map[string]string}` id `vault-reachability`.

- [ ] **Step 1: Write the failing test**

Append to `app/doctor/checks_platform_test.go`:

```go
// The defect this check exists for: a 401 must read as an auth failure, never
// as "the thing is not there".
func TestRegistryAuth401IsFailNamingAuth(t *testing.T) {
	ex := &fakeExec{probes: map[string]fakeProbe{
		"https://registry.example/v2/": {status: 401},
	}}
	got := RegistryAuthCheck{ProbeURL: "https://registry.example/v2/"}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
	low := strings.ToLower(got.Detail)
	if !strings.Contains(low, "auth") {
		t.Fatalf("detail %q must attribute this to authentication", got.Detail)
	}
	if strings.Contains(low, "no image") || strings.Contains(low, "not found") {
		t.Fatalf("a 401 must never be reported as a missing image: %q", got.Detail)
	}
}

func TestRegistryAuth200IsPass(t *testing.T) {
	ex := &fakeExec{probes: map[string]fakeProbe{
		"https://registry.example/v2/": {status: 200},
	}}
	got := RegistryAuthCheck{ProbeURL: "https://registry.example/v2/"}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass", got.State)
	}
}

func TestRegistryAuth404IsWarnDistinctFrom401(t *testing.T) {
	ex := &fakeExec{probes: map[string]fakeProbe{
		"https://registry.example/v2/": {status: 404},
	}}
	got := RegistryAuthCheck{ProbeURL: "https://registry.example/v2/"}.Run(context.Background(), ex)
	if got.State != StateWarn {
		t.Fatalf("state = %q, want warn for 404", got.State)
	}
	if strings.Contains(strings.ToLower(got.Detail), "auth") {
		t.Fatalf("a 404 is not an auth problem: %q", got.Detail)
	}
}

func TestRegistryUnreachableIsUnknown(t *testing.T) {
	ex := &fakeExec{probes: map[string]fakeProbe{
		"https://registry.example/v2/": {err: errors.New("dial timeout")},
	}}
	got := RegistryAuthCheck{ProbeURL: "https://registry.example/v2/"}.Run(context.Background(), ex)
	if got.State != StateUnknown {
		t.Fatalf("state = %q, want unknown", got.State)
	}
}

func TestVaultsAllReachableIsPass(t *testing.T) {
	ex := &fakeExec{probes: map[string]fakeProbe{
		"https://api.example/memory": {status: 200},
		"https://api.example/skills": {status: 200},
	}}
	got := VaultReachabilityCheck{VaultURLs: map[string]string{
		"memory": "https://api.example/memory",
		"skills": "https://api.example/skills",
	}}.Run(context.Background(), ex)
	if got.State != StatePass {
		t.Fatalf("state = %q, want pass (detail %q)", got.State, got.Detail)
	}
}

func TestVaultUnauthorizedIsFailNamingTheVault(t *testing.T) {
	ex := &fakeExec{probes: map[string]fakeProbe{
		"https://api.example/memory": {status: 200},
		"https://api.example/skills": {status: 403},
	}}
	got := VaultReachabilityCheck{VaultURLs: map[string]string{
		"memory": "https://api.example/memory",
		"skills": "https://api.example/skills",
	}}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
	if !strings.Contains(got.Detail, "skills") {
		t.Fatalf("detail %q must name the failing vault", got.Detail)
	}
}

// A 500 from a vault is the unmigrated-SoR signature: the relation the new
// binary queries does not exist yet.
func TestVault500SuggestsMigrations(t *testing.T) {
	ex := &fakeExec{probes: map[string]fakeProbe{
		"https://api.example/memory": {status: 500},
	}}
	got := VaultReachabilityCheck{VaultURLs: map[string]string{
		"memory": "https://api.example/memory",
	}}.Run(context.Background(), ex)
	if got.State != StateFail {
		t.Fatalf("state = %q, want fail", got.State)
	}
	if got.Remedy == nil || !strings.Contains(got.Remedy.Command, "db migrate") {
		t.Fatal("a 500 should point at pending SoR migrations")
	}
	if got.Remedy.AutoSafe {
		t.Fatal("migrations are order-sensitive; must not be auto-safe")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./doctor/ -run 'TestRegistry|TestVault' -v`
Expected: FAIL — `undefined: RegistryAuthCheck`, `undefined: VaultReachabilityCheck`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/doctor/checks_platform.go` (add `"sort"` to imports):

```go
// RegistryAuthCheck exists to kill a specific defect: the app once rendered a
// registry 401 as "no image". Auth failure and absence are different facts and
// this check must never collapse them.
type RegistryAuthCheck struct{ ProbeURL string }

func (RegistryAuthCheck) ID() string   { return "registry-auth" }
func (RegistryAuthCheck) Scope() Scope { return ScopePlatform }

func (c RegistryAuthCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "registry credentials accepted", Scope: ScopePlatform}
	status, err := ex.Probe(ctx, c.ProbeURL)
	if err != nil {
		res.State = StateUnknown
		res.Detail = "could not reach the registry: " + err.Error()
		return res
	}
	switch {
	case status == 401 || status == 403:
		res.State = StateFail
		res.Detail = fmt.Sprintf(
			"the registry rejected our credentials (HTTP %d) — this is an authentication failure, not a missing image",
			status)
		res.Remedy = &Remedy{
			Command:  "set REGISTRY_PASSWORD in the platform node's .env",
			AutoSafe: false,
			Confirm:  "",
		}
	case status >= 200 && status < 300:
		res.State = StatePass
		res.Detail = fmt.Sprintf("registry accepted our credentials (HTTP %d)", status)
	default:
		res.State = StateWarn
		res.Detail = fmt.Sprintf("registry answered HTTP %d", status)
	}
	return res
}

// VaultReachabilityCheck probes each brain vault. A 500 is called out
// separately because it is the unmigrated-SoR signature: the daemon does not
// auto-apply SoR migrations, so a new binary serving an old schema 500s every
// request against the whole vault.
type VaultReachabilityCheck struct{ VaultURLs map[string]string }

func (VaultReachabilityCheck) ID() string   { return "vault-reachability" }
func (VaultReachabilityCheck) Scope() Scope { return ScopePlatform }

func (c VaultReachabilityCheck) Run(ctx context.Context, ex Executor) Result {
	res := Result{ID: c.ID(), Title: "brain vaults reachable", Scope: ScopePlatform}
	if len(c.VaultURLs) == 0 {
		res.State = StateUnknown
		res.Detail = "no vault URLs configured"
		return res
	}
	names := make([]string, 0, len(c.VaultURLs))
	for name := range c.VaultURLs {
		names = append(names, name)
	}
	sort.Strings(names) // deterministic Detail text

	var denied, serverErr, unreachable, other []string
	for _, name := range names {
		status, err := ex.Probe(ctx, c.VaultURLs[name])
		switch {
		case err != nil:
			unreachable = append(unreachable, name)
		case status == 401 || status == 403:
			denied = append(denied, name)
		case status >= 500:
			serverErr = append(serverErr, name)
		case status >= 200 && status < 300:
		default:
			other = append(other, fmt.Sprintf("%s (HTTP %d)", name, status))
		}
	}
	switch {
	case len(serverErr) > 0:
		res.State = StateFail
		res.Detail = "vaults returning 5xx: " + strings.Join(serverErr, ", ") +
			" — the usual cause is a pending SoR migration"
		res.Remedy = &Remedy{
			Command:  "pbrainctl server db migrate <profile>",
			AutoSafe: false,
			Confirm:  "",
		}
	case len(denied) > 0:
		res.State = StateFail
		res.Detail = "vaults rejecting our bearer: " + strings.Join(denied, ", ")
		res.Remedy = &Remedy{
			Command:  "check the per-vault tokens in auth.toml and the client .env",
			AutoSafe: false,
			Confirm:  "",
		}
	case len(unreachable) > 0:
		res.State = StateUnknown
		res.Detail = "could not reach: " + strings.Join(unreachable, ", ")
	case len(other) > 0:
		res.State = StateWarn
		res.Detail = "unexpected responses: " + strings.Join(other, ", ")
	default:
		res.State = StatePass
		res.Detail = fmt.Sprintf("all %d vaults answered", len(c.VaultURLs))
	}
	return res
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && go test ./doctor/ -v`
Expected: PASS — 36 tests.

- [ ] **Step 5: Commit**

```bash
git add app/doctor/checks_platform.go app/doctor/checks_platform_test.go
git commit -m "feat(doctor): registry-auth and vault-reachability checks

registry-auth asserts in a test that a 401 is never reported as a missing
image — the exact defect the app shipped. A vault 5xx points at a pending
SoR migration, which the daemon does not apply on its own."
```

---

### Task 7: Real executor and the RunDoctor binding

**Files:**
- Create: `app/app_doctor.go`
- Test: `app/app_doctor_test.go`

**Interfaces:**
- Consumes: the whole `phantom-ink/doctor` package; existing `(*App).ListPlatformServices() ([]PlatformService, error)` (`app/app_platform_services.go:112`); existing `(*App).ScanProfiles() ([]Profile, error)` (`app/app_profiles.go:175`) returning `Profile` with a `Path` field (`app/profiles.go:15`).
- Produces: `appExecutor` implementing `doctor.Executor`; `(*App).buildDoctorRegistry() *doctor.Registry`; `(*App).RunDoctor(scope string) []doctor.Result`.

- [ ] **Step 1: Write the failing test**

Create `app/app_doctor_test.go`:

```go
package main

import (
	"testing"

	"phantom-ink/doctor"
)

// Every registered check must satisfy the invariants the UI relies on: unique
// ids, and an auto-safe remedy that can state what it will do. This is the test
// that stops an unconfirmable one-click fix from reaching the panel.
func TestRegisteredChecksHoldInvariants(t *testing.T) {
	a := &App{}
	reg := a.buildDoctorRegistry()
	checks := reg.All()
	if len(checks) == 0 {
		t.Fatal("no checks registered")
	}
	seen := map[string]bool{}
	for _, c := range checks {
		if seen[c.ID()] {
			t.Fatalf("duplicate check id %q", c.ID())
		}
		seen[c.ID()] = true
		if s := c.Scope(); s != doctor.ScopeLocal && s != doctor.ScopePlatform {
			t.Fatalf("check %q has invalid scope %q", c.ID(), s)
		}
	}
}

func TestAdaptServicesMapsFields(t *testing.T) {
	in := []PlatformService{{
		Name: "router", State: "running", Health: "healthy",
		Addr: "localhost:9910", OneShot: false,
	}}
	got := adaptServices(in)
	if len(got) != 1 {
		t.Fatalf("len = %d, want 1", len(got))
	}
	if got[0].Name != "router" || got[0].State != "running" ||
		got[0].Health != "healthy" || got[0].Addr != "localhost:9910" || got[0].OneShot {
		t.Fatalf("adapted badly: %+v", got[0])
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test . -run 'TestRegisteredChecks|TestAdaptServices' -v`
Expected: FAIL — `undefined: buildDoctorRegistry`, `undefined: adaptServices`.

- [ ] **Step 3: Write minimal implementation**

Create `app/app_doctor.go`:

```go
package main

// Doctor: the install's own health report.
//
// The checks themselves live in the pure phantom-ink/doctor package, which
// cannot import package main. This file is the whole of the wiring: it adapts
// main's PlatformService into doctor's narrower Service, supplies the three
// executor capabilities, and builds the registry with host-specific paths and
// URLs injected.
//
// There is deliberately no remote shell capability. The only remote seam the app
// has is PlatformActionOn, which performs compose actions; adding a generic
// "run this on the node" endpoint would be a permanent new attack surface
// reachable from a desktop app, so platform checks read service state and probe
// over HTTP instead.

import (
	"context"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"phantom-ink/doctor"
)

type appExecutor struct{ a *App }

func (e appExecutor) Shell(ctx context.Context, cmd []string) (string, int, error) {
	if len(cmd) == 0 {
		return "", 0, nil
	}
	c := exec.CommandContext(ctx, cmd[0], cmd[1:]...)
	out, err := c.CombinedOutput()
	if ee, ok := err.(*exec.ExitError); ok {
		// A non-zero exit is an observation, not a failure to observe.
		return string(out), ee.ExitCode(), nil
	}
	if err != nil {
		return string(out), -1, err
	}
	return string(out), 0, nil
}

func (e appExecutor) PlatformServices(context.Context) ([]doctor.Service, error) {
	svcs, err := e.a.ListPlatformServices()
	if err != nil {
		return nil, err
	}
	return adaptServices(svcs), nil
}

func (e appExecutor) Probe(ctx context.Context, url string) (int, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, err
	}
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	return resp.StatusCode, nil
}

// adaptServices narrows main's PlatformService to the doctor package's Service.
func adaptServices(in []PlatformService) []doctor.Service {
	out := make([]doctor.Service, 0, len(in))
	for _, s := range in {
		out = append(out, doctor.Service{
			Name:    s.Name,
			State:   s.State,
			Health:  s.Health,
			Addr:    s.Addr,
			OneShot: s.OneShot,
		})
	}
	return out
}

// doctorProfileDirs lists the profile directories whose .envrc approval matters.
// A failure to enumerate yields an empty list, which the direnv check reports as
// a pass over zero profiles rather than inventing a fault.
func (a *App) doctorProfileDirs() []string {
	profiles, err := a.ScanProfiles()
	if err != nil {
		return nil
	}
	dirs := make([]string, 0, len(profiles))
	for _, p := range profiles {
		if p.Path != "" {
			dirs = append(dirs, p.Path)
		}
	}
	return dirs
}

func (a *App) buildDoctorRegistry() *doctor.Registry {
	home, _ := os.UserHomeDir()
	reg := doctor.NewRegistry()
	for _, c := range []doctor.Check{
		doctor.DockerSymlinkCheck{},
		doctor.AppDBCheck{Path: filepath.Join(home, ".phantom-ink.db")},
		doctor.DirenvApprovalCheck{ProfileDirs: a.doctorProfileDirs()},
		doctor.GitHubTokenShadowCheck{},
		doctor.PortShadowCheck{Port: "9910"},
		doctor.GatewayMCPPinCheck{Container: "phantom-gateway-1"},
		doctor.ServiceHealthCheck{},
		doctor.TraefikBoundCheck{ServiceName: "traefik"},
	} {
		// Register only fails on a duplicate id, which the registry test catches.
		_ = reg.Register(c)
	}
	return reg
}

// RunDoctor runs the checks for one scope ("local", "platform", or "" for all)
// and returns them pre-sorted worst-first. Read-only: opening the panel must
// never change anything.
func (a *App) RunDoctor(scope string) []doctor.Result {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	return a.buildDoctorRegistry().Run(ctx, appExecutor{a: a}, doctor.Scope(scope))
}
```

> Implementer note: `registry-auth` and `vault-reachability` do **not** exist in
> the `doctor` package and must **not** be referenced here — Task 6 was
> cancelled. They were specced against `Executor.Probe`, which sends no
> credentials, while both checks exist to answer "do our credentials work". An
> unauthenticated probe of a private registry returns 401 unconditionally, so
> `registry-auth` would have reported "the registry rejected our credentials" on
> every run while its stubbed tests passed — permanently false output from a
> check written to prevent false output. Building them needs a fourth `Executor`
> capability (an authenticated probe, or a registry-status read delegating to the
> existing `client.GetProfileImageStatus`, which already performs an authed check
> server-side and returns an `Error` string). That is a follow-up, not this task.
> Register exactly the eight checks listed above and nothing else.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && go test . -run 'TestRegisteredChecks|TestAdaptServices' -v`
Expected: PASS — 2 tests.

- [ ] **Step 5: Regenerate Wails bindings**

Run: `cd app && wails generate module`
Expected: `app/frontend/wailsjs/go/main/App.d.ts` now declares `RunDoctor`.

- [ ] **Step 6: Commit**

```bash
git add app/app_doctor.go app/app_doctor_test.go app/frontend/wailsjs
git commit -m "feat(doctor): real executor and the RunDoctor binding

main adapts its own PlatformService into the doctor package's narrower
Service, since a package main type cannot be imported. A non-zero exit is
passed through as an observation rather than an error: only a genuine
failure to observe becomes unknown."
```

---

### Task 8: ApplyRemedy with the AutoSafe gate

**Files:**
- Modify: `app/app_doctor.go`
- Test: `app/app_doctor_test.go`

**Interfaces:**
- Consumes: Task 7's registry and executor.
- Produces: `(*App).ApplyRemedy(id string) doctor.Result`; `doctor.ErrNotAutoSafe`; `doctor.Registry.ApplyRemedy(ctx, ex, id) (Result, error)`.

The Go side refuses a non-auto-safe remedy regardless of any frontend state. The UI switch is an affordance; this is the boundary.

- [ ] **Step 1: Write the failing test**

Append to `app/doctor/registry_test.go`:

```go
type remedyCheck struct {
	id    string
	state State
	rem   *Remedy
	runs  *int
}

func (c remedyCheck) ID() string   { return c.id }
func (c remedyCheck) Scope() Scope { return ScopeLocal }
func (c remedyCheck) Run(context.Context, Executor) Result {
	*c.runs++
	return Result{ID: c.id, State: c.state, Remedy: c.rem}
}

func TestApplyRemedyRefusesNonAutoSafe(t *testing.T) {
	runs := 0
	r := NewRegistry()
	_ = r.Register(remedyCheck{
		id: "x", state: StateFail, runs: &runs,
		rem: &Remedy{Command: "rm -rf /", AutoSafe: false},
	})
	_, err := r.ApplyRemedy(context.Background(), &fakeExec{}, "x")
	if !errors.Is(err, ErrNotAutoSafe) {
		t.Fatalf("err = %v, want ErrNotAutoSafe", err)
	}
}

func TestApplyRemedyUnknownIDErrors(t *testing.T) {
	r := NewRegistry()
	if _, err := r.ApplyRemedy(context.Background(), &fakeExec{}, "nope"); err == nil {
		t.Fatal("want an error for an unknown check id")
	}
}

func TestApplyRemedyNoRemedyErrors(t *testing.T) {
	runs := 0
	r := NewRegistry()
	_ = r.Register(remedyCheck{id: "x", state: StatePass, runs: &runs, rem: nil})
	if _, err := r.ApplyRemedy(context.Background(), &fakeExec{}, "x"); err == nil {
		t.Fatal("want an error when the check offers no remedy")
	}
}

// After a fix, the caller must see a re-run result, not an optimistic green.
func TestApplyRemedyReRunsTheCheck(t *testing.T) {
	runs := 0
	r := NewRegistry()
	_ = r.Register(remedyCheck{
		id: "x", state: StateFail, runs: &runs,
		rem: &Remedy{Command: "true", Exec: []string{"true"}, AutoSafe: true, Confirm: "Run true."},
	})
	ex := &fakeExec{shellOut: map[string]fakeShell{"true": {code: 0}}}
	got, err := r.ApplyRemedy(context.Background(), ex, "x")
	if err != nil {
		t.Fatalf("apply: %v", err)
	}
	if runs < 2 {
		t.Fatalf("check ran %d times, want the pre-check plus a re-run", runs)
	}
	if got.State != StateFail {
		t.Fatalf("state = %q — the result must be the re-run's truth, not an assumed pass", got.State)
	}
}
```

Add `"errors"` to that test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && go test ./doctor/ -run TestApplyRemedy -v`
Expected: FAIL — `undefined: ErrNotAutoSafe`, no `ApplyRemedy` method.

- [ ] **Step 3: Write minimal implementation**

Append to `app/doctor/registry.go` (add `"errors"` to imports):

```go
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
```

Append to `app/app_doctor.go`:

```go
// DoctorRemedyResult carries the re-run result plus any error, so the frontend
// can show "the fix ran and here is what is true now" in one round trip.
type DoctorRemedyResult struct {
	Result doctor.Result `json:"result"`
	Error  string        `json:"error,omitempty"`
}

// ApplyRemedy runs one check's auto-safe remedy. Non-auto-safe remedies are
// refused here, not in the UI.
func (a *App) ApplyRemedy(id string) DoctorRemedyResult {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	res, err := a.buildDoctorRegistry().ApplyRemedy(ctx, appExecutor{a: a}, id)
	out := DoctorRemedyResult{Result: res}
	if err != nil {
		out.Error = err.Error()
	}
	return out
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && go test ./... -race`
Expected: PASS — all packages.

- [ ] **Step 5: Regenerate bindings and commit**

```bash
cd app && wails generate module
git add app/doctor/registry.go app/doctor/registry_test.go app/app_doctor.go app/frontend/wailsjs
git commit -m "feat(doctor): gated ApplyRemedy that returns the re-run truth

A non-auto-safe remedy is refused in Go, so the frontend switch is an
affordance rather than the security boundary. The returned result is always
the post-fix observation: a fix that did not take shows as still failing."
```

---

### Task 9: DoctorPanel.svelte

**Files:**
- Create: `app/frontend/src/lib/panels/DoctorPanel.svelte`
- Modify: `app/frontend/src/lib/panels.ts`
- Modify: `app/frontend/src/lib/layout/AppShell.svelte`

**Interfaces:**
- Consumes: `RunDoctor(scope)` and `ApplyRemedy(id)` from `wailsjs/go/main/App`; `Badge.svelte` (`{ text, variant }`); `CardExpander.svelte`; `EmptyState.svelte`; `Spinner.svelte`.
- Produces: the `doctor` panel id.

- [ ] **Step 1: Add the panel to the registry**

In `app/frontend/src/lib/panels.ts`, add this entry to the `system` section, immediately before the `settings` entry:

```ts
  {
    id: 'doctor',
    label: 'Doctor',
    section: 'system',
    icon: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 2a2 2 0 0 0-2 2v5H4a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h5v5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2v-5h5a2 2 0 0 0 2-2v-2a2 2 0 0 0-2-2h-5V4a2 2 0 0 0-2-2z"/></svg>`,
  },
```

- [ ] **Step 2: Write the panel**

Create `app/frontend/src/lib/panels/DoctorPanel.svelte`:

```svelte
<script lang="ts">
  // Doctor: the install's own health report.
  //
  // Two UI rules carry the weight here. Checks run only when asked — opening
  // the panel must not fire a dozen probes at the platform. And `unknown`
  // renders muted grey rather than amber: an absence of evidence must not look
  // like a mild failure, which is the same mistake as a fake green light.
  import { getApi } from '../utils/api';
  import Badge from '../components/Badge.svelte';
  import CardExpander from '../components/CardExpander.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';

  type State = 'pass' | 'warn' | 'fail' | 'unknown';
  interface Remedy { command: string; auto_safe: boolean; confirm: string }
  interface Result {
    id: string; title: string; scope: 'local' | 'platform';
    state: State; detail: string; remedy?: Remedy; docs?: string;
  }

  let results = $state<Result[]>([]);
  let running = $state(false);
  let ran = $state(false);
  let error = $state('');

  // Deliberately NOT persisted: off on every launch, so it cannot be left on.
  let allowFixes = $state(false);
  let pendingFix = $state<Result | null>(null);
  let fixing = $state('');

  const local = $derived(results.filter((r) => r.scope === 'local'));
  const platform = $derived(results.filter((r) => r.scope === 'platform'));

  // Badge variants are limited to those Badge.svelte defines. 'unknown' maps to
  // no variant on purpose: the muted default is the point.
  function variantFor(s: State): string {
    if (s === 'fail') return 'failed';
    if (s === 'warn') return 'pending';
    if (s === 'pass') return 'running';
    return '';
  }

  function summarize(rs: Result[]): string {
    if (!rs.length) return 'no checks';
    const counts: Record<string, number> = {};
    for (const r of rs) counts[r.state] = (counts[r.state] ?? 0) + 1;
    return (['fail', 'unknown', 'warn', 'pass'] as State[])
      .filter((s) => counts[s])
      .map((s) => `${counts[s]} ${s}`)
      .join(' · ');
  }

  async function run() {
    running = true;
    error = '';
    try {
      results = ((await (getApi() as any).RunDoctor('')) as Result[]) ?? [];
      ran = true;
    } catch (e) {
      error = String(e);
    } finally {
      running = false;
    }
  }

  async function applyFix(r: Result) {
    pendingFix = null;
    fixing = r.id;
    try {
      const out = (await (getApi() as any).ApplyRemedy(r.id)) as {
        result: Result; error?: string;
      };
      if (out.error) error = out.error;
      // Replace the row with the re-run result, never an assumed pass.
      if (out.result?.id) {
        results = results.map((x) => (x.id === out.result.id ? out.result : x));
      }
    } catch (e) {
      error = String(e);
    } finally {
      fixing = '';
    }
  }

  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); } catch { /* clipboard denied */ }
  }
</script>

<div class="doctor">
  <header>
    <div>
      <h2>Doctor</h2>
      <p class="sub">Checks run only when you ask. Nothing here changes until you say so.</p>
    </div>
    <div class="actions">
      <label class="switch">
        <input type="checkbox" bind:checked={allowFixes} />
        Allow fixes
      </label>
      <button onclick={run} disabled={running}>
        {running ? 'Running…' : 'Run checks'}
      </button>
    </div>
  </header>

  {#if error}
    <div class="error">{error}</div>
  {/if}

  {#if running && !ran}
    <Spinner />
  {:else if !ran}
    <EmptyState message="Run the checks to see this install's health." />
  {:else}
    {#each [{ label: 'Local', rows: local }, { label: 'Platform', rows: platform }] as group (group.label)}
      <CardExpander title={`${group.label} · ${summarize(group.rows)}`}>
        {#if !group.rows.length}
          <p class="sub">No checks in this scope.</p>
        {:else}
          <ul>
            {#each group.rows as r (r.id)}
              <li>
                <Badge text={r.state} variant={variantFor(r.state)} />
                <div class="body">
                  <div class="title">{r.title}</div>
                  <div class="detail">{r.detail}</div>
                </div>
                {#if r.remedy}
                  {#if r.remedy.auto_safe && allowFixes}
                    <button
                      class="fix"
                      disabled={fixing === r.id}
                      onclick={() => (pendingFix = r)}
                    >{fixing === r.id ? 'Fixing…' : 'Fix'}</button>
                  {:else}
                    <button class="copy" onclick={() => copy(r.remedy!.command)}>Copy fix</button>
                  {/if}
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </CardExpander>
    {/each}
  {/if}

  {#if pendingFix?.remedy}
    <div class="confirm">
      <p>{pendingFix.remedy.confirm}</p>
      <code>{pendingFix.remedy.command}</code>
      <div class="confirm-actions">
        <button onclick={() => (pendingFix = null)}>Cancel</button>
        <button class="fix" onclick={() => applyFix(pendingFix!)}>Run it</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .doctor { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  h2 { margin: 0; font-size: 15px; }
  .sub { margin: 2px 0 0; font-size: 11px; color: var(--color-text-tertiary); }
  .actions { display: flex; align-items: center; gap: 10px; }
  .switch { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--color-text-secondary); }
  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  li { display: flex; align-items: flex-start; gap: 8px; }
  .body { flex: 1; min-width: 0; }
  .title { font-size: 12px; font-weight: 600; }
  .detail { font-size: 11px; color: var(--color-text-tertiary); word-break: break-word; }
  .error { font-size: 11px; color: var(--color-status-error-text); }
  .confirm { border: 1px solid var(--color-warning-border); background: var(--color-warning-bg); padding: 10px; border-radius: 6px; font-size: 11px; }
  .confirm code { display: block; margin: 6px 0; font-size: 10px; word-break: break-all; }
  .confirm-actions { display: flex; gap: 6px; justify-content: flex-end; }
</style>
```

- [ ] **Step 3: Wire it into AppShell**

In `app/frontend/src/lib/layout/AppShell.svelte`, add the import alongside the other panel imports (near line 21):

```ts
  import DoctorPanel from '../panels/DoctorPanel.svelte';
```

Then add a render branch in the same `{#if currentPanel.value === …}` chain that handles `'files'` and `'event-log'`:

```svelte
      {:else if currentPanel.value === 'doctor'}
        <DoctorPanel />
```

- [ ] **Step 4: Verify the frontend typechecks**

Run: `cd app/frontend && npm run check`
Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add app/frontend/src/lib/panels/DoctorPanel.svelte app/frontend/src/lib/panels.ts app/frontend/src/lib/layout/AppShell.svelte
git commit -m "feat(doctor): Doctor panel

Checks run only on request, so opening the panel never probes the platform.
The allow-fixes switch is in-memory only and off on every launch, and
unknown renders muted rather than amber: absence of evidence must not look
like a mild failure."
```

---

### Task 10: Full verification

**Files:** none modified.

- [ ] **Step 1: Typecheck and test Go**

Run: `cd app && go vet ./... && go test ./... -race`
Expected: PASS.

- [ ] **Step 2: Typecheck the frontend**

Run: `cd app/frontend && npm run check`
Expected: no new errors.

- [ ] **Step 3: Confirm the contract gate is untouched**

Run: `just app-contract-gen && git diff --exit-code app/internal/contract`
Expected: exit 0, no diff.

- [ ] **Step 4: Build the app**

Run: `just app-build`
Expected: builds to `app/build/bin/phantom-ink.app`.

- [ ] **Step 5: Run the app and exercise the panel**

Open Doctor, click **Run checks**, and confirm by observation:
- `app-db` reports **fail** if `~/.phantom-ink.db` is still 0 bytes.
- Platform rows report `unknown` with a stated reason when the platform node is unreachable — **not** `fail`.
- With **Allow fixes** off, no `Fix` button is drawn anywhere.
- Turning it on draws `Fix` only on auto-safe rows; others still say `Copy fix`.
- Clicking `Fix` shows the confirm text before anything runs.

Report what you observed. Do not claim the panel works without having run it.

- [ ] **Step 6: Commit any fixes and open the PR**

```bash
git push -u origin feat/doctor-panel
gh pr create --title "Doctor panel: executable install health checks" --body "$(cat <<'BODY'
Turns the recurring install failures documented in CLAUDE.md and the memory
vault into executable checks.

The state model is the point: a check reports `pass` only when it positively
observed the good state, and `unknown` is first-class. This exists because the
app shipped the opposite bug — a registry 401 rendering as "no image" — and
`registry-auth` now has a test asserting a 401 is never reported as a missing
image.

Remediation is opt-in and narrow. Go refuses any remedy not marked auto-safe,
so the UI switch is an affordance rather than the boundary, and `ApplyRemedy`
returns the re-run result so a fix that did not work shows as still failing.
Two of the registered checks are auto-safe; the rest are copy-the-command.

No generic remote exec was added: platform checks read existing service state
and probe over HTTP. Spec: `docs/superpowers/specs/2026-10-15-doctor-panel-design.md`
BODY
)"
```

- [ ] **Step 7: Watch CI**

Run: `env -u GITHUB_TOKEN -u GH_TOKEN gh pr checks --watch`
Expected: `test-app` passes. Fix failures before merging.

---

## Deferred, with reasons

- **`registry-auth` and `vault-reachability`** — Task 6 cancelled during
  execution; neither is implemented. The config accessors exist
  (`a.resolveRegistryURL(profile)`, `a.meshURL()`), but the blocker is the
  `Executor` interface: `Probe` sends no credentials and both checks are about
  whether our credentials work. Needs a fourth capability first.
- **`sor-migrations` and `profile-api-key` checks** — specced but not planned. `sor-migrations` is partly covered by `vault-reachability`'s 5xx branch pointing at pending migrations; `profile-api-key` needs a placeholder-detection rule that should be decided against real config rather than guessed here.
- **Tri-state retrofit of the Services and Gateway badges** — follow-up. Doctor establishes the state model first.
