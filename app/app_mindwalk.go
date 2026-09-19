package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// mindwalk is the phantom-mindwalk memory-graph visualizer, run as a docker
// integration (see knownServices "mindwalk"). Its container needs three values
// resolved at compose-up time that serviceEnv() cannot supply (it is a package
// func with no *App): the ACTIVE profile's memory-vault bearer token, a brain
// URL reachable from inside the container, and — for the build-context v1 — the
// absolute path to the sibling phantom-mindwalk repo used as the build context.
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

// mindwalkComposeEnv resolves CL_BRAIN_API_TOKEN (active profile's memory token),
// BRAIN_URL (container-reachable), and MINDWALK_REPO (build context). Any failure
// is returned so StartService surfaces a clear message instead of a container that
// silently serves nothing.
func (a *App) mindwalkComposeEnv() ([]string, error) {
	profile := a.activeProfileName()
	if profile == "" {
		return nil, fmt.Errorf("no active profile selected")
	}
	_, token, err := a.brainVaultCreds(profile, "memory")
	if err != nil {
		return nil, fmt.Errorf("resolve memory-vault token for profile %q: %w", profile, err)
	}
	repo, err := resolveMindwalkRepo()
	if err != nil {
		return nil, err
	}
	return []string{
		"CL_BRAIN_API_TOKEN=" + token,
		"BRAIN_URL=" + containerBrainURL(a.BrainHostAPI()),
		"MINDWALK_REPO=" + repo,
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

// resolveMindwalkRepo returns the absolute path to the phantom-mindwalk repo for
// the build-context v1 integration. Honors MINDWALK_REPO; otherwise probes the
// sibling-repo layout relative to cwd. A Dockerfile must be present to count.
func resolveMindwalkRepo() (string, error) {
	var candidates []string
	if env := strings.TrimSpace(os.Getenv("MINDWALK_REPO")); env != "" {
		candidates = append(candidates, env)
	}
	if cwd, err := os.Getwd(); err == nil {
		candidates = append(candidates,
			filepath.Join(cwd, "..", "phantom-mindwalk"),       // cwd = code/phantom-ink
			filepath.Join(cwd, "..", "..", "phantom-mindwalk"), // cwd = code/phantom-ink/app
		)
	}
	for _, c := range candidates {
		abs, err := filepath.Abs(c)
		if err != nil {
			continue
		}
		if fi, err := os.Stat(filepath.Join(abs, "Dockerfile")); err == nil && !fi.IsDir() {
			return abs, nil
		}
	}
	return "", fmt.Errorf("phantom-mindwalk repo not found (no Dockerfile at the expected sibling path); set MINDWALK_REPO to its absolute path")
}
