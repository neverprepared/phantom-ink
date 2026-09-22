package main

import (
	"context"
	"errors"
	"strings"

	"phantom-ink/jira"
)

// jiraIntegrationName is the row key in the integrations table.
const jiraIntegrationName = "jira"

// jiraConfig returns the app-level credential set and whether it is usable.
// Usable means BOTH gates that are not profile-specific: the operator enabled
// the integration, and all three credential parts are present.
func (a *App) jiraConfig() (jira.Config, bool) {
	if a.db == nil {
		return jira.Config{}, false
	}
	row, ok := a.db.GetIntegration(jiraIntegrationName)
	if !ok || !row.Enabled {
		return jira.Config{}, false
	}
	cfg := jira.Config{
		BaseURL:  strings.TrimSpace(row.RemoteURL),
		Username: strings.TrimSpace(a.db.GetSetting(settingJiraUsername, "")),
		Token:    strings.TrimSpace(a.db.GetSetting(settingJiraToken, "")),
	}
	return cfg, cfg.BaseURL != "" && cfg.Username != "" && cfg.Token != ""
}

// JiraEnabledForProfile reports whether this profile should see Jira data.
// All three gates must pass. The opt-in defaults off, so a profile never gains
// a new surface merely because the operator configured credentials.
func (a *App) JiraEnabledForProfile(profile string) bool {
	if _, ok := a.jiraConfig(); !ok {
		return false
	}
	if a.db == nil || strings.TrimSpace(profile) == "" {
		return false
	}
	return a.db.GetSetting(settingJiraProfilePrefix+profile, "") == "true"
}

// SetJiraEnabledForProfile records one profile's opt-in.
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

// GetJiraSettings returns the non-secret settings for the Integrations UI.
// The token is deliberately absent: same contract as GetRegistrySettings.
func (a *App) GetJiraSettings() map[string]string {
	if a.db == nil {
		return map[string]string{"url": "", "username": "", "enabled": "false", "token_set": "false"}
	}
	row, _ := a.db.GetIntegration(jiraIntegrationName)
	enabled := "false"
	if row.Enabled {
		enabled = "true"
	}
	// token_set reports PRESENCE only — never the value — so the UI can say
	// "stored, leave blank to keep" without the token crossing the boundary.
	tokenSet := "false"
	if strings.TrimSpace(a.db.GetSetting(settingJiraToken, "")) != "" {
		tokenSet = "true"
	}
	return map[string]string{
		"url":       strings.TrimSpace(row.RemoteURL),
		"username":  strings.TrimSpace(a.db.GetSetting(settingJiraUsername, "")),
		"enabled":   enabled,
		"token_set": tokenSet,
	}
}

// SetJiraSettings stores the app-level credential set and the integration row.
// Enabling here is the operator's global switch; profiles still opt in.
func (a *App) SetJiraSettings(url, username, token string, enabled bool) error {
	if a.db == nil {
		return errNoDB
	}
	if err := a.db.UpsertIntegration(IntegrationRow{
		Name:      jiraIntegrationName,
		Enabled:   enabled,
		Remote:    true, // saas: always remote, never a placed compose stack
		RemoteURL: strings.TrimRight(strings.TrimSpace(url), "/"),
	}); err != nil {
		return err
	}
	if err := a.db.SetSetting(settingJiraUsername, strings.TrimSpace(username)); err != nil {
		return err
	}
	return a.db.SetSetting(settingJiraToken, strings.TrimSpace(token))
}

// jiraClientFor returns a client, or nil when this profile must see no Jira
// data. Callers treat nil as "skip Jira entirely" — not as an error.
func (a *App) jiraClientFor(profile string) *jira.Client {
	if !a.JiraEnabledForProfile(profile) {
		return nil
	}
	cfg, ok := a.jiraConfig()
	if !ok {
		return nil
	}
	return jira.New(cfg, nil)
}

// VerifyJira is the Integrations status probe: a saas integration has no
// container to inspect, so "up" means the credentials work.
func (a *App) VerifyJira() error {
	cfg, ok := a.jiraConfig()
	if !ok {
		return errors.New("jira: not enabled or credentials incomplete")
	}
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	return jira.New(cfg, nil).Verify(ctx)
}
