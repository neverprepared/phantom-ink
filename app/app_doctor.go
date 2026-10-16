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
// a pass over zero profiles rather than inventing a fault. a.config is nil on a
// zero-value App (as in tests that construct &App{} directly without NewApp's
// startup wiring); ScanProfiles dereferences it unconditionally, so guard here
// rather than let it panic.
func (a *App) doctorProfileDirs() []string {
	if a.config == nil {
		return nil
	}
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
	home, err := os.UserHomeDir()
	var dbPath string
	if err == nil {
		dbPath = filepath.Join(home, ".phantom-ink.db")
	}
	// On error, dbPath stays "" rather than falling back to a relative
	// ".phantom-ink.db" — a relative path would stat whatever happens to be
	// in the process's cwd and could produce a plausible-looking StatePass
	// having never looked at the real app database. AppDBCheck reports
	// StateUnknown for an empty Path.
	reg := doctor.NewRegistry()
	for _, c := range []doctor.Check{
		doctor.DockerSymlinkCheck{},
		doctor.AppDBCheck{Path: dbPath},
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
