// Package langfuse reads agent sessions from a self-hosted Langfuse v4
// deployment, for the Agent Work panel.
//
// Why this package exists alongside app/opensearch
// ------------------------------------------------
// The two stores are good at different things, and the measurements are the
// reason this is a split rather than a replacement:
//
//   - CONTENT lives only here. Langfuse observations carry the real `input`
//     and `output` — the assistant's actual message text. OpenSearch has none:
//     content is stripped from spans by design and the log attributes read
//     `<REDACTED>`. "Click to see what this session said" is only answerable
//     from Langfuse.
//   - FRESHNESS is the opposite. Measured together on one live session:
//     OpenSearch 33s behind, Langfuse 725s. Langfuse's Claude Code capture is
//     a Stop hook, so it fires once per COMPLETED turn — a session mid-turn is
//     invisible here until the turn ends. Any liveness indicator built on
//     Langfuse recency would call a working session dead.
//
// So liveness stays on OpenSearch (see app/opensearch/liveness.go) and is
// merged over these rows by the caller. This package deliberately does not
// attempt it.
//
// v4 `events_only` mode removes routes
// ------------------------------------
// This deployment answers 404 for `/api/public/sessions` and
// `/api/public/traces` with "not available on deployments running in Langfuse
// v4 events_only mode". There is therefore NO session-list endpoint, and the
// list is assembled from two calls that do work — see sessions.go.
package langfuse

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Client reads one Langfuse project. Keys are project-scoped in Langfuse —
// there is no key that reads across projects — so a Client is implicitly bound
// to the profile whose credentials it was built with.
type Client struct {
	baseURL string
	auth    string
	http    *http.Client
}

// NewClient returns nil when either credential is missing, so a caller can
// treat "Langfuse not configured" as a normal state rather than an error. The
// keys are project-scoped; see the type comment.
func NewClient(baseURL, publicKey, secretKey string) *Client {
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if baseURL == "" || publicKey == "" || secretKey == "" {
		return nil
	}
	return &Client{
		baseURL: baseURL,
		auth: "Basic " + base64.StdEncoding.EncodeToString(
			[]byte(publicKey+":"+secretKey)),
		// Matches the opensearch client's ceiling. A panel that polls every
		// 15s must not have a request outlive its own poll interval.
		http: &http.Client{Timeout: 5 * time.Second},
	}
}

// get issues an authenticated GET and decodes the JSON body into out.
func (c *Client) get(ctx context.Context, path string, q url.Values, out any) error {
	u := c.baseURL + path
	if len(q) > 0 {
		u += "?" + q.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return fmt.Errorf("langfuse request: %w", err)
	}
	req.Header.Set("Authorization", c.auth)
	req.Header.Set("Accept", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("langfuse %s: %w", path, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		// Cap the echoed body: a 4xx from this API carries a JSON message, and
		// an unbounded read of an error page would land in a log line.
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		msg := strings.TrimSpace(string(b))
		// 404 on a route that exists in Langfuse Cloud is the events_only
		// signature; say so, because the fix is a different route, not a retry.
		if resp.StatusCode == http.StatusNotFound && strings.Contains(msg, "events_only") {
			return fmt.Errorf("langfuse %s: not available in v4 events_only mode", path)
		}
		return fmt.Errorf("langfuse %s: http %d: %s", path, resp.StatusCode, msg)
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("langfuse %s: decode: %w", path, err)
	}
	return nil
}
