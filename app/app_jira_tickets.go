package main

import (
	"context"
	"errors"
	"strings"
	"time"

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

	// FetchedAt is when these tickets came off Jira (RFC3339), and Stale marks
	// a copy served past the TTL while a refresh runs behind it. Same contract
	// as CodeOverview -- see profile_memo.go.
	FetchedAt string `json:"fetched_at"`
	Stale     bool   `json:"stale"`
}

// jiraTicketsTTL matches the launchpad's: these are the same provider round
// trips seen from a different angle, and a shorter window here would make the
// two tabs disagree about how current "now" is.
const jiraTicketsTTL = 45 * time.Second

var jiraTicketsMemo = newProfileMemo[JiraTicketsResult](jiraTicketsTTL)

// InvalidateJiraTickets drops a profile's memoised tickets, or every profile's
// when profile is empty. Called whenever something that CHANGES the result is
// written: the saved JQL, and manual links.
func (a *App) InvalidateJiraTickets(profile string) {
	jiraTicketsMemo.invalidate(profile)
}

// storeJiraTickets memoises a successful read, stamping the data's own age.
func storeJiraTickets(profile string, res JiraTicketsResult) {
	res.Stale = false
	if res.FetchedAt == "" {
		res.FetchedAt = time.Now().UTC().Format(time.RFC3339)
	}
	jiraTicketsMemo.store(profile, res)
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
	if err := a.db.SetSetting(settingJiraJQLPrefix+profile, strings.TrimSpace(jql)); err != nil {
		return err
	}
	// The memoised tickets ARE the old query's results, so serving them after
	// a new query is saved would make the editor look broken.
	a.InvalidateJiraTickets(profile)
	return nil
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
//
// force bypasses the per-profile memo -- the refresh button passes true, plain
// navigation passes false.
func (a *App) JiraTickets(profile string, force bool) (JiraTicketsResult, error) {
	if force {
		// Same as CodeOverview: refresh means "re-read everything", the
		// profile's credentials included.
		a.invalidateProfileEnv(profile)
	}
	if !force {
		if res, fresh, ok := jiraTicketsMemo.get(profile); ok {
			if fresh {
				return res, nil
			}
			// Stale but displayable: hand it back now, refresh behind it.
			res.Stale = true
			jiraTicketsMemo.revalidate(profile, a.jiraTicketsUncached)
			return res, nil
		}
	}
	return a.jiraTicketsUncached(profile)
}

func (a *App) jiraTicketsUncached(profile string) (JiraTicketsResult, error) {
	res := JiraTicketsResult{Profile: profile, JQL: a.GetJiraJQL(profile)}

	env, err := a.profileEnv(profile)
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
	// Reuse the memo: this is a read of what the Code tab would already show,
	// so it must not trigger a second full provider fan-out.
	if ov, err := a.CodeOverview(profile, false); err == nil {
		prs = ov.PullRequests
	}
	var manual map[string][]string
	if a.db != nil {
		manual, _ = a.db.JiraLinksByIssue(profile)
	}
	res.Tickets = buildTickets(issues, prs, manual)
	// Only a clean read is memoised. A JQL error and "jira is not enabled" are
	// both states the operator fixes and expects to see fixed on the next look,
	// and they return above without reaching this line.
	storeJiraTickets(profile, res)
	return res, nil
}

// LinkJiraIssue records a manual link between a ticket and a pull-request row.
func (a *App) LinkJiraIssue(profile, issueKey, rowKey string) error {
	if a.db == nil {
		return errNoDB
	}
	if err := a.db.AddJiraLink(profile, issueKey, rowKey); err != nil {
		return err
	}
	// The links are baked into the memoised tickets, so a new one would not
	// appear until the TTL expired.
	a.InvalidateJiraTickets(profile)
	return nil
}

// UnlinkJiraIssue removes one. Only manual links can be removed; a derived one
// would return on the next read.
func (a *App) UnlinkJiraIssue(profile, issueKey, rowKey string) error {
	if a.db == nil {
		return errNoDB
	}
	if err := a.db.RemoveJiraLink(profile, issueKey, rowKey); err != nil {
		return err
	}
	a.InvalidateJiraTickets(profile)
	return nil
}
