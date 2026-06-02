// Package profileimage builds pre-configured brainbox profile container images
// and pushes them to a private Docker registry.
//
// Build pipeline:
//  1. Pull the base brainbox image
//  2. Create a short-lived configure container
//  3. Inject SSH keys (from workspaceHome/.ssh or via `op read`)
//  4. Inject Claude credentials (.credentials.json, .claude.json, settings.json)
//  5. docker commit → registry tag
//  6. docker push
//  7. Remove the configure container
package profileimage

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// BuildOptions controls the profile image build.
type BuildOptions struct {
	// Profile is the workspace profile name (e.g. "personal").
	Profile string
	// WorkspaceHome is the absolute path to the profile's workspace home.
	WorkspaceHome string
	// BaseImage is the brainbox base image to build from (e.g. "brainbox").
	BaseImage string
	// RegistryURL is the private registry address (e.g. "registry.internal:5000").
	RegistryURL string
	// RegistryUsername and RegistryPassword are used for docker login.
	RegistryUsername string
	RegistryPassword string
	// Progress receives status messages during the build.
	Progress func(string)
}

// BuildResult holds the outcome of a successful build.
type BuildResult struct {
	// Tag is the full registry-qualified image tag that was pushed.
	Tag string
	// Digest is the image digest returned by docker push.
	Digest string
	// EnvKey is the AES-256 key (hex) used to encrypt .env.enc in the image.
	// Must be stored by the caller and passed as PROFILE_ENV_KEY at container startup.
	EnvKey string
}

func (o *BuildOptions) progress(msg string) {
	if o.Progress != nil {
		o.Progress(msg)
	}
}

// imageTag returns the full registry-qualified tag for this profile.
func (o *BuildOptions) imageTag() string {
	return fmt.Sprintf("%s/brainbox-profile:%s", strings.TrimRight(o.RegistryURL, "/"), o.Profile)
}

// Build runs the full pipeline and returns the pushed image digest.
func Build(opts BuildOptions) (BuildResult, error) {
	containerName := fmt.Sprintf("phantom-profile-build-%s", opts.Profile)

	// Clean up any leftover container from a previous failed build.
	_ = run("docker", "rm", "-f", containerName)

	// Generate a single encryption key used for both .env.enc and .claude.enc.
	// Always generated so both encrypted files can share the same PROFILE_ENV_KEY.
	envKey, err := generateEnvKey()
	if err != nil {
		return BuildResult{}, fmt.Errorf("generate env key: %w", err)
	}

	// 1. Pull base image.
	opts.progress("Pulling base image…")
	if err := run("docker", "pull", opts.BaseImage); err != nil {
		return BuildResult{}, fmt.Errorf("pull base image: %w", err)
	}

	// 2. Create configure container (not started yet).
	opts.progress("Creating configure container…")
	if err := run("docker", "create", "--name", containerName, opts.BaseImage, "sleep", "infinity"); err != nil {
		return BuildResult{}, fmt.Errorf("create container: %w", err)
	}
	defer func() { _ = run("docker", "rm", "-f", containerName) }()

	// 3. Start container so we can exec into it.
	if err := run("docker", "start", containerName); err != nil {
		return BuildResult{}, fmt.Errorf("start container: %w", err)
	}

	// 4. Inject SSH keys.
	opts.progress("Injecting SSH keys…")
	if err := injectSSHKeys(containerName, opts); err != nil {
		return BuildResult{}, fmt.Errorf("inject SSH keys: %w", err)
	}

	// 5. Inject Claude credentials (encrypted as .claude.enc).
	opts.progress("Injecting Claude credentials…")
	if err := injectClaudeCredentials(containerName, opts, envKey); err != nil {
		return BuildResult{}, fmt.Errorf("inject Claude credentials: %w", err)
	}

	// 6. Inject profile env vars encrypted as /home/developer/.env.enc.
	opts.progress("Injecting profile environment…")
	if err := injectEnvFile(containerName, opts, envKey); err != nil {
		return BuildResult{}, fmt.Errorf("inject env file: %w", err)
	}

	// 7. Commit while the container is still running — Docker Desktop's
	// containerd storage driver fails to compute layer diffs on stopped
	// containers that had directories created via exec.
	tag := opts.imageTag()
	opts.progress(fmt.Sprintf("Committing image as %s…", tag))
	if err := run("docker", "commit", containerName, tag); err != nil {
		return BuildResult{}, fmt.Errorf("commit image: %w", err)
	}

	// 7. Stop container after commit.
	opts.progress("Stopping container…")
	_ = run("docker", "stop", containerName)

	// 8. Login and push.
	opts.progress("Logging in to registry…")
	if err := registryLogin(opts); err != nil {
		return BuildResult{}, fmt.Errorf("registry login: %w", err)
	}

	opts.progress("Pushing image…")
	digest, err := push(tag)
	if err != nil {
		return BuildResult{}, fmt.Errorf("push image: %w", err)
	}

	opts.progress("Done.")
	return BuildResult{Tag: tag, Digest: digest, EnvKey: envKey}, nil
}

