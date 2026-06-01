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
	"encoding/base64"
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
	// OPVault is the 1Password vault name for SSH key lookup (optional).
	OPVault string
	// Progress receives status messages during the build.
	Progress func(string)
}

// BuildResult holds the outcome of a successful build.
type BuildResult struct {
	// Tag is the full registry-qualified image tag that was pushed.
	Tag string
	// Digest is the image digest returned by docker push.
	Digest string
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

	// 5. Inject Claude credentials.
	opts.progress("Injecting Claude credentials…")
	if err := injectClaudeCredentials(containerName, opts); err != nil {
		return BuildResult{}, fmt.Errorf("inject Claude credentials: %w", err)
	}

	// 6. Stop container before commit (clean snapshot).
	opts.progress("Stopping container…")
	if err := run("docker", "stop", containerName); err != nil {
		return BuildResult{}, fmt.Errorf("stop container: %w", err)
	}

	// 7. Commit to registry tag.
	tag := opts.imageTag()
	opts.progress(fmt.Sprintf("Committing image as %s…", tag))
	if err := run("docker", "commit", containerName, tag); err != nil {
		return BuildResult{}, fmt.Errorf("commit image: %w", err)
	}

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
	return BuildResult{Tag: tag, Digest: digest}, nil
}

// injectSSHKeys copies ~/.ssh from workspaceHome into the container.
// Falls back to `op read` for any key that does not exist as a file.
func injectSSHKeys(container string, opts BuildOptions) error {
	sshDir := filepath.Join(opts.WorkspaceHome, ".ssh")

	// Ensure target dir exists in container.
	if err := dockerExecSh(container, "mkdir -p /home/developer/.ssh && chmod 700 /home/developer/.ssh"); err != nil {
		return err
	}

	if entries, err := os.ReadDir(sshDir); err == nil {
		for _, e := range entries {
			if e.IsDir() {
				continue
			}
			data, err := os.ReadFile(filepath.Join(sshDir, e.Name()))
			if err != nil {
				continue
			}
			if err := writeFileToContainer(container, "/home/developer/.ssh/"+e.Name(), data, "600"); err != nil {
				return fmt.Errorf("write SSH key %s: %w", e.Name(), err)
			}
		}
		return nil
	}

	// Fallback: try op read for canonical key names.
	if opts.OPVault == "" {
		return nil // no vault configured, skip silently
	}
	for _, keyName := range []string{"id_ed25519", "id_ed25519.pub", "id_rsa", "id_rsa.pub"} {
		ref := fmt.Sprintf("op://%s/ssh-key/%s/%s", opts.OPVault, opts.Profile, keyName)
		out, err := exec.Command("op", "read", ref).Output()
		if err != nil || len(out) == 0 {
			continue
		}
		mode := "644"
		if !strings.HasSuffix(keyName, ".pub") {
			mode = "600"
		}
		if err := writeFileToContainer(container, "/home/developer/.ssh/"+keyName, out, mode); err != nil {
			return fmt.Errorf("write op SSH key %s: %w", keyName, err)
		}
	}
	return nil
}

// injectClaudeCredentials writes Claude auth files from the local workspaceHome/.claude.
func injectClaudeCredentials(container string, opts BuildOptions) error {
	claudeConfigDir := filepath.Join(opts.WorkspaceHome, ".claude")
	// Respect CLAUDE_CONFIG_DIR if explicitly set for this profile.
	if v := os.Getenv("CLAUDE_CONFIG_DIR"); v != "" {
		claudeConfigDir = v
	}

	if err := dockerExecSh(container, "mkdir -p /home/developer/.claude && chmod 700 /home/developer/.claude"); err != nil {
		return err
	}

	// .credentials.json (OAuth tokens) → ~/.claude/.credentials.json
	credsPath := filepath.Join(claudeConfigDir, ".credentials.json")
	if data, err := os.ReadFile(credsPath); err == nil {
		if err := writeFileToContainer(container, "/home/developer/.claude/.credentials.json", data, "600"); err != nil {
			return fmt.Errorf("write .credentials.json: %w", err)
		}
	}

	// .claude.json (oauthAccount, theme) → ~/.claude.json
	claudeJSONPath := filepath.Join(claudeConfigDir, ".claude.json")
	if data, err := os.ReadFile(claudeJSONPath); err == nil {
		if err := writeFileToContainer(container, "/home/developer/.claude.json", data, "600"); err != nil {
			return fmt.Errorf("write .claude.json: %w", err)
		}
	}

	// settings.json (theme, bypass flags) → ~/.claude/settings.json
	settingsPath := filepath.Join(claudeConfigDir, "settings.json")
	if data, err := os.ReadFile(settingsPath); err == nil {
		if err := writeFileToContainer(container, "/home/developer/.claude/settings.json", data, "644"); err != nil {
			return fmt.Errorf("write settings.json: %w", err)
		}
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

// writeFileToContainer writes data into a container path via base64-piped docker exec.
func writeFileToContainer(container, destPath string, data []byte, mode string) error {
	encoded := base64.StdEncoding.EncodeToString(data)
	script := fmt.Sprintf(
		"printf '%%s' '%s' | base64 -d > %s && chmod %s %s",
		encoded, destPath, mode, destPath,
	)
	return dockerExecSh(container, script)
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
