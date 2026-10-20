package main

import (
	"database/sql"
	"testing"
)

// The ticket memo caches the RESULT of a query plus the links baked into it,
// so every write that changes either must drop it. Serving the old query's
// rows after saving a new one makes the editor look broken.

func newJiraMemoDB(t *testing.T) *DB {
	t.Helper()
	conn, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	conn.SetMaxOpenConns(1)
	if _, err := conn.Exec(`
		CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
		CREATE TABLE jira_links (
			profile    TEXT NOT NULL,
			issue_key  TEXT NOT NULL,
			row_key    TEXT NOT NULL,
			created_at TEXT NOT NULL DEFAULT '',
			PRIMARY KEY (profile, issue_key, row_key)
		);
	`); err != nil {
		t.Fatalf("schema: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	return &DB{conn: conn}
}

func TestSetJiraJQLInvalidatesOnlyThatProfile(t *testing.T) {
	jiraTicketsMemo.invalidate("")
	storeJiraTickets("work", JiraTicketsResult{Profile: "work"})
	storeJiraTickets("personal", JiraTicketsResult{Profile: "personal"})

	app := &App{db: newJiraMemoDB(t)}
	if err := app.SetJiraJQL("work", "assignee = currentUser()"); err != nil {
		t.Fatalf("SetJiraJQL: %v", err)
	}

	if _, _, ok := jiraTicketsMemo.get("work"); ok {
		t.Error("the edited profile kept tickets from its OLD query")
	}
	if _, _, ok := jiraTicketsMemo.get("personal"); !ok {
		t.Error("an unrelated profile lost its tickets")
	}
}

// An empty JQL is refused, and a refused write must not drop the memo.
func TestSetJiraJQLRejectionKeepsMemo(t *testing.T) {
	jiraTicketsMemo.invalidate("")
	storeJiraTickets("work", JiraTicketsResult{Profile: "work"})

	app := &App{db: newJiraMemoDB(t)}
	if err := app.SetJiraJQL("work", "   "); err == nil {
		t.Fatal("an empty JQL should be refused")
	}
	if _, _, ok := jiraTicketsMemo.get("work"); !ok {
		t.Error("a REFUSED write dropped the memo")
	}
}

func TestLinkAndUnlinkJiraIssueInvalidateTickets(t *testing.T) {
	db := newJiraMemoDB(t)
	app := &App{db: db}

	for _, tc := range []struct {
		name string
		call func() error
	}{
		{"link", func() error { return app.LinkJiraIssue("work", "ENG-1", "github:o/r#1") }},
		{"unlink", func() error { return app.UnlinkJiraIssue("work", "ENG-1", "github:o/r#1") }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			jiraTicketsMemo.invalidate("")
			storeJiraTickets("work", JiraTicketsResult{Profile: "work"})
			storeJiraTickets("personal", JiraTicketsResult{Profile: "personal"})

			if err := tc.call(); err != nil {
				t.Fatalf("%s: %v", tc.name, err)
			}
			if _, _, ok := jiraTicketsMemo.get("work"); ok {
				t.Error("the link change did not drop this profile's tickets")
			}
			if _, _, ok := jiraTicketsMemo.get("personal"); !ok {
				t.Error("an unrelated profile lost its tickets")
			}
		})
	}
}

// Per-profile opt-in gates whether the profile may see Jira at all.
func TestSetJiraEnabledForProfileInvalidatesThatProfile(t *testing.T) {
	jiraTicketsMemo.invalidate("")
	storeJiraTickets("work", JiraTicketsResult{Profile: "work"})
	storeJiraTickets("personal", JiraTicketsResult{Profile: "personal"})

	app := &App{db: newJiraMemoDB(t)}
	if err := app.SetJiraEnabledForProfile("work", false); err != nil {
		t.Fatalf("SetJiraEnabledForProfile: %v", err)
	}

	if _, _, ok := jiraTicketsMemo.get("work"); ok {
		t.Error("opting a profile out left its tickets memoised")
	}
	if _, _, ok := jiraTicketsMemo.get("personal"); !ok {
		t.Error("an unrelated profile lost its tickets")
	}
}

// storeJiraTickets stamps the data's own age, so a served copy can be labelled.
func TestStoreJiraTicketsStampsFetchedAt(t *testing.T) {
	jiraTicketsMemo.invalidate("")
	storeJiraTickets("work", JiraTicketsResult{Profile: "work", Stale: true})

	res, _, ok := jiraTicketsMemo.get("work")
	if !ok {
		t.Fatal("nothing stored")
	}
	if res.FetchedAt == "" {
		t.Error("FetchedAt is unset; the tab cannot show the data's age")
	}
	if res.Stale {
		t.Error("a freshly stored result stayed marked Stale")
	}
}