// hostOnlyVars are stripped from the profile env before baking into the image.
// These are host-specific values that would be wrong or harmful inside a container.
var hostOnlyVars = map[string]bool{
	"SSH_AUTH_SOCK": true, "GIT_SSH_COMMAND": true, "TMPDIR": true,
	"SHELL": true, "TERM_PROGRAM": true, "TERM_SESSION_ID": true,
	"HOME": true, "USER": true, "LOGNAME": true,
	"PATH": true, "PWD": true, "OLDPWD": true, "SHLVL": true,
	"XDG_CONFIG_HOME": true, "CLAUDE_CONFIG_DIR": true, "GEMINI_CONFIG_DIR": true,
	"WORKSPACE_HOME": true, // rewritten to /home/developer below
}

// generateEnvKey returns a random 32-byte key as a 64-char hex string.
func generateEnvKey() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// encryptEnv encrypts plaintext using openssl AES-256-CBC with PBKDF2 and
// returns the ciphertext bytes. The same openssl invocation in the container
// can decrypt it with the same key.
func encryptEnv(plaintext, key string) ([]byte, error) {
	cmd := exec.Command("openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "100000",
		"-pass", "pass:"+key)
	cmd.Stdin = strings.NewReader(plaintext)
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("openssl enc: %w", err)
	}
	return out, nil
}

// injectEnvFile reads the profile's .env and .env.secrets, filters host-only
// vars, encrypts with the provided key, and writes the ciphertext to
// /home/developer/.env.enc inside the container. Skips silently if there are
// no vars beyond the identity pair (WORKSPACE_PROFILE / WORKSPACE_HOME).
func injectEnvFile(container string, opts BuildOptions, key string) error {
	var lines []string

	lines = append(lines,
		"WORKSPACE_PROFILE="+opts.Profile,
		"WORKSPACE_HOME=/home/developer",
	)

	readEnvFile := func(path string) {
		data, err := os.ReadFile(path)
		if err != nil {
			return
		}
		for _, raw := range strings.Split(string(data), "\n") {
			line := strings.TrimSpace(raw)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			if strings.HasPrefix(line, "export ") {
				line = line[7:]
			}
			varName := line
			if idx := strings.IndexByte(line, '='); idx >= 0 {
				varName = line[:idx]
			}
			varName = strings.TrimSpace(varName)
			if hostOnlyVars[varName] || varName == "WORKSPACE_PROFILE" || varName == "WORKSPACE_HOME" {
				continue
			}
			line = strings.ReplaceAll(line, opts.WorkspaceHome, "/home/developer")
			lines = append(lines, line)
		}
	}

	readEnvFile(filepath.Join(opts.WorkspaceHome, ".env"))
	readEnvFile(filepath.Join(opts.WorkspaceHome, ".env.secrets"))

	if len(lines) <= 2 {
		return nil // nothing beyond identity vars
	}

	plaintext := strings.Join(lines, "\n") + "\n"
	ciphertext, err := encryptEnv(plaintext, key)
	if err != nil {
		return err
	}

	if err := writeFileToContainer(container, "/home/developer/.env.enc", ciphertext, "600"); err != nil {
		return fmt.Errorf("write .env.enc: %w", err)
	}
	return nil
}

// injectSSHKeys copies ~/.ssh from workspaceHome into the container.
// Falls back to `op read` for any key that does not exist as a file.
func injectSSHKeys(container string, opts BuildOptions) error {
	sshDir := filepath.Join(opts.WorkspaceHome, ".ssh")

	// Ensure target dir exists in container.
	if err := dockerExecSh(container, "mkdir -p /home/developer/.ssh && chmod 700 /home/developer/.ssh"); err != nil {
		return err
	}

	entries, err := os.ReadDir(sshDir)
	if err != nil {
		return nil // no .ssh dir, skip silently
	}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		data, err := os.ReadFile(filepath.Join(sshDir, e.Name()))
		if err != nil {
			continue
		}
		mode := "644"
		if !strings.HasSuffix(e.Name(), ".pub") {
			mode = "600"
		}
		if err := writeFileToContainer(container, "/home/developer/.ssh/"+e.Name(), data, mode); err != nil {
			return fmt.Errorf("write SSH key %s: %w", e.Name(), err)
		}
	}
	return nil
}

