package profileimage

import (
	"os"
	"path/filepath"
	"strings"
)

// MergeEnvFiles reads a profile's $WORKSPACE_HOME/.env and .env.secrets and
// returns their merged KEY=VALUE map, with .env.secrets winning on overlap.
//
// The parse rules match phantom-credentials' scripts/env-sync.sh exactly, so
// the env-store mirror the app pushes is byte-for-byte what the manual script
// produced: strip a leading `export `, skip blank/`#` lines and lines without
// `=`, split on the first `=`, trim key and value, and drop one layer of
// matching surrounding quotes. This is deliberately RAW — no key filtering and
// no container rewrites (loopback→host.docker.internal, routerManagedVars):
// that adaptation belongs to the image-bake path (builder.go) and the router's
// forward step, NOT the store. Changing it here would silently diverge what
// the router reads from the established mechanism.
//
// Missing files are treated as empty, not an error (a profile may have only
// one of the two).
func MergeEnvFiles(workspaceHome string) (map[string]string, error) {
	env := map[string]string{}
	// .env first, then .env.secrets so secrets override.
	for _, name := range []string{".env", ".env.secrets"} {
		if err := parseEnvFileInto(filepath.Join(workspaceHome, name), env); err != nil {
			return nil, err
		}
	}
	return env, nil
}

// parseEnvFileInto parses one env file into dst. A non-existent file is a no-op.
func parseEnvFileInto(path string, dst map[string]string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	for _, raw := range strings.Split(string(data), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "export ") {
			line = strings.TrimPrefix(line, "export ")
		}
		idx := strings.IndexByte(line, '=')
		if idx < 0 {
			continue
		}
		k := strings.TrimSpace(line[:idx])
		v := strings.TrimSpace(line[idx+1:])
		if len(v) >= 2 && (v[0] == '"' || v[0] == '\'') && v[len(v)-1] == v[0] {
			v = v[1 : len(v)-1]
		}
		if k != "" {
			dst[k] = v
		}
	}
	return nil
}
