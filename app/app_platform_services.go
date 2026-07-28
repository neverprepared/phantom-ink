package main

import (
	"fmt"
	"os/exec"
	"sort"
	"strings"
)

// The phantom-platform compose project — the decomposed service stack the app
// drives (router, gateway, events, fleet, auth, credentials, postgres, minio,
// nginx, + one-shot *-init sidecars). Discovered/controlled via docker + docker
// compose, keyed on the compose project label (same approach as restartViaDocker
// and the Databases card) so no compose path is hardcoded.
const platformProject = "phantom-platform"

// PlatformService is one compose service's live state for the Services panel.
type PlatformService struct {
	Name    string `json:"name"`     // compose service name
	State   string `json:"state"`    // running | exited | restarting | created | …
	Status  string `json:"status"`   // docker's human status, e.g. "Up 4 hours (healthy)"
	Health  string `json:"health"`   // healthy | unhealthy | starting | "" (no healthcheck)
	OneShot bool   `json:"one_shot"` // *-init sidecars: "exited (0)" is success, not a fault
}

// platformComposeCtx returns the project's working dir + compose file, read off a
// running platform container's labels, so `docker compose` runs with the right
// project + .env without a hardcoded path.
func (a *App) platformComposeCtx() (workdir, configFile string, err error) {
	out, err := exec.Command("docker", "ps",
		"--filter", "label=com.docker.compose.project="+platformProject,
		"--format", `{{.Label "com.docker.compose.project.working_dir"}}	{{.Label "com.docker.compose.project.config_files"}}`,
	).Output()
	if err != nil {
		return "", "", fmt.Errorf("docker ps: %w", err)
	}
	for _, ln := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		parts := strings.SplitN(ln, "\t", 2)
		if len(parts) == 2 && parts[0] != "" && parts[1] != "" {
			return parts[0], parts[1], nil
		}
	}
	return "", "", fmt.Errorf("no running %s containers found", platformProject)
}

// ListPlatformServices lists the platform compose services with their state.
func (a *App) ListPlatformServices() ([]PlatformService, error) {
	out, err := exec.Command("docker", "ps", "-a",
		"--filter", "label=com.docker.compose.project="+platformProject,
		"--format", `{{.Label "com.docker.compose.service"}}	{{.State}}	{{.Status}}`,
	).Output()
	if err != nil {
		return nil, fmt.Errorf("docker ps: %w", err)
	}
	var svcs []PlatformService
	for _, ln := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if ln == "" {
			continue
		}
		f := strings.SplitN(ln, "\t", 3)
		if len(f) < 2 || f[0] == "" {
			continue
		}
		s := PlatformService{Name: f[0], State: f[1], OneShot: strings.HasSuffix(f[0], "-init")}
		if len(f) == 3 {
			s.Status = f[2]
			s.Health = parseHealth(f[2])
		}
		svcs = append(svcs, s)
	}
	sort.Slice(svcs, func(i, j int) bool { return svcs[i].Name < svcs[j].Name })
	return svcs, nil
}

func parseHealth(status string) string {
	switch {
	case strings.Contains(status, "(healthy)"):
		return "healthy"
	case strings.Contains(status, "(unhealthy)"):
		return "unhealthy"
	case strings.Contains(status, "health: starting"):
		return "starting"
	default:
		return ""
	}
}

// PlatformExternal is a platform dependency that lives OUTSIDE the compose stack
// (a host process or an external endpoint) — surfaced under the platform group as
// read-only health, since it isn't docker-compose managed.
type PlatformExternal struct {
	Name     string `json:"name"`
	Label    string `json:"label"`
	Endpoint string `json:"endpoint"`
	Healthy  bool   `json:"healthy"`
	Note     string `json:"note"`
}

// ListPlatformExternals reports the platform's non-compose dependencies —
// Ollama (host inference) and OpenSearch (OTEL signal store) — by a quick TCP
// reachability probe. No start/stop: Ollama is a host process; OpenSearch is
// external (its platform placement lands with the observability work).
func (a *App) ListPlatformExternals() []PlatformExternal {
	return []PlatformExternal{
		{Name: "ollama", Label: "Ollama", Endpoint: "localhost:11434",
			Healthy: isPortOpen(11434), Note: "host inference"},
		{Name: "opensearch", Label: "OpenSearch", Endpoint: "localhost:9200",
			Healthy: isPortOpen(9200), Note: "OTEL store (observability)"},
	}
}

// StartPlatformService (re)creates + starts a service (revives removed/stopped ones).
func (a *App) StartPlatformService(name string) error { return a.composeAction("up", "-d", name) }

// StopPlatformService stops a service (container kept, revivable via Start).
func (a *App) StopPlatformService(name string) error { return a.composeAction("stop", name) }

// RestartPlatformService restarts a service. NOTE: restarting `router` briefly
// drops the app's own connection to the platform.
func (a *App) RestartPlatformService(name string) error { return a.composeAction("restart", name) }

// RestartAllPlatformServices restarts the whole stack.
func (a *App) RestartAllPlatformServices() error { return a.composeAction("restart") }

// composeAction runs `docker compose <args>` scoped to the discovered platform
// project. `name` (when passed via args) targets one service; omit it for the stack.
func (a *App) composeAction(args ...string) error {
	wd, cfg, err := a.platformComposeCtx()
	if err != nil {
		return err
	}
	full := append([]string{"compose", "--project-directory", wd, "-f", cfg}, args...)
	if out, err := exec.Command("docker", full...).CombinedOutput(); err != nil {
		msg := strings.TrimSpace(string(out))
		if msg == "" {
			msg = err.Error()
		}
		return fmt.Errorf("%s", msg)
	}
	return nil
}
