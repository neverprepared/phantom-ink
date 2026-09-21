package ado

// Azure CLI (`az login`) auth for the ADO client: instead of a stored PAT, mint
// a short-lived Entra bearer token from the operator's existing az session. This
// is the preferred path for operators who already `az login`; PAT stays as a
// fallback for environments without the Azure CLI.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"time"
)

// adoResourceID is the fixed Azure DevOps Entra application ID; a token scoped to
// it is what the ADO REST API accepts as a bearer credential.
const adoResourceID = "499b84ac-1321-427f-aa17-267ca6975798"

// azTokenCmd runs `az account get-access-token` for the ADO resource and returns
// raw stdout JSON. azConfigDir, when non-empty, selects the az session via
// AZURE_CONFIG_DIR (sessions are per-profile here). A package var so tests stub
// it without a real az / az login.
var azTokenCmd = func(ctx context.Context, azConfigDir string) ([]byte, error) {
	bin := azBinary()
	if bin == "" {
		return nil, errors.New("Azure CLI (az) not found — install it or set ADO_PAT")
	}
	cmd := exec.CommandContext(ctx, bin, "account", "get-access-token",
		"--resource", adoResourceID, "--output", "json")
	if azConfigDir != "" {
		cmd.Env = append(os.Environ(), "AZURE_CONFIG_DIR="+azConfigDir)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		msg := strings.TrimSpace(stderr.String())
		if msg == "" {
			msg = err.Error()
		}
		return nil, fmt.Errorf("az account get-access-token: %s", msg)
	}
	return stdout.Bytes(), nil
}

// azBinary finds the az CLI: PATH first, then the common install locations a
// GUI/launchd-launched app's minimal PATH omits (Homebrew arm64/intel, system).
func azBinary() string {
	if p, err := exec.LookPath("az"); err == nil {
		return p
	}
	for _, p := range []string{"/opt/homebrew/bin/az", "/usr/local/bin/az", "/usr/bin/az"} {
		if fi, err := os.Stat(p); err == nil && !fi.IsDir() {
			return p
		}
	}
	return ""
}

// parseAzToken reads the access token + expiry from az JSON. `expires_on` is
// epoch seconds on modern az; when absent we fall back to a short window so the
// token is re-minted promptly rather than trusted indefinitely.
func parseAzToken(raw []byte) (string, time.Time, error) {
	var t struct {
		AccessToken string      `json:"accessToken"`
		ExpiresOn   json.Number `json:"expires_on"`
	}
	if err := json.Unmarshal(raw, &t); err != nil {
		return "", time.Time{}, fmt.Errorf("parse az token: %w", err)
	}
	if t.AccessToken == "" {
		return "", time.Time{}, errors.New("az returned an empty access token")
	}
	exp := time.Now().Add(30 * time.Minute)
	if s := t.ExpiresOn.String(); s != "" {
		if sec, err := strconv.ParseInt(s, 10, 64); err == nil && sec > 0 {
			exp = time.Unix(sec, 0)
		}
	}
	return t.AccessToken, exp, nil
}

// AzAuthHeader mints a one-shot `Bearer <token>` header from an az login session
// (azConfigDir selects which; "" = default) — for callers that need a single
// fresh token (e.g. a git clone) rather than a long-lived client.
func AzAuthHeader(ctx context.Context, azConfigDir string) (string, error) {
	raw, err := azTokenCmd(ctx, azConfigDir)
	if err != nil {
		return "", err
	}
	tok, _, err := parseAzToken(raw)
	if err != nil {
		return "", err
	}
	return "Bearer " + tok, nil
}

// azTokenSource caches a bearer token on one client and re-mints ~5 min before
// expiry, so a page-load's read fan-out doesn't spawn az per request.
// azConfigDir selects the per-profile az session.
type azTokenSource struct {
	azConfigDir string

	mu    sync.Mutex
	token string
	exp   time.Time
}

func (s *azTokenSource) header(ctx context.Context) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.token != "" && time.Now().Before(s.exp.Add(-5*time.Minute)) {
		return "Bearer " + s.token, nil
	}
	raw, err := azTokenCmd(ctx, s.azConfigDir)
	if err != nil {
		return "", err
	}
	tok, exp, err := parseAzToken(raw)
	if err != nil {
		return "", err
	}
	s.token, s.exp = tok, exp
	return "Bearer " + s.token, nil
}
