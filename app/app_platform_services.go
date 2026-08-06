package main

import (
	"fmt"
	"os/exec"
	"sort"
	"strings"

	"phantom-ink/brainbox"
)

// The phantom-platform compose project — the decomposed service stack the app
// drives (router, gateway, events, fleet, auth, credentials, postgres, minio,
// nginx, + one-shot *-init sidecars). Discovered/controlled via docker + docker
// compose, keyed on the compose project label (same approach as restartViaDocker
// and the Databases card) so no compose path is hardcoded.
const platformProject = "phantom-platform"

// PlatformService is one compose service's live state for the Services panel.
type PlatformService struct {
	Name    string `json:"name"`              // compose service name
	State   string `json:"state"`             // running | exited | restarting | created | …
	Status  string `json:"status"`            // docker's human status, e.g. "Up 4 hours (healthy)"
	Health  string `json:"health"`            // healthy | unhealthy | starting | "" (no healthcheck)
	OneShot bool   `json:"one_shot"`          // *-init sidecars: "exited (0)" is success, not a fault
	Addr    string `json:"addr,omitempty"`    // host-reachable host:port from the first published port ("" = internal-only)
	WebURL  string `json:"web_url,omitempty"` // browsable web UI, if the service has one (else "")
}

// hostAddrFromPorts extracts a host-reachable "localhost:<port>" from a docker
// ps Ports string (e.g. "0.0.0.0:9910->9910/tcp" or "127.0.0.1:4900->4900/tcp,
// 0.0.0.0:21890-21892->…"). Returns the first single published host port, or ""
// when nothing is published (internal-only service).
func hostAddrFromPorts(ports string) string {
	for _, part := range strings.Split(ports, ",") {
		part = strings.TrimSpace(part)
		arrow := strings.Index(part, "->")
		if arrow < 0 {
			continue // only exposed, not published
		}
		left := part[:arrow] // "0.0.0.0:9910" | "127.0.0.1:4900"
		colon := strings.LastIndex(left, ":")
		if colon < 0 {
			continue
		}
		port := left[colon+1:]
		if port == "" || strings.Contains(port, "-") {
			continue // skip published ranges (e.g. 21890-21892)
		}
		return "localhost:" + port
	}
	return ""
}

// platformWebUIs maps a compose service name → its host-published web UI URL,
// for services that offer a browser-openable interface. The app runs on the
// host and the platform stack is docker-compose-managed locally, so localhost
// is correct. These mirror the compose port publishes; MinIO's UI is the
// console on :9001 (distinct from the S3 API on :9090).
var platformWebUIs = map[string]string{
	"minio":                 "http://localhost:9001",
	"opensearch-dashboards": "http://localhost:5601",
	"traefik":               "http://localhost:8090/dashboard/",
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

// platformNode returns the configured remote platform node ("" ⇒ manage local
// docker). When set, Platform Services operations route through the router →
// that node's runner, so the app can manage a platform it isn't co-located with.
func (a *App) platformNode() string {
	if a.db == nil {
		return ""
	}
	return a.db.GetSetting(settingPlatformNode, "")
}

// ListPlatformNodes returns docker-capable fleet nodes eligible to host the
// platform stack (for the Settings picker). Requires a configured router.
func (a *App) ListPlatformNodes() ([]brainbox.PlatformNode, error) {
	if a.client == nil {
		return nil, fmt.Errorf("not connected to a platform API")
	}
	res, err := a.client.ListPlatformNodes()
	if err != nil {
		return nil, err
	}
	return res.Nodes, nil
}

// ListPlatformServices lists the platform compose services with their state.
// Routes through the runner on the configured platform node, or the local
// docker daemon when no node is set.
func (a *App) ListPlatformServices() ([]PlatformService, error) {
	if node := a.platformNode(); node != "" {
		res, err := a.client.ListPlatformServicesOn(node)
		if err != nil {
			return nil, err
		}
		svcs := make([]PlatformService, 0, len(res.Services))
		for _, s := range res.Services {
			svcs = append(svcs, PlatformService{
				Name: s.Name, State: s.State, Status: s.Status, Health: s.Health,
				OneShot: s.OneShot, Addr: s.Addr, WebURL: s.WebURL,
			})
		}
		return svcs, nil
	}
	out, err := exec.Command("docker", "ps", "-a",
		"--filter", "label=com.docker.compose.project="+platformProject,
		"--format", `{{.Label "com.docker.compose.service"}}	{{.State}}	{{.Status}}	{{.Ports}}`,
	).Output()
	if err != nil {
		return nil, fmt.Errorf("docker ps: %w", err)
	}
	var svcs []PlatformService
	for _, ln := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if ln == "" {
			continue
		}
		f := strings.SplitN(ln, "\t", 4)
		if len(f) < 2 || f[0] == "" {
			continue
		}
		s := PlatformService{Name: f[0], State: f[1], OneShot: strings.HasSuffix(f[0], "-init"), WebURL: platformWebUIs[f[0]]}
		if len(f) >= 3 {
			s.Status = f[2]
			s.Health = parseHealth(f[2])
		}
		if len(f) >= 4 {
			s.Addr = hostAddrFromPorts(f[3])
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

// ListPlatformExternals reports the platform's non-compose dependencies. Ollama
// is a host process (the macOS app) surfaced here as read-only health; it has no
// docker-compose lifecycle. (OpenSearch is now a first-class compose service, so
// it appears in ListPlatformServices with controls instead.)
func (a *App) ListPlatformExternals() []PlatformExternal {
	out := []PlatformExternal{
		{Name: "ollama", Label: "Ollama", Endpoint: "localhost:11434",
			Healthy: isPortOpen(11434), Note: "host inference"},
	}

	// Image registry (remote): profile/base image push+pull target. Read-only
	// health here; the URL is configured in Settings → General.
	registryURL := ""
	if a.db != nil {
		registryURL = a.db.GetSetting(settingRegistryURL, "")
	}
	reg := PlatformExternal{Name: "registry", Label: "Registry"}
	if registryURL == "" {
		reg.Endpoint = "not configured"
		reg.Note = "set the registry URL in Settings"
	} else {
		reg.Endpoint = registryURL
		reg.Healthy = isHostReachable(registryURL)
		reg.Note = "image registry"
	}
	out = append(out, reg)

	return out
}

// StartPlatformService (re)creates + starts a service (revives removed/stopped ones).
func (a *App) StartPlatformService(name string) error { return a.platformAction("up", name) }

// StopPlatformService stops a service (container kept, revivable via Start).
func (a *App) StopPlatformService(name string) error { return a.platformAction("stop", name) }

// RestartPlatformService restarts a service. NOTE: restarting `router` briefly
// drops the app's own connection to the platform.
func (a *App) RestartPlatformService(name string) error { return a.platformAction("restart", name) }

// RestartAllPlatformServices restarts the whole stack.
func (a *App) RestartAllPlatformServices() error { return a.platformAction("restart", "") }

// platformAction runs up|stop|restart on the configured platform node's runner
// (whole stack when service is ""), or the local docker daemon when no node is
// set. `up` maps to `docker compose up -d`.
func (a *App) platformAction(action, service string) error {
	if node := a.platformNode(); node != "" {
		res, err := a.client.PlatformActionOn(node, action, service)
		if err != nil {
			return err
		}
		if !res.OK {
			return fmt.Errorf("%s", strings.TrimSpace(res.Output))
		}
		return nil
	}
	args := []string{action}
	if action == "up" {
		args = append(args, "-d")
	}
	if service != "" {
		args = append(args, service)
	}
	return a.composeAction(args...)
}

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