// injectClaudeCredentials packs Claude auth files into a JSON bundle, encrypts
// with the provided key, and writes the ciphertext to /home/developer/.claude.enc.
// ttyd-wrapper.sh decrypts at container startup using the same PROFILE_ENV_KEY.
// No plaintext credential files are written into the image layers.
func injectClaudeCredentials(container string, opts BuildOptions, key string) error {
	claudeConfigDir := filepath.Join(opts.WorkspaceHome, ".claude")

	// Collect whichever files exist; skip silently if absent.
	type entry struct {
		field string
		path  string
	}
	sources := []entry{
		{"credentials_json", filepath.Join(claudeConfigDir, ".credentials.json")},
		{"claude_json", filepath.Join(claudeConfigDir, ".claude.json")},
		{"settings_json", filepath.Join(claudeConfigDir, "settings.json")},
	}

	bundle := make(map[string]string, len(sources))
	for _, s := range sources {
		data, err := os.ReadFile(s.path)
		if err != nil {
			continue
		}
		bundle[s.field] = string(data)
	}

	if len(bundle) == 0 {
		return nil // no Claude credentials found, skip
	}

	// Serialise to JSON then encrypt — same openssl cipher as .env.enc so
	// ttyd-wrapper.sh can decrypt both with one key.
	plaintext, err := marshalJSON(bundle)
	if err != nil {
		return fmt.Errorf("marshal claude bundle: %w", err)
	}

	ciphertext, err := encryptEnv(plaintext, key)
	if err != nil {
		return fmt.Errorf("encrypt claude bundle: %w", err)
	}

	if err := writeFileToContainer(container, "/home/developer/.claude.enc", ciphertext, "600"); err != nil {
		return fmt.Errorf("write .claude.enc: %w", err)
	}
	return nil
}

// registryLogin runs docker login for the configured registry.
func registryLogin(opts BuildOptions) error {
	if opts.RegistryUsername == "" {
		return nil // anonymous registry, no login needed
	}
	cmd := exec.Command("docker", "login",
		"--username", opts.RegistryUsername,
		"--password-stdin",
		opts.RegistryURL,
	)
	cmd.Stdin = strings.NewReader(opts.RegistryPassword)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("docker login: %w — %s", err, string(out))
	}
	return nil
}

// push runs docker push and returns the digest from the output.
func push(tag string) (string, error) {
	out, err := exec.Command("docker", "push", tag).CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("docker push: %w — %s", err, string(out))
	}
	// Extract digest from output line like "latest: digest: sha256:abc size: 123"
	for _, line := range strings.Split(string(out), "\n") {
		if strings.Contains(line, "digest:") {
			parts := strings.Fields(line)
			for i, p := range parts {
				if p == "digest:" && i+1 < len(parts) {
					return parts[i+1], nil
				}
			}
		}
	}
	return "", nil
}

// marshalJSON encodes v as a JSON string (newline-terminated).
func marshalJSON(v any) (string, error) {
	b, err := json.Marshal(v)
	if err != nil {
		return "", err
	}
	return string(b) + "\n", nil
}

// writeFileToContainer writes data into a container path by piping base64
// via stdin to avoid ARG_MAX limits on large files.
func writeFileToContainer(container, destPath string, data []byte, mode string) error {
	encoded := base64.StdEncoding.EncodeToString(data)
	script := fmt.Sprintf("base64 -d > %s && chmod %s %s", destPath, mode, destPath)
	cmd := exec.Command("docker", "exec", "-i", container, "sh", "-c", script)
	cmd.Stdin = strings.NewReader(encoded)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("exec in container: %w — %s", err, string(out))
	}
	return nil
}

// dockerExecSh runs a shell command inside a running container.
func dockerExecSh(container, script string) error {
	cmd := exec.Command("docker", "exec", container, "sh", "-c", script)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("exec in container: %w — %s", err, string(out))
	}
	return nil
}

// run executes a command, discarding output on success.
func run(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %s: %w — %s", name, strings.Join(args, " "), err, bytes.TrimSpace(out))
	}
	return nil
}
