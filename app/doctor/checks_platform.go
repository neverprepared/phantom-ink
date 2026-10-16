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
		}
		return res
	}
	res.State = StatePass
	res.Detail = fmt.Sprintf("%s answers on %s (HTTP %d)", c.ServiceName, found.Addr, status)
	return res
}
