package doctor

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
)

// DockerSymlinkCheck guards the OrbStack migration failure: Docker Desktop used
// to provide /usr/local/bin/docker, and without it GUI/launchd apps (the runner
// app) have no docker on PATH, so session creation dies with exit 127 while the
// control plane still looks healthy.
type DockerSymlinkCheck struct {
	// homeDir resolves the operator's home directory, defaulting to
	// os.UserHomeDir. It is a field (rather than a bare call) so a test can
	// make the lookup fail without relying on unsetting $HOME, which does not
	// portably affect os.UserHomeDir on macOS.
	homeDir func() (string, error)
}

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
		Command: "ln -sf ~/.orbstack/bin/docker /usr/local/bin/docker",
		Confirm: "Symlink ~/.orbstack/bin/docker to /usr/local/bin/docker on this Mac.",
	}
	homeDir := c.homeDir
	if homeDir == nil {
		homeDir = os.UserHomeDir
	}
	home, err := homeDir()
	if err != nil {
		// The observation stands: the symlink is positively confirmed
		// missing, so this stays StateFail. What's unavailable is only the
		// ability to safely auto-run a fix, since the source path cannot be
		// resolved — never build an Exec argv from a path we couldn't get.
		res.Detail += "; could not resolve home directory for an auto-fix: " + err.Error()
		return res
	}
	res.Remedy.Exec = []string{"ln", "-sf", home + "/.orbstack/bin/docker", "/usr/local/bin/docker"}
	res.Remedy.AutoSafe = true
	return res
}

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
