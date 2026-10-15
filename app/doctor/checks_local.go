package doctor

import (
	"context"
	"os"
)

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
