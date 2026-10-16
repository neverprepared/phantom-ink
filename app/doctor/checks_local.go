package doctor

import (
	"context"
	"os"
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
