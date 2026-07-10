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
	"bufio"
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
)

// validEnvKey matches only legal shell identifier names: [A-Za-z_][A-Za-z0-9_]*
var validEnvKey = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

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
	// OTLPHost is the hostname (or host:port base) of the Data Prepper /
	// OpenTelemetry ingest endpoint, e.g. "storage.example.com".
	// When set, the container's settings.json gets OTLP exporter env vars
	// pointing at the three signal ports (21890/21891/21892).
	// CLAUDE_CODE_ENABLE_TELEMETRY is intentionally excluded — that remains
	// user opt-in, set in the profile .env or shell environment.
	OTLPHost string
	// NoCache forces a fresh base image before building: any local copy of
	// BaseImage is removed so the subsequent `docker pull` re-fetches the
	// current registry digest. Use after pushing a rebuilt base so the profile
	// build doesn't reuse a stale locally-cached base layer.
	NoCache bool
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

	// 1. Pull base image. With NoCache, drop any local copy first so the pull
	// re-fetches the current registry digest instead of reusing a stale layer.
	if opts.NoCache {
		opts.progress("Removing cached base image…")
		_ = run("docker", "rmi", "-f", opts.BaseImage)
	}
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

	// 5b. Inject Codex credentials (encrypted as .codex.enc).
	opts.progress("Injecting Codex credentials…")
	if err := injectCodexCredentials(containerName, opts, envKey); err != nil {
		return BuildResult{}, fmt.Errorf("inject Codex credentials: %w", err)
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
			// Require a valid KEY=value assignment; skip shell commands,
			// function defs, unset calls, etc. that would fail when sourced.
			idx := strings.IndexByte(line, '=')
			if idx <= 0 {
				continue
			}
			varName := strings.TrimSpace(line[:idx])
			if !validEnvKey.MatchString(varName) {
				continue
			}
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

// macOSOnlyPlugins are Claude Code IDE integrations that only work on macOS and
// hang at startup inside a Linux container.
var macOSOnlyPlugins = map[string]bool{
	"gopls-lsp@claude-plugins-official": true,
	"swift-lsp@claude-plugins-official": true,
	"pylsp-lsp@claude-plugins-official": true,
	"rust-lsp@claude-plugins-official":  true,
}

// translateClaudeJSON produces the container's .claude.json. It strips the
// host's MCP server definitions entirely and bakes ONLY the phantom-gateway
// entry, so every container reaches MCP through the gateway rather than
// spawning direct servers. The profile's own servers are intentionally
// dropped: their credentials live daemon-side and are injected by the gateway
// per profile, so baking direct in-container servers would run them without
// creds and bypass the gateway's residency scoping. Remaining Mac paths
// elsewhere in the document (projects, settings) are still translated.
func translateClaudeJSON(raw []byte, workspaceHome string) []byte {
	var doc map[string]interface{}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return raw
	}

	pathReplacer := strings.NewReplacer(workspaceHome, "/home/developer")

	// The MCP gateway declaration is static; only the secret is dynamic. The
	// daemon delivers PHANTOM_GATEWAY_URL/TOKEN via container env at session
	// create and Claude Code expands the references at connect time. Simple
	// ${VAR} refs only — Claude Code corrupts nested ${A:-${B}} fallbacks.
	doc["mcpServers"] = map[string]interface{}{
		"phantom-gateway": map[string]interface{}{
			"type": "http",
			"url":  "${PHANTOM_GATEWAY_URL}",
			"headers": map[string]interface{}{
				"Authorization": "Bearer ${PHANTOM_GATEWAY_TOKEN}",
			},
		},
	}

	result, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return raw
	}
	return []byte(pathReplacer.Replace(string(result)))
}

