package main

import (
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// Manual code <-> Jira links
// ---------------------------------------------------------------------------
//
// Conventional links are DERIVED from PR titles and never stored. This table
// holds only the EXCEPTIONS — a PR whose title does not name its ticket — so
// the stored set stays small enough to read. Every accessor is scoped by
// profile; a link can never cross profiles.

// AddJiraLink records one manual link. Idempotent: re-linking the same pair is
// a no-op rather than an error, so a double click costs nothing.
func (db *DB) AddJiraLink(profile, issueKey, rowKey string) error {
	profile, issueKey, rowKey = strings.TrimSpace(profile), strings.TrimSpace(issueKey), strings.TrimSpace(rowKey)
	if profile == "" || issueKey == "" || rowKey == "" {
		return errNoDB
	}
	_, err := db.conn.Exec(
		`INSERT INTO jira_links (profile, issue_key, row_key, created_at)
		 VALUES (?, ?, ?, ?)
		 ON CONFLICT(profile, issue_key, row_key) DO NOTHING`,
		profile, issueKey, rowKey, time.Now().UTC().Format(time.RFC3339))
	return err
}

// RemoveJiraLink deletes one manual link. Removing a link that is not there is
// not an error — the caller's intent (it should not exist) is satisfied.
func (db *DB) RemoveJiraLink(profile, issueKey, rowKey string) error {
	_, err := db.conn.Exec(
		`DELETE FROM jira_links WHERE profile = ? AND issue_key = ? AND row_key = ?`,
		strings.TrimSpace(profile), strings.TrimSpace(issueKey), strings.TrimSpace(rowKey))
	return err
}

// JiraLinks returns one profile's manual links keyed by row: rowKey -> issue keys.
// This is the direction the Code tab needs.
func (db *DB) JiraLinks(profile string) (map[string][]string, error) {
	return db.jiraLinks(profile, false)
}

// JiraLinksByIssue returns the same links keyed the other way: issue key ->
// rowKeys. This is the direction the Jira tab needs.
func (db *DB) JiraLinksByIssue(profile string) (map[string][]string, error) {
	return db.jiraLinks(profile, true)
}

func (db *DB) jiraLinks(profile string, byIssue bool) (map[string][]string, error) {
	rows, err := db.conn.Query(
		`SELECT issue_key, row_key FROM jira_links WHERE profile = ? ORDER BY issue_key, row_key`,
		strings.TrimSpace(profile))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make(map[string][]string)
	for rows.Next() {
		var issueKey, rowKey string
		if err := rows.Scan(&issueKey, &rowKey); err != nil {
			continue
		}
		if byIssue {
			out[issueKey] = append(out[issueKey], rowKey)
		} else {
			out[rowKey] = append(out[rowKey], issueKey)
		}
	}
	return out, rows.Err()
}
