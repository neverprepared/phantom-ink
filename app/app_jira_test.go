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
		);`); err != nil {
		t.Fatalf("schema: %v", err)
	}
	return &App{db: &DB{conn: conn}}
}

func TestJiraDisabledWhenIntegrationOff(t *testing.T) {
	a := newJiraTestApp(t)
	// Credentials present, profile opted in, but the operator has NOT enabled
	// the integration: the global gate alone must keep it off.
	if err := a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "tok", false); err != nil {
		t.Fatalf("SetJiraSettings: %v", err)
	}
	if err := a.SetJiraEnabledForProfile("work", true); err != nil {
		t.Fatalf("SetJiraEnabledForProfile: %v", err)
	}
	if a.JiraEnabledForProfile("work") {
		t.Fatal("enabled while the integration itself is off")
	}
}

func TestJiraDisabledWhenCredentialsIncomplete(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "", true); err != nil {
		t.Fatalf("SetJiraSettings: %v", err)
	}
	a.SetJiraEnabledForProfile("work", true)
	if a.JiraEnabledForProfile("work") {
		t.Fatal("enabled with an empty token")
	}
}

func TestJiraDisabledUntilProfileOptsIn(t *testing.T) {
	a := newJiraTestApp(t)
	if err := a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "tok", true); err != nil {
		t.Fatalf("SetJiraSettings: %v", err)
	}
	if a.JiraEnabledForProfile("work") {
		t.Fatal("profile enabled by default; opt-in must default off")
	}
	if err := a.SetJiraEnabledForProfile("work", true); err != nil {
		t.Fatalf("SetJiraEnabledForProfile: %v", err)
	}
	if !a.JiraEnabledForProfile("work") {
		t.Fatal("profile not enabled after opting in")
	}
	if a.JiraEnabledForProfile("personal") {
		t.Fatal("opting in one profile enabled another")
	}
}

func TestJiraOptOutIsReversible(t *testing.T) {
	a := newJiraTestApp(t)
	a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "tok", true)
	a.SetJiraEnabledForProfile("work", true)
	if err := a.SetJiraEnabledForProfile("work", false); err != nil {
		t.Fatalf("opt out: %v", err)
	}
	if a.JiraEnabledForProfile("work") {
		t.Fatal("still enabled after opting out")
	}
}

func TestGetJiraSettingsNeverReturnsToken(t *testing.T) {
	a := newJiraTestApp(t)
	a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "supersecret", true)
	for k, v := range a.GetJiraSettings() {
		if v == "supersecret" {
			t.Fatalf("GetJiraSettings leaked the token in key %q", k)
		}
	}
}

func TestGetJiraSettingsReportsTokenPresence(t *testing.T) {
	a := newJiraTestApp(t)
	a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "", true)
	if got := a.GetJiraSettings()["token_set"]; got != "false" {
		t.Fatalf("token_set = %q with no token, want false", got)
	}
	a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "supersecret", true)
	if got := a.GetJiraSettings()["token_set"]; got != "true" {
		t.Fatalf("token_set = %q with a token stored, want true", got)
	}
}

func TestJiraClientNilWhenProfileDisabled(t *testing.T) {
	a := newJiraTestApp(t)
	a.SetJiraSettings("https://x.atlassian.net", "me@example.com", "tok", true)
	if a.jiraClientFor("work") != nil {
		t.Fatal("client returned for a profile that has not opted in")
	}
}