// translateSettingsJSON applies container-safe translations to settings.json:
//   - Removes macOS-only LSP plugins from enabledPlugins
//   - Clears statusLine if it references a Mac-specific path
//   - Replaces workspaceHome paths
//   - Forces container-required settings (bypassPermissions, dark theme, no Mac LSPs)
//   - When otlpHost is non-empty, injects OTLP exporter env vars into the env block.
//     CLAUDE_CODE_ENABLE_TELEMETRY is intentionally omitted — telemetry remains
//     user opt-in, controlled via the profile .env or shell environment.
func translateSettingsJSON(raw []byte, workspaceHome, otlpHost string) []byte {
	var doc map[string]interface{}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return raw
	}

	pathReplacer := strings.NewReplacer(workspaceHome, "/home/developer")

	// Strip macOS-only LSP plugins.
	if plugins, ok := doc["enabledPlugins"].(map[string]interface{}); ok {
		for k := range plugins {
			if macOSOnlyPlugins[k] {
				delete(plugins, k)
			}
		}
	}

	// Clear statusLine if it references a Mac-specific path.
	if sl, ok := doc["statusLine"].(map[string]interface{}); ok {
		if cmd, _ := sl["command"].(string); isMacAbsolutePath(cmd) || strings.Contains(cmd, workspaceHome) {
			delete(doc, "statusLine")
		}
	}

	// Strip user-level hooks: they reference host paths and host state
	// (e.g. ${CLAUDE_CONFIG_DIR}/hooks/error-correction-stop.sh — the
	// scripts aren't baked into the image, and CLAUDE_CONFIG_DIR is unset
	// in containers so the command degrades to /hooks/… "not found" noise
	// at session end). Container hooks come from the reflex plugin.
	delete(doc, "hooks")

	// Force container-required overrides.
	doc["bypassPermissions"] = true
	doc["skipDangerousModePermissionPrompt"] = true
	doc["bypassPermissionsModeAccepted"] = true
	doc["theme"] = "dark"

	// Inject OTLP exporter configuration when a host is configured.
	// We set the exporter types and per-signal endpoints but deliberately
	// omit CLAUDE_CODE_ENABLE_TELEMETRY so telemetry stays user opt-in.
	if otlpHost != "" {
		env, _ := doc["env"].(map[string]interface{})
		if env == nil {
			env = make(map[string]interface{})
		}
		env["OTEL_METRICS_EXPORTER"] = "otlp"
		env["OTEL_LOGS_EXPORTER"] = "otlp"
		env["OTEL_TRACES_EXPORTER"] = "otlp"
		env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] = "1"
		env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"
		env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = "http://" + otlpHost + ":21890"
		env["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] = "http://" + otlpHost + ":21891"
		env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] = "http://" + otlpHost + ":21892"
		doc["env"] = env
	}

	out, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return raw
	}
	return []byte(pathReplacer.Replace(string(out)))
}

// isMacAbsolutePath returns true for absolute paths that are macOS-specific
// and won't exist inside a Linux container.
func isMacAbsolutePath(cmd string) bool {
	return strings.HasPrefix(cmd, "/Users/") ||
		strings.HasPrefix(cmd, "/opt/homebrew/") ||
		strings.HasPrefix(cmd, "/Library/") ||
		strings.HasPrefix(cmd, "/Applications/")
}

// cloneMap makes a shallow copy of a map[string]interface{}.
func cloneMap(m map[string]interface{}) map[string]interface{} {
	c := make(map[string]interface{}, len(m))
	for k, v := range m {
		c[k] = v
	}
	return c
}

