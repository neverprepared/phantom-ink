package profileimage

// Credential-bundle collection: resolve enabled sources against the
// operator's filesystem and produce the tar.gz + manifest the daemon's
// gateway_bundle module consumes (see brainbox gateway_bundle.py for the
// manifest contract). Missing files are skipped silently — an absent
// credential is normal, not an error; size-cap overflows skip with a
// warning so one fat file can't block the rest of the capture.

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// ManifestEntry mirrors gateway_bundle.py's manifest schema (version 1).
type ManifestEntry struct {
	Source    string            `json:"source"`
	Kind      string            `json:"kind"` // catalog | custom
	Audiences []string          `json:"audiences"`
	Files     []string          `json:"files"`
	Env       map[string]string `json:"env,omitempty"`
}

// Manifest is the bundle's self-description; the daemon is driven by it.
type Manifest struct {
	Version    int             `json:"version"`
	CapturedAt string          `json:"captured_at"`
	AppVersion string          `json:"app_version"`
	Entries    []ManifestEntry `json:"entries"`
}

// CustomSourceDef is the operator-authored definition of a non-catalog
// source (stored as JSON in the app DB).
type CustomSourceDef struct {
	Globs    []string          `json:"globs"`
	Audience string            `json:"audience"` // gateway | session | both
	EnvMap   map[string]string `json:"env_map,omitempty"`
}

// ResolvedSource is one enabled source ready for collection.
type ResolvedSource struct {
	Source BundleSource
	Kind   string // catalog | custom
}

// CollectResult reports what a capture produced.
type CollectResult struct {
	TarGz    []byte
	Manifest Manifest
	Warnings []string
}

var validSourceName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)

// CustomSource converts an operator definition into a BundleSource so it
// flows through the same collection code as catalog entries.
func CustomSource(name string, def CustomSourceDef) (BundleSource, error) {
	if !validSourceName.MatchString(name) {
		return BundleSource{}, fmt.Errorf("invalid source name %q", name)
	}
	audience := def.Audience
	if audience == "" {
		audience = AudienceSession // the safer default for unknown material
	}
	src := BundleSource{Name: name, Label: name, Audience: audience}
	for _, g := range def.Globs {
		src.Items = append(src.Items, BundleItem{Rel: ".", Glob: true, Candidates: []string{g}})
	}
	// def.EnvMap is applied at collection time (CollectBundle's customEnv) —
	// mappings are operator-authored, and the daemon re-filters them anyway.
	return src, nil
}

