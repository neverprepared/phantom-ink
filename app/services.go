package main

import (
	"crypto/sha256"
	"embed"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

//go:embed compose/*
var embeddedCompose embed.FS

// integrationsDir returns ~/.config/phantom-ink/integrations, creating it if needed.
// Errors creating the directory are logged to stderr; callers receive the path regardless
// so that subsequent file operations will surface their own errors naturally.
func integrationsDir() string {
	dir := filepath.Join(os.Getenv("HOME"), ".config", "phantom-ink", "integrations")
	if err := os.MkdirAll(dir, 0755); err != nil {
		fmt.Fprintf(os.Stderr, "warning: failed to create integrations dir %q: %v\n", dir, err)
	}
	return dir
}

// ensureComposeFile extracts an embedded compose directory to the integrations dir.
// A SHA-256 hash of docker-compose.yml is stored alongside; if it differs the
// entire directory (including config subdirs) is re-extracted so updates to
// bundled compose files are always applied.
func ensureComposeFile(name string) (string, error) {
	dir := filepath.Join(integrationsDir(), name)
	dest := filepath.Join(dir, "docker-compose.yml")
	hashDest := filepath.Join(dir, "docker-compose.hash")

	currentHash, err := hashEmbedDir(fmt.Sprintf("compose/%s", name))
	if err != nil {
		return "", fmt.Errorf("hash embedded dir for %s: %w", name, err)
	}

	if stored, err := os.ReadFile(hashDest); err == nil {
		if strings.TrimSpace(string(stored)) == currentHash {
			return dest, nil // already extracted and up to date
		}
	}

	if err := extractEmbedDir(fmt.Sprintf("compose/%s", name), dir); err != nil {
		return "", fmt.Errorf("extract compose dir for %s: %w", name, err)
	}
	if err := os.WriteFile(hashDest, []byte(currentHash), 0644); err != nil {
		return "", fmt.Errorf("write compose hash: %w", err)
	}
	return dest, nil
}

// extractEmbedDir recursively extracts all files from an embedded directory to dest.
func extractEmbedDir(embedPath, destDir string) error {
	entries, err := embeddedCompose.ReadDir(embedPath)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(destDir, 0755); err != nil {
		return err
	}
	for _, entry := range entries {
		src := embedPath + "/" + entry.Name()
		dst := filepath.Join(destDir, entry.Name())
		if entry.IsDir() {
			if err := extractEmbedDir(src, dst); err != nil {
				return err
			}
			continue
		}
		fileData, err := embeddedCompose.ReadFile(src)
		if err != nil {
			return err
		}
		if err := os.WriteFile(dst, fileData, 0644); err != nil {
			return err
		}
	}
	return nil
}

// hashEmbedDir returns a hex SHA-256 over the sorted contents of all files
// under embedPath, so any change to any embedded file invalidates the hash.
func hashEmbedDir(embedPath string) (string, error) {
	h := sha256.New()
	var walk func(string) error
	walk = func(p string) error {
		entries, err := embeddedCompose.ReadDir(p)
		if err != nil {
			return err
		}
		for _, entry := range entries {
			child := p + "/" + entry.Name()
			if entry.IsDir() {
				if err := walk(child); err != nil {
					return err
				}
				continue
			}
			data, err := embeddedCompose.ReadFile(child)
			if err != nil {
				return err
			}
			fmt.Fprintf(h, "%s:", child)
			h.Write(data)
		}
		return nil
	}
	if err := walk(embedPath); err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", h.Sum(nil)), nil
}

// serviceEnv returns the environment variables needed for a named service's
// compose stack. It starts from the current process environment and adds
// service-specific defaults for any variables that are not already set.
// For qdrant, it also ensures the data directory exists before compose runs.
func serviceEnv(name string) []string {
	env := os.Environ()
	switch name {
	case "qdrant":
		if os.Getenv("QDRANT_DATA_DIR") == "" {
			dir := filepath.Join(os.Getenv("HOME"), ".config", "phantom-ink", "qdrant", "storage")
			if err := os.MkdirAll(dir, 0755); err != nil {
				fmt.Fprintf(os.Stderr, "warning: failed to create qdrant data dir %q: %v\n", dir, err)
			}
			env = append(env, "QDRANT_DATA_DIR="+dir)
		}
	}
	return env
}

// ServiceDef describes a known infrastructure service.
type ServiceDef struct {
	Name        string `json:"name"`
	Label       string `json:"label"`
	Description string `json:"description"`
	DefaultURL  string `json:"default_url"`
	Port        int    `json:"port"`
	Native      bool   `json:"native"`   // true = managed outside docker (e.g. macOS app)
	Platform    bool   `json:"platform"` // true = belongs to the phantom-platform group,
	// surfaced by the Platform Services card, not the Integrations toggles. The
	// entry is kept (its DefaultURL still backs opensearchAPIURL / minio address
	// resolution) but hidden from ListServices.
}

// ServiceStatus is the runtime state of a service.
type ServiceStatus struct {
	ServiceDef
	Enabled   bool   `json:"enabled"`
	Remote    bool   `json:"remote"`
	LocalURL  string `json:"local_url"`
	RemoteURL string `json:"remote_url"`
	URL       string `json:"url"` // active URL based on current mode
	Running   bool   `json:"running"`
}

// ServiceConfig persists user preferences per service.
type ServiceConfig struct {
	Enabled   bool   `json:"enabled"`
	Remote    bool   `json:"remote"`     // true = remote host, no local container management
	LocalURL  string `json:"local_url"`  // localhost URL (editable, defaults to service default)
	RemoteURL string `json:"remote_url"` // remote host URL
}

// ActiveURL returns the URL for the current mode.
func (c ServiceConfig) ActiveURL(defaultURL string) string {
	if c.Remote {
		if c.RemoteURL != "" {
			return c.RemoteURL
		}
		return ""
	}
	if c.LocalURL != "" {
		return c.LocalURL
	}
	return defaultURL
}

// knownServices defines the infrastructure services available in the repo.
// ComposePath is relative to the repo root (resolved at runtime).
var knownServices = []ServiceDef{
	{
		Name:        "langfuse",
		Label:       "LangFuse",
		Description: "LLM observability — traces, metrics, and cost tracking",
		DefaultURL:  "http://localhost:3000",
		Port:        3000,
	},
	{
		Name:        "opensearch",
		Label:       "OpenSearch",
		Description: "OpenTelemetry signal store — traces, metrics, and logs via OTLP → OpenSearch + Dashboards",
		DefaultURL:  "http://localhost:5601",
		Port:        5601,
		Platform:    true,
	},
	{
		Name:        "ollama",
		Label:       "Ollama",
		Description: "Local LLM inference server for private model hosting",
		DefaultURL:  "http://localhost:11434",
		Port:        11434,
		Native:      true,
		Platform:    true,
	},
	{
		Name:        "minio",
		Label:       "MinIO",
		Description: "S3-compatible artifact store — vault + loops + sessions",
		DefaultURL:  "http://localhost:9090",
		Port:        9090,
		Platform:    true,
	},
	{
		Name:        "phantom-brain-mesh",
		Label:       "Phantom-Brain Mesh",
		Description: "p2p phantom-brain memory mesh — local node + peers with sync stats",
		DefaultURL:  "http://127.0.0.1:9998",
		Port:        9998,
		Native:      true, // the mesh daemon is managed outside docker
	},
}

// isPortOpen checks if a TCP port is accepting connections.
func isPortOpen(port int) bool {
	conn, err := net.DialTimeout("tcp", fmt.Sprintf("localhost:%d", port), 1*time.Second)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

// isHostReachable TCP-dials an arbitrary "host:port" (unlike isPortOpen, which is
// localhost-only). Accepts bare host:port or a URL — scheme/path are stripped and
// a missing port defaults by scheme (https→443, http→80, else 443). Used for the
// remote Docker registry reachability indicator.
func isHostReachable(endpoint string) bool {
	e := strings.TrimSpace(endpoint)
	if e == "" {
		return false
	}
	defaultPort := "443"
	if strings.HasPrefix(e, "http://") {
		defaultPort = "80"
	}
	e = strings.TrimPrefix(e, "https://")
	e = strings.TrimPrefix(e, "http://")
	if i := strings.IndexByte(e, '/'); i >= 0 { // strip any path
		e = e[:i]
	}
	if !strings.Contains(e, ":") {
		e = e + ":" + defaultPort
	}
	conn, err := net.DialTimeout("tcp", e, 2*time.Second)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

// isComposeRunning checks if any containers are running for a service's compose file.
func isComposeRunning(name string) bool {
	composePath, err := ensureComposeFile(name)
	if err != nil {
		return false
	}
	cmd := exec.Command("docker", "compose", "-f", composePath, "ps", "--status", "running", "-q")
	out, err := cmd.Output()
	if err != nil {
		return false
	}
	return strings.TrimSpace(string(out)) != ""
}

// isServiceRunning checks whether a service is running.
// Native or remote services use a TCP port probe; local docker services use compose ps.
func isServiceRunning(def ServiceDef, cfg ServiceConfig) bool {
	if def.Native || cfg.Remote {
		return isPortOpen(def.Port)
	}
	return isComposeRunning(def.Name)
}

// composeUp starts a service's docker compose stack.
func composeUp(name string) error {
	composePath, err := ensureComposeFile(name)
	if err != nil {
		return err
	}
	cmd := exec.Command("docker", "compose", "-f", composePath, "up", "-d")
	cmd.Env = serviceEnv(name)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("%s: %w", strings.TrimSpace(string(out)), err)
	}
	return nil
}

// composeDown stops a service's docker compose stack.
func composeDown(name string) error {
	composePath, err := ensureComposeFile(name)
	if err != nil {
		return err
	}
	cmd := exec.Command("docker", "compose", "-f", composePath, "down")
	cmd.Env = serviceEnv(name)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("%s: %w", strings.TrimSpace(string(out)), err)
	}
	return nil
}
