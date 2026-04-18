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

// ensureComposeFile extracts an embedded compose file to the integrations dir.
// A SHA-256 hash of the embedded content is stored alongside the file; if the
// stored hash differs from the embedded content the file is re-extracted so
// updates to the bundled compose files are always applied.
func ensureComposeFile(name string) (string, error) {
	dir := filepath.Join(integrationsDir(), name)
	dest := filepath.Join(dir, "docker-compose.yml")
	hashDest := filepath.Join(dir, "docker-compose.hash")

	data, err := embeddedCompose.ReadFile(fmt.Sprintf("compose/%s/docker-compose.yml", name))
	if err != nil {
		return "", fmt.Errorf("read embedded compose for %s: %w", name, err)
	}

	sum := sha256.Sum256(data)
	currentHash := fmt.Sprintf("%x", sum)

	if stored, err := os.ReadFile(hashDest); err == nil {
		if strings.TrimSpace(string(stored)) == currentHash {
			return dest, nil // already extracted and up to date
		}
	}

	if err := os.MkdirAll(dir, 0755); err != nil {
		return "", fmt.Errorf("create dir: %w", err)
	}
	if err := os.WriteFile(dest, data, 0644); err != nil {
		return "", fmt.Errorf("write compose file: %w", err)
	}
	if err := os.WriteFile(hashDest, []byte(currentHash), 0644); err != nil {
		return "", fmt.Errorf("write compose hash: %w", err)
	}
	return dest, nil
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
		Name:        "uptime-kuma",
		Label:       "Uptime Kuma",
		Description: "Lightweight service health monitoring and alerting",
		DefaultURL:  "http://localhost:3001",
		Port:        3001,
	},
	{
		Name:        "ollama",
		Label:       "Ollama",
		Description: "Local LLM inference server for private model hosting",
		DefaultURL:  "http://localhost:11434",
		Port:        11434,
		Native:      true,
	},
	{
		Name:        "n8n",
		Label:       "n8n",
		Description: "Workflow automation — webhooks, integrations, and scheduled tasks",
		DefaultURL:  "http://localhost:5678",
		Port:        5678,
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