// CollectBundle captures every enabled source into a tar.gz + manifest.
// customEnv carries custom sources' env maps (keyed by source name) since
// their mappings are operator-authored rather than item-derived.
func CollectBundle(
	profile, workspaceHome string,
	sources []ResolvedSource,
	customEnv map[string]map[string]string,
	appVersion string,
) (CollectResult, error) {
	profileEnv := parseProfileEnv(workspaceHome)
	manifest := Manifest{
		Version:    1,
		CapturedAt: time.Now().UTC().Format(time.RFC3339),
		AppVersion: appVersion,
	}
	var warnings []string

	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	var total int64

	addFile := func(src BundleSource, hostPath, rel string, used *int64) bool {
		info, err := os.Stat(hostPath)
		if err != nil || !info.Mode().IsRegular() {
			return false
		}
		cap := src.MaxBytes
		if cap == 0 {
			cap = perSourceCapBytes
		}
		if *used+info.Size() > cap || total+info.Size() > totalCapBytes {
			warnings = append(warnings, fmt.Sprintf("%s: %s skipped (size cap)", src.Name, hostPath))
			return false
		}
		data, err := os.ReadFile(hostPath)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("%s: %s unreadable: %v", src.Name, hostPath, err))
			return false
		}
		name := src.Name + "/" + strings.TrimPrefix(rel, "./")
		hdr := &tar.Header{Name: name, Mode: 0o600, Size: int64(len(data)), ModTime: info.ModTime()}
		if err := tw.WriteHeader(hdr); err != nil {
			return false
		}
		if _, err := tw.Write(data); err != nil {
			return false
		}
		*used += info.Size()
		total += info.Size()
		return true
	}

	for _, rs := range sources {
		src := rs.Source
		entry := ManifestEntry{
			Source:    src.Name,
			Kind:      rs.Kind,
			Audiences: []string{src.Audience},
			Env:       map[string]string{},
		}
		var used int64
		for _, item := range src.Items {
			if item.Glob {
				for _, pattern := range item.Candidates {
					matches, _ := filepath.Glob(expandPath(pattern, workspaceHome, profileEnv))
					for _, m := range matches {
						rel := filepath.Base(m)
						if item.Rel != "" && item.Rel != "." {
							rel = item.Rel + "/" + rel
						}
						if addFile(src, m, rel, &used) {
							entry.Files = append(entry.Files, rel)
						}
					}
					if len(matches) > 0 {
						break // first candidate pattern with matches wins
					}
				}
				continue
			}
			// Single file: override var (mount-table convention) then candidates.
			var candidates []string
			if item.OverrideVar != "" {
				if v := profileEnv[item.OverrideVar]; v != "" {
					candidates = append(candidates, v)
				}
			}
			candidates = append(candidates, item.Candidates...)
			for _, c := range candidates {
				hostPath := expandPath(c, workspaceHome, profileEnv)
				if addFile(src, hostPath, item.Rel, &used) {
					entry.Files = append(entry.Files, item.Rel)
					if item.EnvVar != "" {
						entry.Env[item.EnvVar] = src.Name + "/" + item.Rel
					}
					break
				}
			}
		}
		// Custom sources carry operator-authored env maps (daemon re-filters).
		if m := customEnv[src.Name]; len(m) > 0 {
			for k, v := range m {
				entry.Env[k] = v
			}
		}
		if len(entry.Files) == 0 {
			continue // nothing found for this source — omit from manifest
		}
		if len(entry.Env) == 0 {
			entry.Env = nil
		}
		manifest.Entries = append(manifest.Entries, entry)
	}

	mdata, err := json.Marshal(manifest)
	if err != nil {
		return CollectResult{}, fmt.Errorf("marshal manifest: %w", err)
	}
	if err := tw.WriteHeader(&tar.Header{
		Name: "manifest.json", Mode: 0o600, Size: int64(len(mdata)), ModTime: time.Now(),
	}); err != nil {
		return CollectResult{}, err
	}
	if _, err := tw.Write(mdata); err != nil {
		return CollectResult{}, err
	}
	if err := tw.Close(); err != nil {
		return CollectResult{}, err
	}
	if err := gz.Close(); err != nil {
		return CollectResult{}, err
	}
	return CollectResult{TarGz: buf.Bytes(), Manifest: manifest, Warnings: warnings}, nil
}

// parseProfileEnv reads the profile's .env + .env.secrets the same way
// injectEnvFile does (KEY=value lines, export prefix stripped), but keeps
// values raw — expansion happens per-path in expandPath.
func parseProfileEnv(workspaceHome string) map[string]string {
	env := map[string]string{}
	for _, f := range []string{".env", ".env.secrets"} {
		data, err := os.ReadFile(filepath.Join(workspaceHome, f))
		if err != nil {
			continue
		}
		for _, raw := range strings.Split(string(data), "\n") {
			line := strings.TrimSpace(raw)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			line = strings.TrimPrefix(line, "export ")
			idx := strings.IndexByte(line, '=')
			if idx <= 0 {
				continue
			}
			key := strings.TrimSpace(line[:idx])
			if !validEnvKey.MatchString(key) {
				continue
			}
			val := strings.TrimSpace(line[idx+1:])
			val = strings.Trim(val, `"'`)
			env[key] = val
		}
	}
	return env
}
