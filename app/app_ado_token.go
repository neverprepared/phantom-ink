package main

// Post-save validation for a curated ADO connection. Saving org/project/PAT to
// the gateway env store delivers config; it does not prove ADO accepts it. The
// editor calls ValidateADOConnection after a save so a bad PAT or a wrong
// org/project surfaces immediately, not on the first failed read.

import (
	"context"
	"encoding/base64"
	"net/http"
	"time"
)

const adoAPIBase = "https://dev.azure.com"

// ADOConnectionStatus is the verdict returned to the UI.
type ADOConnectionStatus struct {
	Valid   bool   `json:"valid"`
	Checked bool   `json:"checked"`
	Message string `json:"message"`
}

// ValidateADOConnection checks whether ADO accepts the org/project/PAT. Bound to the UI.
func (a *App) ValidateADOConnection(org, project, pat string) ADOConnectionStatus {
	return checkADOConnection(adoAPIBase, org, project, pat, &http.Client{Timeout: 10 * time.Second})
}

// checkADOConnection probes the project's repositories endpoint (which requires
// a valid PAT AND a resolvable org+project). 200 → valid; 401/403 → rejected;
// anything else → inconclusive (leave the UI neutral).
func checkADOConnection(base, org, project, pat string, hc *http.Client) ADOConnectionStatus {
	if org == "" || project == "" || pat == "" {
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "org, project, and PAT are all required"}
	}
	uri := base + "/" + org + "/" + project + "/_apis/git/repositories?api-version=7.1"
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, uri, nil)
	if err != nil {
		return ADOConnectionStatus{Checked: false, Message: "could not build request"}
	}
	req.Header.Set("Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte(":"+pat)))
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
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "Azure DevOps rejected the PAT (401) — expired or wrong value"}
	case resp.StatusCode == http.StatusForbidden:
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "PAT accepted but lacks access to this org/project (403)"}
	case resp.StatusCode == http.StatusNotFound:
		return ADOConnectionStatus{Valid: false, Checked: true, Message: "org or project not found (404) — check ADO_ORG / ADO_PROJECT"}
	default:
		return ADOConnectionStatus{Checked: false, Message: "inconclusive (Azure DevOps returned " + resp.Status + ")"}
	}
}
