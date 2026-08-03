package brainbox

import (
	"fmt"
	"net/url"
)

// Brain memory-binding surface (Phase 2, brain facade).
//
// These wrap the router's /api/brain/profiles* facade, which proxies
// phantom-brain's operator /admin/profiles and threads CL_BRAIN_* into the
// profile's credentials server-side. Provisioning is fire-and-observe; the
// default token stays server-side. The one exception is GetBrainProfileTokens,
// an operator-gated read that returns the per-vault tokens on demand.

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

// BrainVaultToken is one vault's bearer token for a profile. The token IS the
// (profile, vault) scope — an MCP client sends it as Authorization: Bearer.
type BrainVaultToken struct {
	Vault     string `json:"vault"`
	Token     string `json:"token"`
	IsDefault bool   `json:"is_default"`
}

// BrainProfileTokens is a profile's per-vault bearer tokens, from
// GET /api/brain/profiles/{profile}/tokens. SECRET — the UI masks them and
// reveals on demand.
type BrainProfileTokens struct {
	Profile      string            `json:"profile"`
	SessionURL   string            `json:"session_url"`
	DefaultVault string            `json:"default_vault"`
	Tokens       []BrainVaultToken `json:"tokens"`
}

// GetBrainProfileTokens returns a profile's per-vault brain bearer tokens via
// the router facade (operator-gated). Secrets — callers must treat them as such.
func (c *Client) GetBrainProfileTokens(profile string) (BrainProfileTokens, error) {
	var res BrainProfileTokens
	path := fmt.Sprintf("/api/brain/profiles/%s/tokens", url.PathEscape(profile))
	if err := c.get(path, &res); err != nil {
		return BrainProfileTokens{}, err
	}
	return res, nil
}

// Skills CRUD over the router's brain-authoritative skills facade
// (/api/brain/profiles/{profile}/skills). The skills vault is the source of
// truth; the router mints the vault token server-side. Identity is the skill's
// frontmatter name. See phantom-router skills_crud for the semantics.

// BrainSkill is one skill's list-view metadata (body omitted).
type BrainSkill struct {
	Name      string   `json:"name"`
	Title     string   `json:"title"`
	SHA       string   `json:"sha"`
	UpdatedAt string   `json:"updated_at"`
	Tags      []string `json:"tags"`
}

// BrainSkillDetail is one skill with its full SKILL.md body.
type BrainSkillDetail struct {
	Name      string `json:"name"`
	Title     string `json:"title"`
	Body      string `json:"body"`
	SHA       string `json:"sha"`
	UpdatedAt string `json:"updated_at"`
}

// BrainSkillWriteResult is the outcome of a create/update/delete. Fields are
// populated per operation: create sets Created+SHA, update sets SHA+Replaced,
// delete sets Deleted+Title.
type BrainSkillWriteResult struct {
	Name     string `json:"name"`
	Title    string `json:"title"`
	SHA      string `json:"sha"`
	Created  bool   `json:"created"`
	Replaced int    `json:"replaced"`
	Deleted  int    `json:"deleted"`
}

type brainSkillsList struct {
	Profile string       `json:"profile"`
	Skills  []BrainSkill `json:"skills"`
}

// ListBrainSkills returns a profile's skills (metadata only, newest-wins).
func (c *Client) ListBrainSkills(profile string) ([]BrainSkill, error) {
	var res brainSkillsList
	path := fmt.Sprintf("/api/brain/profiles/%s/skills", url.PathEscape(profile))
	if err := c.get(path, &res); err != nil {
		return nil, err
	}
	return res.Skills, nil
}

// GetBrainSkill returns one skill's full SKILL.md body by name.
func (c *Client) GetBrainSkill(profile, name string) (BrainSkillDetail, error) {
	var d BrainSkillDetail
	path := fmt.Sprintf("/api/brain/profiles/%s/skills/%s", url.PathEscape(profile), url.PathEscape(name))
	if err := c.get(path, &d); err != nil {
		return BrainSkillDetail{}, err
	}
	return d, nil
}

// CreateBrainSkill creates a skill from a full SKILL.md body (name from its
// frontmatter). Errors 409 if the name already exists.
func (c *Client) CreateBrainSkill(profile, body string) (BrainSkillWriteResult, error) {
	var res BrainSkillWriteResult
	path := fmt.Sprintf("/api/brain/profiles/%s/skills", url.PathEscape(profile))
	if err := c.post(path, map[string]string{"body": body}, &res); err != nil {
		return BrainSkillWriteResult{}, err
	}
	return res, nil
}

// UpdateBrainSkill replaces a skill's body by name (upsert; prior versions are
// forgotten). The body's frontmatter name must match name.
func (c *Client) UpdateBrainSkill(profile, name, body string) (BrainSkillWriteResult, error) {
	var res BrainSkillWriteResult
	path := fmt.Sprintf("/api/brain/profiles/%s/skills/%s", url.PathEscape(profile), url.PathEscape(name))
	if err := c.put(path, map[string]string{"body": body}, &res); err != nil {
		return BrainSkillWriteResult{}, err
	}
	return res, nil
}

// DeleteBrainSkill hard-deletes every version of a skill by name.
func (c *Client) DeleteBrainSkill(profile, name string) (BrainSkillWriteResult, error) {
	var res BrainSkillWriteResult
	path := fmt.Sprintf("/api/brain/profiles/%s/skills/%s", url.PathEscape(profile), url.PathEscape(name))
	if err := c.delete(path, &res); err != nil {
		return BrainSkillWriteResult{}, err
	}
	return res, nil
}
