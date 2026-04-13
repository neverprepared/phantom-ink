package main

import (
	"embed"
	"encoding/json"
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

// ensureComposeFile extracts an embedded compose file to the integrations dir
// if it doesn't already exist. Returns the absolute path.
func ensureComposeFile(name string) (string, error) {
	dir := filepath.Join(integrationsDir(), name)
	dest := filepath.Join(dir, "docker-compose.yml")
	if _, err := os.Stat(dest); err == nil {
		return dest, nil // already extracted
	}
	if err := os.MkdirAll(dir, 0755); err != nil {
		return "", fmt.Errorf("create dir: %w", err)
	}
	data, err := embeddedCompose.ReadFile(fmt.Sprintf("compose/%s/docker-compose.yml", name))
	if err != nil {
		return "", fmt.Errorf("read embedded compose for %s: %w", name, err)
	}
	if err := os.WriteFile(dest, data, 0644); err != nil {
		return "", fmt.Errorf("write compose file: %w", err)
	}
	return dest, nil
}

// ServiceDef describes a known infrastructure service.
type ServiceDef struct {
	Name        string `json:"name"`
	Label       string `json:"label"`
	Description string `json:"description"`
	DefaultURL  string `json:"default_url"`
	Port        int    `json:"port"`
	Native      bool   `json:"native"` // true = managed outside docker (e.g. macOS app)
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
		Name:        "qdrant",
		Label:       "Qdrant",
		Description: "Vector database for RAG and semantic search",
		DefaultURL:  "http://localhost:6333",
		Port:        6333,
	},
	{
		Name:        "langfuse",
		Label:       "LangFuse",
		Description: "LLM observability — traces, metrics, and cost tracking",
		DefaultURL:  "http://localhost:3000",
		Port:        3000,
	},
	{
		Name:        "minio",
		Label:       "MinIO",
		Description: "S3-compatible object storage for artifacts",
		DefaultURL:  "http://localhost:9090",
		Port:        9090,
	},
	{
		Name:        "ollama",
		Label:       "Ollama",
		Description: "Local LLM inference server for private model hosting",
		DefaultURL:  "http://localhost:11434",
		Port:        11434,
		Native:      true,
	},

}

// discoverDockerServices finds containers carrying com.neverprepared.service=true
// labels and converts them into ServiceDef entries. Containers already covered
// by knownServices (matched by name label) are skipped — the static definition
// wins. Discovered-only services are marked Native=true so the UI shows status
// but does not offer start/stop controls (phantom-ink doesn't own their compose).
func discoverDockerServices(known map[string]bool) []ServiceDef {
	type labelResult struct {
		Labels map[string]string `json:"Labels"`
		State  string            `json:"State"`
	}

	cmd := exec.Command("docker", "ps", "-a",
		"--filter", "label=com.neverprepared.service=true",
		"--format", "{{json .Labels}}",
	)
	out, err := cmd.Output()
	if err != nil {
		return nil
	}

	var discovered []ServiceDef
	seen := map[string]bool{}

	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var labels map[string]string
		if err := json.Unmarshal([]byte(line), &labels); err != nil {
			continue
		}
		name := labels["com.neverprepared.service.name"]
		if name == "" || known[name] || seen[name] {
			continue
		}
		seen[name] = true

		url := labels["com.neverprepared.service.url"]
		port := 0
		if url != "" {
			if _, portStr, err := net.SplitHostPort(strings.TrimPrefix(strings.TrimPrefix(url, "http://"), "https://")); err == nil {
				fmt.Sscanf(portStr, "%d", &port)
			}
		}
		discovered = append(discovered, ServiceDef{
			Name:        name,
			Label:       labels["com.neverprepared.service.name"],
			Description: labels["com.neverprepared.service.description"],
			DefaultURL:  url,
			Port:        port,
			Native:      true, // managed externally — no start/stop from here
		})
	}
	return discovered
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
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("%s: %w", strings.TrimSpace(string(out)), err)
	}
	return nil
}
