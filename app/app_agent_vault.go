package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"
)

// Vault browser for the p2p-synced verbatim phantom-brain vaults (agents, skills).
// Records live on the mesh daemon (:9998) and are PER-PROFILE + PER-VAULT: the
// bearer token binds to one (profile, vault). The app stores a token per
// (vault, profile) under the key "<vault>_token_<profile>", and browses whichever
// profile is ACTIVE — so switching the app's profile switches the vault, honoring
// the daemon's isolation. Copy/Move re-learn a record into a DESTINATION profile's
// vault (move also forgets the source). Auth is Authorization: Bearer.

// VaultRecord is one authored record (agent definition, skill, …) from a vault.
type VaultRecord struct {
	SHA       string    `json:"sha"`
	Title     string    `json:"title"`
	Kind      string    `json:"kind"`
	Body      string    `json:"body"`
	Topic     string    `json:"topic,omitempty"`
	Tags      []string  `json:"tags,omitempty"`
	UpdatedAt time.Time `json:"updated_at"`
}

type vaultListResponse struct {
	Records []VaultRecord `json:"records"`
}

// vaultKind maps a vault to the record kind it stores (the ?kind= list filter).
func vaultKind(vault string) string {
	switch vault {
	case "agents":
		return "agent"
	case "skills":
		return "skill"
	case "todo":
		return "todo"
	default:
		return ""
	}
}

// vaultTokenKey is the settings key for a (vault, profile) bearer token, e.g.
// "skills_token_personal". Prefix "<vault>_token_" enumerates all profiles.
func vaultTokenKey(vault, profile string) string { return vault + "_token_" + profile }

// tokenFor returns the bearer token for a (vault, profile), or "".
func (a *App) tokenFor(vault, profile string) string {
	if a.db == nil || profile == "" {
		return ""
	}
	return a.db.GetSetting(vaultTokenKey(vault, profile), "")
}

// GetVaultToken returns the ACTIVE profile's token for a vault ("" if unset).
func (a *App) GetVaultToken(vault string) string {
	return a.tokenFor(vault, a.activeProfileName())
}

// SetVaultToken persists the ACTIVE profile's token for a vault (trimmed).
func (a *App) SetVaultToken(vault, token string) error {
	p := a.activeProfileName()
	if p == "" {
		return fmt.Errorf("no active profile selected")
	}
	if a.db == nil {
		return fmt.Errorf("no database")
	}
	return a.db.SetSetting(vaultTokenKey(vault, p), strings.TrimSpace(token))
}

// GetVaultProfiles returns the profiles that have a token configured for this
// vault — the valid copy/move targets — sorted.
func (a *App) GetVaultProfiles(vault string) []string {
	out := []string{}
	if a.db == nil {
		return out
	}
	prefix := vault + "_token_"
	m, err := a.db.GetSettingsWithPrefix(prefix)
	if err != nil {
		return out
	}
	for k, v := range m {
		if strings.TrimSpace(v) == "" {
			continue
		}
		out = append(out, strings.TrimPrefix(k, prefix))
	}
	sort.Strings(out)
	return out
}

// GetVaultRecords lists the ACTIVE profile's vault records, head-only.
func (a *App) GetVaultRecords(vault string) ([]VaultRecord, error) {
	profile := a.activeProfileName()
	if profile == "" {
		return nil, fmt.Errorf("no active profile selected")
	}
	token := a.tokenFor(vault, profile)
	if token == "" {
		return nil, fmt.Errorf("no %s-vault token for profile %q", vault, profile)
	}
	return a.listVaultRecords(vault, token)
}

func (a *App) listVaultRecords(vault, token string) ([]VaultRecord, error) {
	url := a.meshURL()
	endpoint := url + "/api/brain/records?head=true&limit=500"
	if k := vaultKind(vault); k != "" {
		endpoint += "&kind=" + k
	}
	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, fmt.Errorf("build %s request: %w", vault, err)
	}
	req.Header.Set("Authorization", "Bearer "+token)

	resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
	if err != nil {
		return nil, fmt.Errorf("mesh daemon unreachable at %s: %w", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusUnauthorized {
		return nil, fmt.Errorf("%s-vault token rejected (401) — check the token in Settings", vault)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("mesh daemon at %s returned HTTP %d", url, resp.StatusCode)
	}
	var out vaultListResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("decode %s records from %s: %w", vault, url, err)
	}
	return out.Records, nil
}

