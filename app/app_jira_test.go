package main

import (
	"database/sql"
	"testing"
)

// newJiraTestApp builds an App over an in-memory SQLite with just the two
// tables these gates touch. Mirrors the &App{db: &DB{conn: ...}} construction
// already used by app_conformance_test.go — there is no NewDB constructor.
func newJiraTestApp(t *testing.T) *App {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	conn.SetMaxOpenConns(1)
	t.Cleanup(func() { conn.Close() })
	if _, err := conn.Exec(`
		CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
		CREATE TABLE integrations (
			name       TEXT PRIMARY KEY,
			enabled    INTEGER NOT NULL DEFAULT 0,
			remote     INTEGER NOT NULL DEFAULT 0,
			local_url  TEXT NOT NULL DEFAULT '',
			remote_url TEXT NOT NULL DEFAULT ''
		);
		CREATE TABLE jira_links (
			profile    TEXT NOT NULL,
			issue_key  TEXT NOT NULL,
			row_key    TEXT NOT NULL,
			created_at TEXT NOT NULL DEFAULT '',
			PRIMARY KEY (profile, issue_key, row_key)
		);`); err != nil {
		t.Fatalf("schema: %v", err)
	}
	return &App{db: &DB{conn: conn}}
}

func workEnv() map[string]string {
	return map[string]string{
		"JIRA_URL":       "https://work.atlassian.net",
		"JIRA_USERNAME":  "me@work.com",
		"JIRA_API_TOKEN": "work-token",
	}
}

// --- credentials come from the PROFILE's gateway env, never app-level -------

func TestJiraConfigFromEnv(t *testing.T) {
	cfg, ok := jiraConfigFromEnv(workEnv())
	if !ok {
		t.Fatal("complete env reported unusable")
	}
	if cfg.BaseURL != "https://work.atlassian.net" || cfg.Username != "me@work.com" || cfg.Token != "work-token" {
		t.Fatalf("config not mapped: %+v", cfg)
	}
}

func TestJiraConfigFromEnvIncomplete(t *testing.T) {
	for name, env := range map[string]map[string]string{
		"no url":      {"JIRA_USERNAME": "u", "JIRA_API_TOKEN": "t"},
		"no username": {"JIRA_URL": "https://x", "JIRA_API_TOKEN": "t"},
		"no token":    {"JIRA_URL": "https://x", "JIRA_USERNAME": "u"},
		"empty":       {},
		"blank token": {"JIRA_URL": "https://x", "JIRA_USERNAME": "u", "JIRA_API_TOKEN": "   "},
	} {
		if _, ok := jiraConfigFromEnv(env); ok {
			t.Fatalf("%s: reported usable", name)
		}
	}
}

func TestJiraConfigIsPerProfile(t *testing.T) {
	// The whole point of the fix: two profiles, two different Jira sites.
	work, _ := jiraConfigFromEnv(workEnv())
	client, _ := jiraConfigFromEnv(map[string]string{
		"JIRA_URL":       "https://client.atlassian.net",
		"JIRA_USERNAME":  "me@client.com",
		"JIRA_API_TOKEN": "client-token",
	})
	if work.BaseURL == client.BaseURL || work.Token == client.Token {
		t.Fatal("profiles share credentials; they must not")
	}
}

// --- the two DB-backed gates ------------------------------------------------

func TestJiraGloballyDisabledByDefault(t *testing.T) {
	a := newJiraTestApp(t)
	if a.jiraGloballyEnabled() {
		t.Fatal("integration enabled with no row present")
	}
}

func TestSetJiraEnabledTogglesGlobalGate(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.SetJiraEnabled(true); err != nil {
		t.Fatalf("SetJiraEnabled: %v", err)
	}
	if !a.jiraGloballyEnabled() {
		t.Fatal("not enabled after SetJiraEnabled(true)")
	}
	if err := a.SetJiraEnabled(false); err != nil {
		t.Fatalf("SetJiraEnabled(false): %v", err)
	}
	if a.jiraGloballyEnabled() {
		t.Fatal("still enabled after SetJiraEnabled(false)")
	}
}

func TestJiraOptInIsPerProfileAndDefaultsOff(t *testing.T) {
	a := newJiraTestApp(t)
	if a.JiraOptedIn("work") {
		t.Fatal("opt-in defaults on; it must default off")
	}
	if err := a.SetJiraEnabledForProfile("work", true); err != nil {
		t.Fatalf("opt in: %v", err)
	}
	if !a.JiraOptedIn("work") {
		t.Fatal("not opted in after enabling")
	}
	if a.JiraOptedIn("personal") {
		t.Fatal("opting in one profile enabled another")
	}
	if err := a.SetJiraEnabledForProfile("work", false); err != nil {
		t.Fatalf("opt out: %v", err)
	}
	if a.JiraOptedIn("work") {
		t.Fatal("still opted in after opting out")
	}
}

// --- the composite gate -----------------------------------------------------

func TestJiraClientForEnvRequiresAllThreeGates(t *testing.T) {
	a := newJiraTestApp(t)
	env := workEnv()

	// credentials only
	if a.jiraClientForEnv("work", env) != nil {
		t.Fatal("client built with integration off and no opt-in")
	}
	// + global
	a.SetJiraEnabled(true)
	if a.jiraClientForEnv("work", env) != nil {
		t.Fatal("client built without the profile opting in")
	}
	// + opt-in
	a.SetJiraEnabledForProfile("work", true)
	if a.jiraClientForEnv("work", env) == nil {
		t.Fatal("client not built with all three gates passing")
	}
	// a DIFFERENT profile, opted in but with no credentials of its own
	a.SetJiraEnabledForProfile("personal", true)
	if a.jiraClientForEnv("personal", map[string]string{}) != nil {
		t.Fatal("profile with no credentials of its own got a client")
	}
}

func TestJiraStatusReportsPerProfileWithoutLeakingToken(t *testing.T) {
	a := newJiraTestApp(t)
	a.SetJiraEnabled(true)
	a.SetJiraEnabledForProfile("work", true)

	st := a.jiraStatusForEnv("work", workEnv())
	if !st.Configured || !st.OptedIn {
		t.Fatalf("status = %+v, want configured and opted in", st)
	}
	if st.URL != "https://work.atlassian.net" || st.Username != "me@work.com" {
		t.Fatalf("status did not surface url/username: %+v", st)
	}

	bare := a.jiraStatusForEnv("personal", map[string]string{})
	if bare.Configured {
		t.Fatal("profile with no env reported configured")
	}
}