// injectClaudeCredentials packs Claude auth + config into a JSON bundle, encrypts
// with the provided key, and writes the ciphertext to /home/developer/.claude.enc.
// ttyd-wrapper.sh decrypts at container startup using the same PROFILE_ENV_KEY.
//
// Before packing, .claude.json and settings.json are sanitised for Linux containers:
// the host's MCP servers are dropped in favour of the baked phantom-gateway entry,
// macOS-only plugins are stripped, and Mac absolute paths are replaced.
// CLAUDE.md is included when present.
func injectClaudeCredentials(container string, opts BuildOptions, key string) error {
	claudeConfigDir := filepath.Join(opts.WorkspaceHome, ".claude")

	bundle := make(map[string]string)

	// .credentials.json — Claude Code OAuth tokens. On macOS the LIVE token is
	// in the login Keychain, and Claude Code namespaces it PER CONFIG DIR as
	// "Claude Code-credentials-<sha256(configDir)[:8]>" — one item per profile,
	// each with its own live access + refresh token that Claude Code rotates in
	// place. We must read the item for THIS profile's config dir; the legacy
	// unhashed "Claude Code-credentials" item is a stale husk (empty/rotated
	// refresh token) and any on-disk .credentials.json is an even older leftover
	// — baking either forces a container re-login. Fall through in order:
	// hashed Keychain item → legacy Keychain item → on-disk file.
	sum := sha256.Sum256([]byte(claudeConfigDir))
	credServices := []string{
		"Claude Code-credentials-" + hex.EncodeToString(sum[:])[:8],
		"Claude Code-credentials",
	}
	for _, svc := range credServices {
		if out, kerr := exec.Command(
			"security", "find-generic-password", "-s", svc, "-w",
		).Output(); kerr == nil {
			if tok := strings.TrimSpace(string(out)); tok != "" {
				bundle["credentials_json"] = tok
				break
			}
		}
	}
	if _, ok := bundle["credentials_json"]; !ok {
		if data, err := os.ReadFile(filepath.Join(claudeConfigDir, ".credentials.json")); err == nil {
			bundle["credentials_json"] = string(data)
		}
	}
	if _, ok := bundle["credentials_json"]; !ok {
		opts.progress("warning: no Claude credentials found (Keychain lookup failed and " +
			"no .credentials.json file) — the built image will be UNAUTHENTICATED")
	}

	// .claude.json — strip the host's MCP servers and bake only phantom-gateway.
	// A missing host file still produces a config: the gateway entry is the
	// container's only MCP wiring.
	claudeJSONData := []byte("{}")
	if data, err := os.ReadFile(filepath.Join(claudeConfigDir, ".claude.json")); err == nil {
		claudeJSONData = data
	}
	bundle["claude_json"] = string(translateClaudeJSON(claudeJSONData, opts.WorkspaceHome))

	// settings.json — strip Mac plugins/statusLine, force container settings,
	// and inject OTLP endpoints when configured.
	if data, err := os.ReadFile(filepath.Join(claudeConfigDir, "settings.json")); err == nil {
		bundle["settings_json"] = string(translateSettingsJSON(data, opts.WorkspaceHome, opts.OTLPHost))
	}

	// CLAUDE.md — global instructions, translate any Mac paths.
	if data, err := os.ReadFile(filepath.Join(claudeConfigDir, "CLAUDE.md")); err == nil {
		translated := strings.ReplaceAll(string(data), opts.WorkspaceHome, "/home/developer")
		bundle["claude_md"] = translated
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

// injectCodexCredentials bakes the profile's Codex ChatGPT OAuth credential
// (~/.codex/auth.json) into the image, encrypted as /home/developer/.codex.enc.
// ttyd-wrapper.sh decrypts it to ~/.codex/auth.json at container startup using
// the same PROFILE_ENV_KEY.
//
// Codex, like Claude Code, authenticates via the operator's existing OAuth
// tokens (auth.json "auth_mode": "chatgpt" + rotating tokens), not a per-service
// API key — consistent with the "No API Keys for Agents" convention. Only
// auth.json is baked; the host's config.toml is host-specific state (project
// trust levels with Mac paths) that the container does not need — sessions run
// codex with --sandbox off --ask-for-approval never and select the model via
// --model $CODEX_MODEL.
//
// Skips silently when the profile has no Codex auth: that profile's Codex
// sessions start unauthenticated (codex prompts for login), which is the same
// graceful degradation as a missing Claude credential.
func injectCodexCredentials(container string, opts BuildOptions, key string) error {
	authPath := filepath.Join(opts.WorkspaceHome, ".codex", "auth.json")
	data, err := os.ReadFile(authPath)
	if err != nil {
		opts.progress("warning: no Codex credentials found (" + authPath +
			") — Codex sessions in the built image will be UNAUTHENTICATED")
		return nil
	}

	ciphertext, err := encryptEnv(string(data), key)
	if err != nil {
		return fmt.Errorf("encrypt codex auth: %w", err)
	}
	if err := writeFileToContainer(container, "/home/developer/.codex.enc", ciphertext, "600"); err != nil {
		return fmt.Errorf("write .codex.enc: %w", err)
	}
	return nil
}

// registryLogin runs docker login for the configured registry.
func registryLogin(opts BuildOptions) error {
	return dockerLogin(opts.RegistryURL, opts.RegistryUsername, opts.RegistryPassword)
}

// dockerLogin authenticates to a registry. A blank username is treated as an
// anonymous registry and skipped.
func dockerLogin(url, username, password string) error {
	if username == "" {
		return nil // anonymous registry, no login needed
	}
	cmd := exec.Command("docker", "login",
		"--username", username,
		"--password-stdin",
		url,
	)
	cmd.Stdin = strings.NewReader(password)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("docker login: %w — %s", err, string(out))
	}
	return nil
}

// BaseBuildOptions controls a rebuild of the brainbox *base* image.
type BaseBuildOptions struct {
	// RepoRoot is the absolute path to the phantom-ink checkout containing
	// brainbox/scripts/build.sh and docker/brainbox/Dockerfile.
	RepoRoot string
	// RegistryURL is the private registry to tag and push the base image to,
	// as "<RegistryURL>/brainbox:latest". Required — the profile build pulls
	// the base from here.
	RegistryURL string
	// RegistryUsername and RegistryPassword authenticate the push.
	RegistryUsername string
	RegistryPassword string
	// NoCache passes --no-cache to `docker build` (NO_CACHE=1 to build.sh).
	NoCache bool
	// Progress receives each line of build output.
	Progress func(string)
}

func (o *BaseBuildOptions) progress(msg string) {
	if o.Progress != nil {
		o.Progress(msg)
	}
}

// BuildBase rebuilds the brainbox base image via brainbox/scripts/build.sh and
// pushes it to the registry as "<RegistryURL>/brainbox:latest". It streams the
// script's output line-by-line to Progress so the UI can show live logs.
//
// This is the upstream half of the chain: the script (e.g. ttyd-wrapper.sh) and
// Dockerfile only reach a running container after the base is rebuilt here and
// then a profile image is rebuilt on top of it. Output streams to Progress.
func BuildBase(opts BaseBuildOptions) error {
	if opts.RegistryURL == "" {
		return fmt.Errorf("registry URL is required")
	}
	script := filepath.Join(opts.RepoRoot, "brainbox", "scripts", "build.sh")
	if _, err := os.Stat(script); err != nil {
		return fmt.Errorf("build script not found at %s: %w", script, err)
	}

	// Authenticate before the script pushes.
	opts.progress("Logging in to registry…")
	if err := dockerLogin(opts.RegistryURL, opts.RegistryUsername, opts.RegistryPassword); err != nil {
		return err
	}

	cmd := exec.Command("bash", script)
	cmd.Dir = opts.RepoRoot
	cmd.Env = append(os.Environ(), "REGISTRY_URL="+opts.RegistryURL)
	if opts.NoCache {
		cmd.Env = append(cmd.Env, "NO_CACHE=1")
	}

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("stdout pipe: %w", err)
	}
	// Merge stderr into the same pipe so build/push errors stream too.
	// StdoutPipe set cmd.Stdout to the pipe's write end; reuse it for Stderr.
	cmd.Stderr = cmd.Stdout

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start build.sh: %w", err)
	}
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		opts.progress(scanner.Text())
	}
	if err := cmd.Wait(); err != nil {
		return fmt.Errorf("build.sh failed: %w", err)
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
