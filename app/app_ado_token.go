package main

// Post-save validation for a curated ADO connection. Saving org/project/PAT to
// the gateway env store delivers config; it does not prove ADO accepts it. The
// editor calls ValidateADOConnection after a save so a bad PAT or a wrong
// org/project surfaces immediately, not on the first failed read.

import (
	"context"
	"encoding/base64"
	"net/http"
	"net/url"
	"time"
)

const adoAPIBase = "https://dev.azure.com"

// ADOConnectionStatus is the verdict returned to the UI.
type ADOConnectionStatus struct {
	Valid   bool   `json:"valid"`
	Checked bool   `json:"checked"`
	Message string `json:"message"`
}

// ValidateADOConnection checks whether ADO accepts the org + (first) project +
// credential for a profile. Bound to the UI. project may be a comma-separated
// list (multi-project profiles); the first is probed as a representative check.
// A blank PAT validates the profile's az login path.
func (a *App) ValidateADOConnection(profile, org, project, pat string) ADOConnectionStatus {
	first := ""
	if ps := splitProjects(project); len(ps) > 0 {
		first = ps[0]
	}
	return checkADOConnection(adoAPIBase, org, first, pat, a.profileAzureConfigDir(profile), &http.Client{Timeout: 10 * time.Second})
}

// checkADOConnection probes the project's repositories endpoint (which requires
// a resolvable org+project AND a working credential). The credential is the PAT
// when given (Basic), otherwise a bearer minted from the operator's az login
// session. 200 → valid; 401/403 → rejected; anything else → inconclusive.
func checkADOConnection(base, org, project, pat, azConfigDir string, hc *http.Client) ADOConnectionStatus {
	if org == "" || project == "" {
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "org and at least one project are required"}
	}
	var auth string
	if pat != "" {
		auth = "Basic " + base64.StdEncoding.EncodeToString([]byte(":"+pat))
	} else {
		// No PAT — validate the az login path the client will actually use,
		// scoped to this profile's az session.
		h, err := adoAzAuthHeader(context.Background(), azConfigDir)
		if err != nil {
			return ADOConnectionStatus{Checked: false, Message: "no ADO_PAT and az login unavailable: " + err.Error()}
		}
		auth = h
	}
	uri := base + "/" + url.PathEscape(org) + "/" + url.PathEscape(project) + "/_apis/git/repositories?api-version=7.1"
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, uri, nil)
	if err != nil {
		return ADOConnectionStatus{Checked: false, Message: "could not build request"}
	}
	req.Header.Set("Authorization", auth)
	req.Header.Set("Accept", "application/json")
	resp, err := hc.Do(req)
	if err != nil {
		return ADOConnectionStatus{Checked: false, Message: "could not reach Azure DevOps: " + err.Error()}
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusOK:
		return ADOConnectionStatus{Valid: true, Checked: true, Message: "Azure DevOps accepted the connection"}
	case resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusNonAuthoritativeInfo:
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "Azure DevOps rejected the credential (401) — expired/invalid PAT, or run az login"}
	case resp.StatusCode == http.StatusForbidden:
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "PAT accepted but lacks access to this org/project (403)"}
	case resp.StatusCode == http.StatusNotFound:
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "org or project not found (404) — check ADO_ORG / ADO_PROJECT"}
	default:
		return ADOConnectionStatus{Checked: false, Message: "inconclusive (Azure DevOps returned " + resp.Status + ")"}
	}
}
