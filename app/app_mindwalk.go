package main

import (
	"encoding/json"
	"fmt"
	"strings"
)

// mindwalk is the phantom-mindwalk memory-graph visualizer, run as a docker
// integration (see knownServices "mindwalk"). Its container pulls a published
// image from the fleet registry; phantom-ink injects, at compose-up time, values
// that serviceEnv() cannot supply (it is a package func with no *App): ALL of the
// active profile's vault tokens (so the in-app vault picker can switch between
// memory / skills / agents / …), and a brain URL reachable from inside the
// container.
//
// Because the tokens are baked at `compose up`, the running container is pinned
// to whichever profile was active when it started. Switching the active profile
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

// mindwalkComposeEnv resolves the active profile's vault tokens and a
// container-reachable BRAIN_URL. Every token is for the ACTIVE profile only (no
// cross-profile reach). Any failure is returned so StartService surfaces a clear
// message instead of a container that silently serves nothing.
func (a *App) mindwalkComposeEnv() ([]string, error) {
	profile := a.activeProfileName()
	if profile == "" {
		return nil, fmt.Errorf("no active profile selected")
	}
	res, err := a.client.GetBrainProfileTokens(profile)
	if err != nil {
		return nil, fmt.Errorf("resolve brain tokens for profile %q: %w", profile, err)
	}
	tokens := map[string]string{}
	for _, t := range res.Tokens {
		if t.Token != "" && t.Vault != "" {
			tokens[t.Vault] = t.Token
		}
	}
	memoryToken := tokens["memory"]
	if memoryToken == "" {
		return nil, fmt.Errorf("no memory-vault token for profile %q", profile)
	}
	tokensJSON, err := json.Marshal(tokens)
	if err != nil {
		return nil, fmt.Errorf("marshal vault tokens: %w", err)
	}
	return []string{
		// default vault + its token (the vault picker overrides per request)
		"CL_BRAIN_API_TOKEN=" + memoryToken,
		"CL_BRAIN_VAULT=memory",
		"CL_BRAIN_VAULT_TOKENS=" + string(tokensJSON),
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
