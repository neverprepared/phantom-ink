package main

import (
	"fmt"
	"strings"
)

// mindwalk is the phantom-mindwalk memory-graph visualizer, run as a docker
// integration (see knownServices "mindwalk"). Its container pulls a published
// image from the fleet registry; phantom-ink injects two values at compose-up
// time that serviceEnv() cannot supply (it is a package func with no *App): the
// ACTIVE profile's memory-vault bearer token, and a brain URL reachable from
// inside the container.
//
// Because the token is baked at `compose up`, the running container is pinned to
// whichever profile was active when it started. Switching the active profile
// therefore requires recreating the container; the Memory Graph panel does this
// by stopping and restarting the service, which re-resolves the env below.

const mindwalkServiceName = "mindwalk"

// serviceComposeEnv returns per-service extra compose env (KEY=VALUE). It is nil
// for services whose compose needs nothing beyond serviceEnv().
func (a *App) serviceComposeEnv(name string) ([]string, error) {
	if name == mindwalkServiceName {
		return a.mindwalkComposeEnv()
	}
	return nil, nil
}

// mindwalkComposeEnv resolves CL_BRAIN_API_TOKEN (active profile's memory token)
// and BRAIN_URL (container-reachable). Any failure is returned so StartService
// surfaces a clear message instead of a container that silently serves nothing.
func (a *App) mindwalkComposeEnv() ([]string, error) {
	profile := a.activeProfileName()
	if profile == "" {
		return nil, fmt.Errorf("no active profile selected")
	}
	_, token, err := a.brainVaultCreds(profile, "memory")
	if err != nil {
		return nil, fmt.Errorf("resolve memory-vault token for profile %q: %w", profile, err)
	}
	return []string{
		"CL_BRAIN_API_TOKEN=" + token,
		// Read the LOCAL mesh daemon — the same source MeshPanel/VaultBrowser
		// browse memory from — NOT BrainHostAPI (the platform base_url host,
		// which points at the remote router/brain when configured for a remote
		// platform). The per-(profile,vault) token is unified across the mesh,
		// so it authenticates against the local daemon.
		"BRAIN_URL=" + containerBrainURL(a.meshURL()),
	}, nil
}

// containerBrainURL rewrites a host-facing brain URL to one reachable from inside
// a container: loopback becomes host.docker.internal; a remote host is already
// reachable and left unchanged.
func containerBrainURL(hostAPI string) string {
	return strings.NewReplacer(
		"127.0.0.1", "host.docker.internal",
		"localhost", "host.docker.internal",
	).Replace(hostAPI)
}
