package main

import (
	"context"
	"errors"
	"strings"

	"phantom-ink/jira"
)

// jiraIntegrationName is the row key in the integrations table.
const jiraIntegrationName = "jira"

// JiraProfileStatus is what the settings card renders for one profile. It
// carries the site and account so the operator can see WHICH Jira a profile is
// pointed at — never the token.
type JiraProfileStatus struct {
	Profile    string `json:"profile"`
	Configured bool   `json:"configured"`
	OptedIn    bool   `json:"opted_in"`
	URL        string `json:"url"`
	Username   string `json:"username"`
}

// jiraConfigFromEnv reads a profile's Jira credentials out of its gateway env.
//
// These are the SAME three vars the mcp-atlassian server consumes, so a profile
// configures its Jira once and both the MCP server and the Code panel chips use
// it. Credentials are per profile by construction: two profiles can point at
// two different Atlassian sites, and neither can reach the other's token.
func jiraConfigFromEnv(env map[string]string) (jira.Config, bool) {
	cfg := jira.Config{
		BaseURL:  strings.TrimSpace(env["JIRA_URL"]),
		Username: strings.TrimSpace(env["JIRA_USERNAME"]),
		Token:    strings.TrimSpace(env["JIRA_API_TOKEN"]),
	}
	return cfg, cfg.BaseURL != "" && cfg.Username != "" && cfg.Token != ""
}

// jiraGloballyEnabled reports the operator-level switch: is this integration
// available in the app at all. It says nothing about any one profile.
func (a *App) jiraGloballyEnabled() bool {
	if a.db == nil {
		return false
	}
	row, ok := a.db.GetIntegration(jiraIntegrationName)
	return ok && row.Enabled
}

// SetJiraEnabled flips the global switch. Credentials are NOT stored here —
// they live in each profile's gateway env.
func (a *App) SetJiraEnabled(enabled bool) error {
	if a.db == nil {
		return errNoDB
	}
	row, _ := a.db.GetIntegration(jiraIntegrationName)
	row.Name = jiraIntegrationName
	row.Enabled = enabled
	row.Remote = true // saas: never a placed compose stack
	return a.db.UpsertIntegration(row)
}

// JiraGloballyEnabled is the Wails-bound read of the global switch.
func (a *App) JiraGloballyEnabled() bool { return a.jiraGloballyEnabled() }

// JiraOptedIn reports one profile's opt-in. Defaults off, so configuring
// credentials for the MCP server never silently adds a Code-panel surface.
func (a *App) JiraOptedIn(profile string) bool {
	if a.db == nil || strings.TrimSpace(profile) == "" {
		return false
	}
	return a.db.GetSetting(settingJiraProfilePrefix+profile, "") == "true"
}

// SetJiraEnabledForProfile records one profile's opt-in. Opting out leaves the
// credentials in place, so it is reversible without re-entering a token.
func (a *App) SetJiraEnabledForProfile(profile string, enabled bool) error {
	if a.db == nil {
		return errNoDB
	}
	if strings.TrimSpace(profile) == "" {
		return errors.New("profile required")
	}
	v := "false"
	if enabled {
		v = "true"
	}
	return a.db.SetSetting(settingJiraProfilePrefix+profile, v)
}

// jiraClientForEnv applies all three gates. Returns nil when this profile must
// see no Jira data; callers treat nil as "skip Jira entirely", not an error.
func (a *App) jiraClientForEnv(profile string, env map[string]string) *jira.Client {
	if !a.jiraGloballyEnabled() || !a.JiraOptedIn(profile) {
		return nil
	}
	cfg, ok := jiraConfigFromEnv(env)
	if !ok {
		return nil
	}
	return jira.New(cfg, nil)
}

// jiraStatusForEnv is the testable core of JiraStatus.
func (a *App) jiraStatusForEnv(profile string, env map[string]string) JiraProfileStatus {
	cfg, ok := jiraConfigFromEnv(env)
	return JiraProfileStatus{
		Profile:    profile,
		Configured: ok,
		OptedIn:    a.JiraOptedIn(profile),
		URL:        cfg.BaseURL,
		Username:   cfg.Username,
	}
}

// JiraStatus reports one profile's Jira wiring for the settings card. The
// token never crosses the boundary — only whether the profile is configured.
func (a *App) JiraStatus(profile string) (JiraProfileStatus, error) {
	env, err := a.GetGatewayEnv(profile)
	if err != nil {
		return JiraProfileStatus{Profile: profile}, err
	}
	return a.jiraStatusForEnv(profile, env), nil
}

// VerifyJira probes ONE profile's credentials. A saas integration has no
// container to inspect, so "up" means that profile's credentials work.
func (a *App) VerifyJira(profile string) error {
	env, err := a.GetGatewayEnv(profile)
	if err != nil {
		return err
	}
	cfg, ok := jiraConfigFromEnv(env)
	if !ok {
		return errors.New("jira: set JIRA_URL, JIRA_USERNAME and JIRA_API_TOKEN in this profile's gateway env")
	}
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	return jira.New(cfg, nil).Verify(ctx)
}