// ForgetVaultRecord deletes one record by SHA from the ACTIVE profile's vault.
func (a *App) ForgetVaultRecord(vault, sha string) error {
	profile := a.activeProfileName()
	token := a.tokenFor(vault, profile)
	if token == "" {
		return fmt.Errorf("no %s-vault token for profile %q", vault, profile)
	}
	return a.forgetWithToken(sha, token)
}

// CopyVaultRecord copies a record from the ACTIVE profile's vault into
// destProfile's same vault (re-learned; verbatim ⇒ identical SHA). Source kept.
func (a *App) CopyVaultRecord(vault, sha, destProfile string) error {
	return a.copyOrMove(vault, sha, destProfile, false)
}

// MoveVaultRecord copies then forgets the source — a profile-to-profile move.
func (a *App) MoveVaultRecord(vault, sha, destProfile string) error {
	return a.copyOrMove(vault, sha, destProfile, true)
}

func (a *App) copyOrMove(vault, sha, destProfile string, move bool) error {
	src := a.activeProfileName()
	if src == "" {
		return fmt.Errorf("no active profile selected")
	}
	if destProfile == "" || destProfile == src {
		return fmt.Errorf("choose a destination profile different from %q", src)
	}
	srcTok := a.tokenFor(vault, src)
	if srcTok == "" {
		return fmt.Errorf("no %s-vault token for source profile %q", vault, src)
	}
	destTok := a.tokenFor(vault, destProfile)
	if destTok == "" {
		return fmt.Errorf("no %s-vault token for destination profile %q", vault, destProfile)
	}

	recs, err := a.listVaultRecords(vault, srcTok)
	if err != nil {
		return err
	}
	var rec *VaultRecord
	for i := range recs {
		if recs[i].SHA == sha {
			rec = &recs[i]
			break
		}
	}
	if rec == nil {
		return fmt.Errorf("record %s not found in %s's %s vault", sha, src, vault)
	}

	if err := a.learnWithToken(destTok, rec.SHA, rec.Title, rec.Body, rec.Tags); err != nil {
		return fmt.Errorf("write to %s failed: %w", destProfile, err)
	}
	if move {
		if err := a.forgetWithToken(sha, srcTok); err != nil {
			return fmt.Errorf("copied to %s, but removing from %s failed: %w", destProfile, src, err)
		}
	}
	return nil
}

// learnWithToken re-learns a record into the vault the token binds to. sha is
// the source record's content hash — the daemon requires a well-formed SHA and
// re-derives the canonical one itself (client SHA is advisory), but for a
// verbatim record it matches, so the copy dedups to the same identity.
func (a *App) learnWithToken(token, sha, title, body string, tags []string) error {
	payload := map[string]any{"sha": sha, "title": title, "body": body}
	if len(tags) > 0 {
		payload["tags"] = tags
	}
	buf, _ := json.Marshal(payload)
	req, err := http.NewRequest(http.MethodPost, a.meshURL()+"/api/brain/learn", bytes.NewReader(buf))
	if err != nil {
		return fmt.Errorf("build learn request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := (&http.Client{Timeout: 15 * time.Second}).Do(req)
	if err != nil {
		return fmt.Errorf("mesh daemon unreachable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("learn returned HTTP %d", resp.StatusCode)
	}
	return nil
}

func (a *App) forgetWithToken(sha, token string) error {
	sha = strings.TrimSpace(sha)
	if sha == "" {
		return fmt.Errorf("empty sha")
	}
	buf, _ := json.Marshal(map[string]string{"sha": sha})
	req, err := http.NewRequest(http.MethodPost, a.meshURL()+"/api/brain/forget", bytes.NewReader(buf))
	if err != nil {
		return fmt.Errorf("build forget request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
	if err != nil {
		return fmt.Errorf("mesh daemon unreachable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusUnauthorized {
		return fmt.Errorf("token rejected (401)")
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("forget returned HTTP %d", resp.StatusCode)
	}
	return nil
}
