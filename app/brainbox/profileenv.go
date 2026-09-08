package brainbox

// Gateway profile env-store endpoint (phantom-router
// /api/gateway/profiles/{p}/env → credentials broker /api/creds/{ns}). Sibling
// of the bundle endpoints in bundle.go: this mirrors the profile's env vars
// (the store the router reads at session-create), the bundle mirrors files.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
)

// ProfileEnvPutResult mirrors PUT /api/gateway/profiles/{p}/env.
type ProfileEnvPutResult struct {
	Profile string `json:"profile"`
	Saved   bool   `json:"saved"`
	Count   int    `json:"count"`
}

// PutProfileEnv bulk-replaces a profile's env-typed credentials in the broker,
// via the hub's gateway-profiles proxy (which holds the broker operator key
// server-side — the app only presents its hub api_key). This is the same store
// the router forwards into a session at create time; oauth_json/cloud_file
// creds already in the namespace are preserved by the broker.
func (c *Client) PutProfileEnv(profile string, env map[string]string) (ProfileEnvPutResult, error) {
	var out ProfileEnvPutResult
	body, err := json.Marshal(map[string]any{"env": env})
	if err != nil {
		return out, fmt.Errorf("marshal env: %w", err)
	}
	path := fmt.Sprintf("/api/gateway/profiles/%s/env", url.PathEscape(profile))
	err = c.doRaw(http.MethodPut, path, "application/json", body, nil, &out)
	return out, err
}
