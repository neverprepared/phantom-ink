package brainbox

import (
	"fmt"
	"net/url"
)

// Brain memory-binding surface (Phase 2, brain facade).
//
// These wrap the router's /api/brain/profiles* facade, which proxies
// phantom-brain's operator /admin/profiles and threads CL_BRAIN_* into the
// profile's credentials server-side. The bearer token is never returned to
// the app — provisioning is fire-and-observe from here.

// BrainProfileInfo is a profile's memory-binding status, from
// GET /api/brain/profiles/{profile}. An unprovisioned profile has
// Provisioned=false and empty storage fields.
type BrainProfileInfo struct {
	Profile     string `json:"profile"`
	Vault       string `json:"vault"`
	Provisioned bool   `json:"provisioned"`
	Bucket      string `json:"bucket"`
	IndexPrefix string `json:"index_prefix"`
}

// BrainProfileInitResult is the outcome of POST /api/brain/profiles: the
// binding is provisioned (Postgres SoR + MinIO archives) and CL_BRAIN_*
// threaded into the profile's credentials. TokenCreated is false on a re-run
// (the existing token is read back). Idempotent.
type BrainProfileInitResult struct {
	Profile      string `json:"profile"`
	Vault        string `json:"vault"`
	Provisioned  bool   `json:"provisioned"`
	Live         bool   `json:"live"`
	TokenCreated bool   `json:"token_created"`
	Bucket       string `json:"bucket"`
	IndexPrefix  string `json:"index_prefix"`
	Threaded     bool   `json:"threaded"`
}

// GetBrainProfile returns a profile's memory-binding status via the router
// brain facade.
func (c *Client) GetBrainProfile(profile string) (BrainProfileInfo, error) {
	var info BrainProfileInfo
	path := fmt.Sprintf("/api/brain/profiles/%s", url.PathEscape(profile))
	if err := c.get(path, &info); err != nil {
		return BrainProfileInfo{}, err
	}
	return info, nil
}

// InitBrainProfile provisions (or heals) a profile's memory binding and
// threads its CL_BRAIN_* credentials, via the router brain facade. Idempotent.
func (c *Client) InitBrainProfile(profile string) (BrainProfileInitResult, error) {
	var res BrainProfileInitResult
	if err := c.post("/api/brain/profiles", map[string]interface{}{"profile": profile}, &res); err != nil {
		return BrainProfileInitResult{}, err
	}
	return res, nil
}
