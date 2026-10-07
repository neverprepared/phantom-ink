package main

import (
	"strings"
	"testing"
)

// docker compose passes only the variables its own `environment:` block declares,
// so anything mindwalkComposeEnv injects but the compose file omits is dropped
// silently — the container simply comes up without it. That is how
// CL_BRAIN_VAULT and CL_BRAIN_VAULT_TOKENS went missing and left the in-app vault
// picker with no tokens to switch with.
func TestMindwalkComposeDeclaresEveryInjectedVar(t *testing.T) {
	data, err := embeddedCompose.ReadFile("compose/mindwalk/docker-compose.yml")
	if err != nil {
		t.Fatalf("read embedded compose: %v", err)
	}
	compose := string(data)

	env, ok := composeEnvBlock(compose)
	if !ok {
		t.Fatal("no environment: block found in the mindwalk compose file")
	}

	for _, key := range mindwalkInjectedVars {
		if !strings.Contains(env, key+":") {
			t.Errorf("compose environment: block does not declare %q — "+
				"docker compose will drop it and the container starts without it", key)
		}
	}
}

// composeEnvBlock returns the text of the service's environment: block.
func composeEnvBlock(compose string) (string, bool) {
	const marker = "environment:"
	i := strings.Index(compose, marker)
	if i < 0 {
		return "", false
	}
	rest := compose[i+len(marker):]
	// The block ends at the next key at the same (4-space) indentation.
	var b strings.Builder
	for _, line := range strings.Split(rest, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		if !strings.HasPrefix(line, "      ") { // less indented than block entries
			break
		}
		b.WriteString(trimmed + "\n")
	}
	return b.String(), true
}

// Guards the inverse direction: a var declared in compose but never injected is
// dead config, and the list must stay in step with what the Go side sets.
func TestMindwalkInjectedVarsCoverTokenAndVaultPlane(t *testing.T) {
	want := []string{"BRAIN_URL", "CL_BRAIN_API_TOKEN", "CL_BRAIN_VAULT", "CL_BRAIN_VAULT_TOKENS"}
	if len(mindwalkInjectedVars) != len(want) {
		t.Fatalf("mindwalkInjectedVars = %v, want %v", mindwalkInjectedVars, want)
	}
	have := map[string]bool{}
	for _, k := range mindwalkInjectedVars {
		have[k] = true
	}
	for _, k := range want {
		if !have[k] {
			t.Errorf("mindwalkInjectedVars missing %q", k)
		}
	}
}
