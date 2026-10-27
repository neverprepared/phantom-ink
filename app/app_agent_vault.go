package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
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

// SetVaultToken persists a profile's token for a vault (trimmed).
//
// The profile is an explicit argument, not a.activeProfileName(): the caller
// renders under one profile and the user may switch before this lands, so an
// ambient read here writes the token into whichever profile happens to be
// active when the call completes.
func (a *App) SetVaultToken(profile, vault, token string) error {
	p := strings.TrimSpace(profile)
	if p == "" {
		return fmt.Errorf("no profile specified")
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

// GetVaultRecords lists a profile's vault records, head-only.
//
// Profile is explicit so a response cannot be rendered under a profile other
// than the one it was read for.
func (a *App) GetVaultRecords(profile, vault string) ([]VaultRecord, error) {
	profile = strings.TrimSpace(profile)
	if profile == "" {
		return nil, fmt.Errorf("no profile specified")
	}
	token := a.tokenFor(vault, profile)
	if token == "" {
		return nil, fmt.Errorf("no %s-vault token for profile %q", vault, profile)
	}
	return a.listVaultRecords(vault, token)
}

// VaultAuth is the AUTH-reachability of one vault for the active profile — a
// bearer-token check against the mesh daemon, distinct from the unauthenticated
// transport/sync status in GetMeshStatus.
type VaultAuth struct {
	Vault    string `json:"vault"`
	HasToken bool   `json:"has_token"`
	OK       bool   `json:"ok"`     // daemon accepted the token
	Detail   string `json:"detail"` // "ok" | "no token" | "rejected (401)" | "HTTP N" | "daemon unreachable"
}

// VaultAuthStatus probes the active profile's bearer token against the mesh
// daemon for each browsable vault, so the mesh view can show whether auth
// works — not just whether peers are syncing. One cheap head-only limit-1
// request per vault.
func (a *App) VaultAuthStatus(profile string) []VaultAuth {
	vaults := []string{"skills", "agents", "todo"}
	profile = strings.TrimSpace(profile)
	out := make([]VaultAuth, 0, len(vaults))
	for _, v := range vaults {
		va := VaultAuth{Vault: v}
		token := a.tokenFor(v, profile)
		if token == "" {
			va.Detail = "no token"
			out = append(out, va)
			continue
		}
		va.HasToken = true
		va.OK, va.Detail = a.probeVaultAuth(v, token)
		out = append(out, va)
	}
	return out
}

// probeVaultAuth does a minimal authenticated request and classifies the result.
func (a *App) probeVaultAuth(vault, token string) (bool, string) {
	endpoint := a.meshURL() + "/api/brain/records?head=true&limit=1"
	if k := vaultKind(vault); k != "" {
		endpoint += "&kind=" + k
	}
	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return false, "request error"
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := (&http.Client{Timeout: 8 * time.Second}).Do(req)
	if err != nil {
		return false, "daemon unreachable"
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusUnauthorized:
		return false, "rejected (401)"
	case resp.StatusCode >= 200 && resp.StatusCode < 300:
		return true, "ok"
	default:
		return false, fmt.Sprintf("HTTP %d", resp.StatusCode)
	}
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

// ForgetVaultRecord deletes one record by SHA from a profile's vault.
//
// Destructive, so the profile MUST be explicit: reading it ambiently meant a
// delete issued from a panel rendered under profile A could land after a
// profile switch and tombstone a record in profile B instead.
func (a *App) ForgetVaultRecord(profile, vault, sha string) error {
	profile = strings.TrimSpace(profile)
	if profile == "" {
		return fmt.Errorf("no profile specified")
	}
	token := a.tokenFor(vault, profile)
	if token == "" {
		return fmt.Errorf("no %s-vault token for profile %q", vault, profile)
	}
	return a.forgetWithToken(sha, token)
}

// CopyVaultRecord copies a record from srcProfile's vault into destProfile's
// same vault (re-learned; verbatim ⇒ identical SHA). Source kept.
//
// Both ends are explicit. The source used to be read from the active profile
// while only the destination was an argument, so a move that landed after a
// profile switch took the record out of the wrong profile.
func (a *App) CopyVaultRecord(srcProfile, vault, sha, destProfile string) error {
	return a.copyOrMove(srcProfile, vault, sha, destProfile, false)
}

// MoveVaultRecord copies then forgets the source — a profile-to-profile move.
func (a *App) MoveVaultRecord(srcProfile, vault, sha, destProfile string) error {
	return a.copyOrMove(srcProfile, vault, sha, destProfile, true)
}

func (a *App) copyOrMove(srcProfile, vault, sha, destProfile string, move bool) error {
	src := strings.TrimSpace(srcProfile)
	if src == "" {
		return fmt.Errorf("no source profile specified")
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

// ── Version history (supersession chains) ───────────────────────────────────
//
// A vault record is EDITED by learning a new record and linking the old one to
// it with `supersede`, so a logical document is a chain of SHAs. The browser
// lists head-only (`?head=true`), which keeps the list clean but makes every
// earlier version unreachable from the app — these two methods are how the
// reader walks back:
//
//   GET /api/brain/history/{sha} → the whole chain, oldest → newest
//   GET /api/brain/fetch/{sha}   → one version's body, superseded ones included
//
// The daemon's history entries carry no timestamp, so GetVaultRecordHistory
// enriches each one with its record's UpdatedAt (a concurrent fetch per chain
// member — chains are a handful of entries against a local daemon).

// VaultVersion is one entry in a record's supersession chain.
type VaultVersion struct {
	SHA   string `json:"sha"`
	Title string `json:"title"`
	Kind  string `json:"kind"`
	// Version is 1-based with the OLDEST version as 1, so the label is stable
	// as new versions land (v3 stays v3 once v4 exists).
	Version    int       `json:"version"`
	Superseded bool      `json:"superseded"`
	IsHead     bool      `json:"is_head"`
	UpdatedAt  time.Time `json:"updated_at"`
}

type vaultHistoryResponse struct {
	Chain []struct {
		SHA        string `json:"sha"`
		Title      string `json:"title"`
		Kind       string `json:"kind"`
		Superseded bool   `json:"superseded"`
		IsHead     bool   `json:"is_head"`
	} `json:"chain"`
	HeadSHA string `json:"head_sha"`
}

// GetVaultRecordHistory returns the supersession chain containing sha,
// NEWEST FIRST so the reader's dropdown can default to the head.
//
// A record with no edits comes back as a single entry; the panel treats that
// as "no versions to choose from" and shows no dropdown.
//
// Profile is explicit for the same reason as every other method here: the
// caller renders under one profile and the user may switch mid-flight.
func (a *App) GetVaultRecordHistory(profile, vault, sha string) ([]VaultVersion, error) {
	profile = strings.TrimSpace(profile)
	if profile == "" {
		return nil, fmt.Errorf("no profile specified")
	}
	token := a.tokenFor(vault, profile)
	if token == "" {
		return nil, fmt.Errorf("no %s-vault token for profile %q", vault, profile)
	}
	return vaultHistory(a.meshURL(), vault, token, sha)
}

// GetVaultRecordVersion reads one version's full record by SHA — including a
// superseded one, which the head-only list cannot return.
func (a *App) GetVaultRecordVersion(profile, vault, sha string) (VaultRecord, error) {
	profile = strings.TrimSpace(profile)
	if profile == "" {
		return VaultRecord{}, fmt.Errorf("no profile specified")
	}
	token := a.tokenFor(vault, profile)
	if token == "" {
		return VaultRecord{}, fmt.Errorf("no %s-vault token for profile %q", vault, profile)
	}
	return fetchVaultRecord(a.meshURL(), vault, token, sha)
}

// vaultHistory does the /history read and the timestamp enrichment. base is a
// parameter (rather than a.meshURL()) so it is testable against httptest.
func vaultHistory(base, vault, token, sha string) ([]VaultVersion, error) {
	sha = strings.TrimSpace(sha)
	if sha == "" {
		return nil, fmt.Errorf("empty sha")
	}
	var out vaultHistoryResponse
	if err := vaultGetJSON(base+"/api/brain/history/"+sha, vault, token, &out); err != nil {
		return nil, err
	}

	// Number oldest→newest, then reverse so the head leads.
	versions := make([]VaultVersion, 0, len(out.Chain))
	for i, e := range out.Chain {
		versions = append(versions, VaultVersion{
			SHA:        e.SHA,
			Title:      e.Title,
			Kind:       e.Kind,
			Version:    i + 1,
			Superseded: e.Superseded,
			IsHead:     e.IsHead,
		})
	}

	// Best-effort timestamps: a chain entry whose record was forgotten leaves
	// the link but no row, so a failed fetch just means no date on that entry.
	var wg sync.WaitGroup
	for i := range versions {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			if rec, err := fetchVaultRecord(base, vault, token, versions[i].SHA); err == nil {
				versions[i].UpdatedAt = rec.UpdatedAt
			}
		}(i)
	}
	wg.Wait()

	for l, r := 0, len(versions)-1; l < r; l, r = l+1, r-1 {
		versions[l], versions[r] = versions[r], versions[l]
	}
	return versions, nil
}

func fetchVaultRecord(base, vault, token, sha string) (VaultRecord, error) {
	sha = strings.TrimSpace(sha)
	if sha == "" {
		return VaultRecord{}, fmt.Errorf("empty sha")
	}
	var rec VaultRecord
	if err := vaultGetJSON(base+"/api/brain/fetch/"+sha, vault, token, &rec); err != nil {
		return VaultRecord{}, err
	}
	return rec, nil
}

// vaultGetJSON is the shared authenticated GET + decode, with the same error
// vocabulary the browser already switches on ("token rejected" → re-prompt).
func vaultGetJSON(endpoint, vault, token string, dest any) error {
	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return fmt.Errorf("build %s request: %w", vault, err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
	if err != nil {
		return fmt.Errorf("mesh daemon unreachable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusUnauthorized {
		return fmt.Errorf("%s-vault token rejected (401) — check the token in Settings", vault)
	}
	if resp.StatusCode == http.StatusNotFound {
		return fmt.Errorf("record not found in the %s vault", vault)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("mesh daemon returned HTTP %d", resp.StatusCode)
	}
	if err := json.NewDecoder(resp.Body).Decode(dest); err != nil {
		return fmt.Errorf("decode %s response: %w", vault, err)
	}
	return nil
}
