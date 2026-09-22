package main

import (
	"context"
	"errors"
	"strings"

	"phantom-ink/jira"
	"phantom-ink/provider"
)

// defaultJiraJQL is the "what am I supposed to be doing" query. Editable per
// profile because one person's workflow is not another's — and because being
// wrong about it should never block the operator.
const defaultJiraJQL = "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"

// settingJiraJQLPrefix + <profile> holds that profile's saved query.
const settingJiraJQLPrefix = "jira_jql:"

// CodeRef is one pull request attached to a ticket.
type CodeRef struct {
	RowKey string `json:"row_key"`
	Number int    `json:"number"`
	Title  string `json:"title"`
	Repo   string `json:"repo"`
	URL    string `json:"url"`
	State  string `json:"state"`
	Manual bool   `json:"manual"`
}

// JiraTicket is one row of the Jira tab: the issue plus whatever code points
// at it, derived and manual together.
type JiraTicket struct {
	jira.Issue
	Refs []CodeRef `json:"refs"`
	// OrphanRows are manual links whose pull request is not in the current
	// page of results. They are reported rather than dropped so a stale link
	// has somewhere to be seen and deleted.
	OrphanRows []string `json:"orphan_rows"`
}

// JiraTicketsResult is the whole tab payload. Errors are carried, not returned,
// so a bad JQL shows a message instead of an empty list that reads as "none".
type JiraTicketsResult struct {
	Profile string       `json:"profile"`
	JQL     string       `json:"jql"`
	Tickets []JiraTicket `json:"tickets"`
	Error   string       `json:"error"`
}

// GetJiraJQL returns this profile's saved query, or the default.
func (a *App) GetJiraJQL(profile string) string {
	if a.db == nil {
		return defaultJiraJQL
	}
	if v := strings.TrimSpace(a.db.GetSetting(settingJiraJQLPrefix+profile, "")); v != "" {
		return v
	}
	return defaultJiraJQL
}

// SetJiraJQL saves a profile's query. Empty is refused: Jira reads an empty
// query as the entire instance.
func (a *App) SetJiraJQL(profile, jql string) error {
	if a.db == nil {
		return errNoDB
	}
	if strings.TrimSpace(jql) == "" {
		return errors.New("jql required")
	}
	return a.db.SetSetting(settingJiraJQLPrefix+profile, strings.TrimSpace(jql))
}

// buildTickets joins issues to the code that references them. Derived refs come
// from scanning the same pull requests the Code tab already loaded; manual refs
// come from the overrides table. Jira's own ordering is preserved.
func buildTickets(issues []jira.Issue, prs []provider.Item, manualByIssue map[string][]string) []JiraTicket {
	byRow := make(map[string]provider.Item, len(prs))
	derived := make(map[string][]string) // issue key -> row keys
	for _, pr := range prs {
		rk := rowKey(pr)
		byRow[rk] = pr
		for _, key := range jira.ParseKeys(pr.Title) {
			derived[key] = append(derived[key], rk)
		}
	}

	out := make([]JiraTicket, 0, len(issues))
	for _, iss := range issues {
		tk := JiraTicket{Issue: iss}
		seen := make(map[string]bool)

		for _, rk := range derived[iss.Key] {
			if pr, ok := byRow[rk]; ok && !seen[rk] {
				seen[rk] = true
				tk.Refs = append(tk.Refs, codeRefFrom(rk, pr, false))
			}
		}
		for _, rk := range manualByIssue[iss.Key] {
			if seen[rk] {
				continue // already derived; it is not removable, so not manual
			}
			pr, ok := byRow[rk]
			if !ok {
				tk.OrphanRows = append(tk.OrphanRows, rk)
				continue
			}
			seen[rk] = true
			tk.Refs = append(tk.Refs, codeRefFrom(rk, pr, true))
		}
		out = append(out, tk)
	}
	return out
}

func codeRefFrom(rowKey string, pr provider.Item, manual bool) CodeRef {
	return CodeRef{
		RowKey: rowKey,
		Number: pr.Number,
		Title:  pr.Title,
		Repo:   pr.RepoFullName,
		URL:    pr.HTMLURL,
		State:  pr.State,
		Manual: manual,
	}
}

// JiraTickets is the Jira tab's loader: run the profile's JQL, then attach the
// code that references each ticket.
func (a *App) JiraTickets(profile string) (JiraTicketsResult, error) {
	res := JiraTicketsResult{Profile: profile, JQL: a.GetJiraJQL(profile)}

	env, err := a.GetGatewayEnv(profile)
	if err != nil {
		return res, err
	}
	c := a.jiraClientForEnv(profile, env)
	if c == nil {
		res.Error = "jira is not enabled for this profile"
		return res, nil
	}

	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	issues, err := c.Search(ctx, res.JQL, 50)
	if err != nil {
		// Carried, not returned: a JQL typo must read as a message on the tab.
		res.Error = err.Error()
		return res, nil
	}

	// The pull requests are whatever the Code tab would show for this profile.
	var prs []provider.Item
	if ov, err := a.CodeOverview(profile); err == nil {
		prs = ov.PullRequests
	}
	var manual map[string][]string
	if a.db != nil {
		manual, _ = a.db.JiraLinksByIssue(profile)
	}
	res.Tickets = buildTickets(issues, prs, manual)
	return res, nil
}

// LinkJiraIssue records a manual link between a ticket and a pull-request row.
func (a *App) LinkJiraIssue(profile, issueKey, rowKey string) error {
	if a.db == nil {
		return errNoDB
	}
	return a.db.AddJiraLink(profile, issueKey, rowKey)
}

// UnlinkJiraIssue removes one. Only manual links can be removed; a derived one
// would return on the next read.
func (a *App) UnlinkJiraIssue(profile, issueKey, rowKey string) error {
	if a.db == nil {
		return errNoDB
	}
	return a.db.RemoveJiraLink(profile, issueKey, rowKey)
}
