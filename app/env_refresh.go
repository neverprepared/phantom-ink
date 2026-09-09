package main

// Background auto-refresh of a profile's CURATED gateway env store. Curation
// (GatewayEnvEditor) owns which keys live in the store; this keeps their VALUES
// fresh from the host .env/.env.secrets so rotating a secret in 1Password
// propagates to sessions without a manual re-save. It NEVER adds keys the
// operator didn't curate and never removes keys — the key SET is curation's.

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// envRefreshInterval is how often the watcher polls each profile's env files.
const envRefreshInterval = 30 * time.Second

var envKeyRe = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

// brainEndpointVars are router-managed brain ENDPOINT keys that must NEVER be
// curated/refreshed into a profile's gateway-secrets store from its host .env.
// Their host value is the host-facing endpoint (CL_BRAIN_API=127.0.0.1:9998 —
// correct only for host-side pbrainctl); injected into a container it resolves
// to the container's own loopback and the brain daemon is unreachable. The
// router injects the correct session-facing endpoint (host.docker.internal)
// fresh at session create. The per-vault *_TOKEN vars are deliberately NOT here:
// they are the unified tokens and correct in the host .env, so they may ride.
var brainEndpointVars = map[string]bool{
	"CL_BRAIN_API":   true,
	"CL_BRAIN_VAULT": true,
}

// parseDotenvText parses .env-style text into a KEY=VALUE map. Semantics match
// the gateway editor's frontend parseDotenv so a value refreshed here is
// byte-identical to one loaded + saved through the UI: strip a leading
// `export `, skip blank and `#` lines, require a valid shell key, and drop one
// layer of matching surrounding quotes (double quotes also unescape \n \t \").
func parseDotenvText(text string) map[string]string {
	out := map[string]string{}
	for _, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "export ") {
			line = strings.TrimSpace(line[len("export "):])
		}
		eq := strings.IndexByte(line, '=')
		if eq <= 0 {
			continue
		}
		key := strings.TrimSpace(line[:eq])
		if !envKeyRe.MatchString(key) {
			continue
		}
		val := strings.TrimSpace(line[eq+1:])
		if len(val) >= 2 && (val[0] == '"' || val[0] == '\'') && val[len(val)-1] == val[0] {
			q := val[0]
			val = val[1 : len(val)-1]
			if q == '"' {
				val = strings.NewReplacer(`\n`, "\n", `\t`, "\t", `\"`, `"`).Replace(val)
			}
		}
		out[key] = val
	}
	return out
}

// refreshCuratedValues returns a copy of store with the VALUES of already-present
// keys updated from host (where host has that key and its value differs), plus
// the sorted list of keys whose values changed. It never adds a key absent from
// store and never drops one — the key set is owned by curation.
func refreshCuratedValues(store, host map[string]string) (map[string]string, []string) {
	next := make(map[string]string, len(store))
	var changed []string
	for k, v := range store {
		// Never refresh a router-managed endpoint key from the host .env — its
		// host value (127.0.0.1) is wrong inside a container. Keep as-is.
		if brainEndpointVars[k] {
			next[k] = v
			continue
		}
		if hv, ok := host[k]; ok && hv != v {
			next[k] = hv
			changed = append(changed, k)
		} else {
			next[k] = v
		}
	}
	sortStrings(changed)
	return next, changed
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

// refreshProfileEnv refreshes the VALUES of a profile's already-curated env
// store keys from its host .env/.env.secrets. It returns the keys whose values
// changed. A missing/empty store, a broker/gateway that won't answer, or absent
// host files are all no-ops (nil, nil) — the refresh never seeds keys and never
// writes an empty map. Only a failed WRITE returns an error.
func (a *App) refreshProfileEnv(profile, workspaceHome string) ([]string, error) {
	if a.client == nil {
		return nil, nil
	}
	store, err := a.client.GetGatewayProfileEnv(profile)
	if err != nil || len(store) == 0 {
		return nil, nil // broker down / gateway locked / nothing curated
	}
	text, err := readHostEnvText(workspaceHome)
	if err != nil {
		return nil, nil // no host files to read
	}
	next, changed := refreshCuratedValues(store, parseDotenvText(text))
	if len(changed) == 0 {
		return nil, nil
	}
	if err := a.client.SetGatewayProfileEnv(profile, next); err != nil {
		return nil, err
	}
	return changed, nil
}

// startEnvRefreshWatcher polls every profile's host env files and refreshes any
// profile whose files changed (see refreshProfileEnv). Dependency-free mtime
// poll; runs while the app is open and stops when the app context is cancelled.
func (a *App) startEnvRefreshWatcher() {
	go func() {
		ticker := time.NewTicker(envRefreshInterval)
		defer ticker.Stop()
		seen := map[string]time.Time{} // "<home>/<file>" -> last observed mtime
		// Reconcile once on startup, then on every subsequent change.
		a.scanAndRefreshEnv(seen)
		for {
			select {
			case <-a.ctx.Done():
				return
			case <-ticker.C:
				a.scanAndRefreshEnv(seen)
			}
		}
	}()
}

// scanAndRefreshEnv refreshes each profile whose .env/.env.secrets mtime has
// advanced since last seen (first sight counts, so startup reconciles once).
// `seen` is mutated in place.
func (a *App) scanAndRefreshEnv(seen map[string]time.Time) {
	profiles, err := scanProfiles(defaultWorkspacesRoot())
	if err != nil {
		return
	}
	for _, p := range profiles {
		if p.WorkspaceHome == "" {
			continue
		}
		changed := false
		for _, name := range []string{".env", ".env.secrets"} {
			fp := filepath.Join(p.WorkspaceHome, name)
			fi, err := os.Stat(fp)
			if err != nil {
				continue
			}
			if prev, ok := seen[fp]; !ok || fi.ModTime().After(prev) {
				seen[fp] = fi.ModTime()
				changed = true
			}
		}
		if !changed {
			continue
		}
		keys, err := a.refreshProfileEnv(p.Name, p.WorkspaceHome)
		if err != nil {
			logErr("env-refresh: %s: %v", p.Name, err)
			continue
		}
		if len(keys) > 0 && a.ctx != nil {
			runtime.EventsEmit(a.ctx, "gateway:env-refreshed",
				map[string]any{"profile": p.Name, "keys": keys})
		}
	}
}
