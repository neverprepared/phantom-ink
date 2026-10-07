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

	// Local first: BRAIN_URL below points at the LOCAL mesh daemon, so the token
	// should come from the same source of truth — the profile's own .env — rather
	// than the remote router's brain facade. The facade also provisions on read,
	// so it fails outright when a vault binding is missing from its (read-only)
	// config, which has nothing to do with whether the local daemon would accept
	// the token we already hold.
	local, localErr := a.profileVaultTokens(profile)

	// The router can still fill vaults the .env does not carry.
	var router map[string]string
	var routerErr error
	if res, err := a.client.GetBrainProfileTokens(profile); err != nil {
		routerErr = err
	} else {
		router = map[string]string{}
		for _, t := range res.Tokens {
			if t.Token != "" && t.Vault != "" {
				router[t.Vault] = t.Token
			}
		}
	}

	tokens := mergeVaultTokens(local, router)
	memoryToken := tokens["memory"]
	if memoryToken == "" {
		return nil, fmt.Errorf(
			"no memory-vault token for profile %q (profile env: %v; router: %v)",
			profile, orNone(localErr), orNone(routerErr))
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

// vaultEnvKeys maps a profile .env variable to the brain vault it authenticates.
// These are the unified per-(profile, vault) tokens, identical across the node's
// auth.toml, peers' CL_SYNC__PEERS entries and the client .env — so the value in
// the profile's .env is the same one the local daemon expects.
var vaultEnvKeys = map[string]string{
	"CL_BRAIN_API_TOKEN":  "memory",
	"CL_SKILLS_API_TOKEN": "skills",
	"CL_TODO_API_TOKEN":   "todo",
	"CL_AGENTS_API_TOKEN": "agents",
}

// vaultTokensFromEnvText extracts vault -> token from a profile's .env text.
func vaultTokensFromEnvText(text string) map[string]string {
	env := parseDotenvText(text)
	out := map[string]string{}
	for key, vault := range vaultEnvKeys {
		if tok := env[key]; tok != "" {
			out[vault] = tok
		}
	}
	return out
}

// profileVaultTokens reads vault tokens from the given profile's own .env, so the
// lookup is correct for any profile the app manages, not just the workspace the
// app itself was launched from.
func (a *App) profileVaultTokens(profile string) (map[string]string, error) {
	text, err := a.ReadProfileHostEnv(profile)
	if err != nil {
		return nil, err
	}
	return vaultTokensFromEnvText(text), nil
}

// mergeVaultTokens combines token sources, preferring local per vault while still
// taking vaults only the router knows about.
func mergeVaultTokens(local, router map[string]string) map[string]string {
	out := make(map[string]string, len(local)+len(router))
	for vault, tok := range router {
		out[vault] = tok
	}
	for vault, tok := range local {
		out[vault] = tok
	}
	return out
}

// orNone renders an error for a diagnostic message, or "ok" when there was none.
func orNone(err error) string {
	if err == nil {
		return "ok"
	}
	return err.Error()
}
