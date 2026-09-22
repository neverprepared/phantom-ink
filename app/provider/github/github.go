// Package github is a tiny READ-ONLY GitHub REST client for the Code panel's
// launchpad view: repositories, open PRs, assigned issues, and notifications
// for whoever the client's bound token belongs to.
//
// It is deliberately not a general GitHub SDK. The token is bound once at
// construction (the Code panel resolves it from the ACTIVE profile's gateway
// env when it builds the client, so nothing is cached across profiles) and
// every method returns small provider.* structs holding only the fields the
// UI renders. The base URL is injectable so tests run against an
// httptest.Server.
package github

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	"phantom-ink/provider"
)

// DefaultBase is the public GitHub REST endpoint.
const DefaultBase = "https://api.github.com"

// requestTimeout caps a single GitHub call. The panel fans five of these out
// concurrently, so a hung endpoint must not hold the page open.
const requestTimeout = 10 * time.Second

// Client talks to one GitHub REST base with one bound token.
type Client struct {
	base  string
	token string
	hc    *http.Client
}

// New returns a client against the public GitHub API, bound to token.
func New(token string) *Client {
	return NewWithBase(DefaultBase, token, &http.Client{Timeout: requestTimeout})
}

// NewWithBase returns a client against an arbitrary base — used by tests to
// point at an httptest.Server. A nil client falls back to a timed default.
func NewWithBase(base, token string, hc *http.Client) *Client {
	if hc == nil {
		hc = &http.Client{Timeout: requestTimeout}
	}
	return &Client{base: strings.TrimRight(base, "/"), token: token, hc: hc}
}

var (
	_ provider.Client   = (*Client)(nil)
	_ provider.Notifier = (*Client)(nil)
)

// Kind reports this client's provider tag.
func (c *Client) Kind() provider.Kind { return provider.KindGitHub }

// errNoToken is returned before any network call when the profile has no
// GITHUB_TOKEN. Hitting GitHub unauthenticated would return a *different*
// account's public view, which would be a cross-profile leak of sorts.
var errNoToken = errors.New("no GITHUB_TOKEN for this profile")

// get issues one authenticated GET and decodes the JSON body into out.
func (c *Client) get(ctx context.Context, path string, out any) error {
	if c.token == "" {
		return errNoToken
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+path, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return &provider.StatusError{Code: resp.StatusCode, Status: resp.Status, Path: path}
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// write performs a mutating request (PATCH/PUT) and treats any 2xx as success —
// GitHub's notification writes answer 205/202 with no body worth decoding.
func (c *Client) write(ctx context.Context, method, path string, body []byte) error {
	if c.token == "" {
		return errNoToken
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return &provider.StatusError{Code: resp.StatusCode, Status: resp.Status, Path: path}
	}
	return nil
}

// MarkNotificationRead marks one notification thread as read. The thread id is
// the Notification.ID returned by ListNotifications.
func (c *Client) MarkNotificationRead(ctx context.Context, threadID string) error {
	return c.write(ctx, http.MethodPatch, "/notifications/threads/"+url.PathEscape(threadID), nil)
}

// MarkAllNotificationsRead marks every notification as read.
func (c *Client) MarkAllNotificationsRead(ctx context.Context) error {
	return c.write(ctx, http.MethodPut, "/notifications", []byte(`{"read":true}`))
}

// --- Repositories -----------------------------------------------------------

// wireRepo is the subset of GitHub's repository payload we decode.
type wireRepo struct {
	Name          string `json:"name"`
	FullName      string `json:"full_name"`
	Description   string `json:"description"`
	HTMLURL       string `json:"html_url"`
	CloneURL      string `json:"clone_url"`
	DefaultBranch string `json:"default_branch"`
	PushedAt      string `json:"pushed_at"`
	Stars         int    `json:"stargazers_count"`
	OpenIssues    int    `json:"open_issues_count"`
	Owner         struct {
		Login string `json:"login"`
	} `json:"owner"`
}

// ListRepos returns the token owner's repositories, most recently pushed first.
func (c *Client) ListRepos(ctx context.Context) ([]provider.Repo, error) {
	q := url.Values{
		"sort":        {"pushed"},
		"per_page":    {"50"},
		"affiliation": {"owner,collaborator,organization_member"},
	}
	var wire []wireRepo
	if err := c.get(ctx, "/user/repos?"+q.Encode(), &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Repo, 0, len(wire))
	for _, w := range wire {
		out = append(out, provider.Repo{
			Provider:      provider.KindGitHub,
			ID:            "",
			Owner:         w.Owner.Login,
			Name:          w.Name,
			FullName:      w.FullName,
			Description:   w.Description,
			HTMLURL:       w.HTMLURL,
			CloneURL:      w.CloneURL,
			DefaultBranch: w.DefaultBranch,
			PushedAt:      w.PushedAt,
			Stars:         w.Stars,
			OpenIssues:    w.OpenIssues,
		})
	}
	return out, nil
}

// --- Issues & pull requests -------------------------------------------------

type wireSearch struct {
	Items []struct {
		Number        int    `json:"number"`
		Title         string `json:"title"`
		State         string `json:"state"`
		HTMLURL       string `json:"html_url"`
		UpdatedAt     string `json:"updated_at"`
		Draft         bool   `json:"draft"`
		RepositoryURL string `json:"repository_url"`
		User          struct {
			Login string `json:"login"`
		} `json:"user"`
		// Presence (not content) marks a search hit as a PR.
		PullRequest *struct {
			URL string `json:"url"`
		} `json:"pull_request"`
	} `json:"items"`
}

// SearchMyPRs runs the authored and review-requested searches and folds them
// through the dedupe-by-HTMLURL-merging-reasons logic, so a PR that is both
// mine AND waiting on my review appears once, tagged with both reasons.
func (c *Client) SearchMyPRs(ctx context.Context) ([]provider.Item, error) {
	authored, err := c.search(ctx, "is:open is:pr author:@me", provider.ReasonAuthored)
	if err != nil {
		return nil, err
	}
	reviews, err := c.search(ctx, "is:open is:pr review-requested:@me", provider.ReasonReviewRequested)
	if err != nil {
		return nil, err
	}
	return mergePRs(authored, reviews), nil
}

// ListAssignedWork lists open issues assigned to the owner, newest first.
func (c *Client) ListAssignedWork(ctx context.Context) ([]provider.Item, error) {
	items, err := c.search(ctx, "is:open is:issue assignee:@me", provider.ReasonAssigned)
	if err != nil {
		return nil, err
	}
	sortItems(items)
	return items, nil
}

func (c *Client) search(ctx context.Context, q, reason string) ([]provider.Item, error) {
	var wire wireSearch
	path := "/search/issues?" + url.Values{"q": {q}, "per_page": {"50"}}.Encode()
	if err := c.get(ctx, path, &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Item, 0, len(wire.Items))
	for _, it := range wire.Items {
		out = append(out, provider.Item{
			Provider:      provider.KindGitHub,
			RepoFullName:  repoFullNameFromAPIURL(it.RepositoryURL),
			Number:        it.Number,
			Title:         it.Title,
			State:         it.State,
			HTMLURL:       it.HTMLURL,
			UpdatedAt:     it.UpdatedAt,
			User:          it.User.Login,
			Draft:         it.Draft,
			IsPullRequest: it.PullRequest != nil,
			Reason:        reason,
			RepoID:        "",
		})
	}
	return out, nil
}

// mergePRs unions the authored and review-requested lists, deduping on
// html_url. A PR that is both mine AND waiting on my review appears once,
// tagged with both reasons, so the row explains why it is on the list.
func mergePRs(lists ...[]provider.Item) []provider.Item {
	byURL := map[string]int{}
	out := make([]provider.Item, 0, 16)
	for _, list := range lists {
		for _, pr := range list {
			key := pr.HTMLURL
			if key == "" {
				// No URL to dedupe on (shouldn't happen); keep the row rather
				// than silently dropping work.
				out = append(out, pr)
				continue
			}
			if i, seen := byURL[key]; seen {
				if !strings.Contains(out[i].Reason, pr.Reason) {
					out[i].Reason += ", " + pr.Reason
				}
				continue
			}
			byURL[key] = len(out)
			out = append(out, pr)
		}
	}
	sortItems(out)
	return out
}

// sortItems orders rows most-recently-updated first. UpdatedAt is RFC3339 from
// GitHub, so a plain string compare is already chronological.
func sortItems(rows []provider.Item) {
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].UpdatedAt > rows[j].UpdatedAt })
}

// --- Notifications ----------------------------------------------------------

type wireNotification struct {
	ID         string `json:"id"`
	Reason     string `json:"reason"`
	UpdatedAt  string `json:"updated_at"`
	Repository struct {
		FullName string `json:"full_name"`
	} `json:"repository"`
	Subject struct {
		Title string `json:"title"`
		Type  string `json:"type"`
		URL   string `json:"url"`
	} `json:"subject"`
}

// ListNotifications returns the owner's unread notifications.
func (c *Client) ListNotifications(ctx context.Context) ([]provider.Notification, error) {
	var wire []wireNotification
	if err := c.get(ctx, "/notifications", &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Notification, 0, len(wire))
	for _, w := range wire {
		out = append(out, provider.Notification{
			ID:           w.ID,
			RepoFullName: w.Repository.FullName,
			SubjectTitle: w.Subject.Title,
			SubjectType:  w.Subject.Type,
			Reason:       w.Reason,
			UpdatedAt:    w.UpdatedAt,
			URL:          subjectBrowserURL(w.Subject.URL, w.Repository.FullName),
		})
	}
	return out, nil
}

// --- URL helpers ------------------------------------------------------------

// repoFullNameFromAPIURL turns "https://api.github.com/repos/o/n" into "o/n".
// Search hits carry no repo object, only this URL.
func repoFullNameFromAPIURL(apiURL string) string {
	i := strings.Index(apiURL, "/repos/")
	if i < 0 {
		return ""
	}
	return strings.Trim(apiURL[i+len("/repos/"):], "/")
}

// subjectBrowserURL rewrites a notification's API subject URL into a link a
// browser can open. Only PR and issue subjects have a clean rewrite; anything
// else (releases, discussions, checks) falls back to the repository page,
// which beats handing the UI a dead api.github.com link.
func subjectBrowserURL(subjectURL, repoFullName string) string {
	repoPage := ""
	if repoFullName != "" {
		repoPage = "https://github.com/" + repoFullName
	}
	full := repoFullNameFromAPIURL(subjectURL)
	if full == "" {
		return repoPage
	}
	parts := strings.Split(full, "/")
	if len(parts) != 4 {
		return repoPage
	}
	owner, name, kind, id := parts[0], parts[1], parts[2], parts[3]
	switch kind {
	case "pulls":
		return fmt.Sprintf("https://github.com/%s/%s/pull/%s", owner, name, id)
	case "issues":
		return fmt.Sprintf("https://github.com/%s/%s/issues/%s", owner, name, id)
	default:
		return repoPage
	}
}

// NormalizeCloneURL turns any GitHub reference to a repo into an https clone
// URL, so the same helper serves a repo row's clone_url, a search hit's
// html_url, an api.github.com URL, and an ssh remote pasted by hand:
//
//	git@github.com:o/r.git              → https://github.com/o/r.git
//	ssh://git@github.com/o/r            → https://github.com/o/r.git
//	https://github.com/o/r              → https://github.com/o/r.git
//	https://api.github.com/repos/o/r    → https://github.com/o/r.git
//
// The host is PRESERVED (only api.github.com is rewritten to github.com) so a
// GitHub Enterprise remote still clones from its own host. Returns "" when no
// owner/repo pair can be read out.
func (c *Client) NormalizeCloneURL(repoURL string) string {
	s := strings.TrimSpace(repoURL)
	s = strings.TrimSuffix(s, "/")
	if s == "" {
		return ""
	}
	// Strip the scheme, then any user@ — leaves host/owner/repo for both
	// "https://github.com/..." and "ssh://git@github.com/...".
	if i := strings.Index(s, "://"); i >= 0 {
		s = s[i+3:]
	} else if at := strings.Index(s, "@"); at >= 0 {
		// scp-style ssh (git@github.com:owner/repo.git): the colon separating
		// host from path is a path separator here, not a port.
		s = strings.Replace(s[at+1:], ":", "/", 1)
	}
	if at := strings.Index(s, "@"); at >= 0 {
		s = s[at+1:]
	}

	segs := strings.Split(s, "/")
	if len(segs) < 3 {
		return ""
	}
	host, segs := segs[0], segs[1:]
	if host == "api.github.com" && segs[0] == "repos" {
		host, segs = "github.com", segs[1:]
	}
	if len(segs) < 2 {
		return ""
	}
	owner, repo := segs[0], strings.TrimSuffix(segs[1], ".git")
	if host == "" || owner == "" || repo == "" {
		return ""
	}
	return "https://" + host + "/" + owner + "/" + repo + ".git"
}

// --- Repository detail ------------------------------------------------------
//
// The five reads behind the Code panel's single-repo view. Same shape as the
// launchpad methods: bound token, small structs, no caching. Every one is a
// plain GET — nothing here can mutate a repository.

type wireBranch struct {
	Name   string `json:"name"`
	Commit struct {
		SHA string `json:"sha"`
	} `json:"commit"`
}

// ListBranches returns up to 50 branches of one repository.
func (c *Client) ListBranches(ctx context.Context, ref provider.RepoRef) ([]provider.Branch, error) {
	var wire []wireBranch
	path := fmt.Sprintf("/repos/%s/%s/branches?per_page=50", url.PathEscape(ref.Owner), url.PathEscape(ref.Name))
	if err := c.get(ctx, path, &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Branch, 0, len(wire))
	for _, w := range wire {
		out = append(out, provider.Branch{Name: w.Name, SHA: w.Commit.SHA})
	}
	return out, nil
}

// wireCommit mirrors GitHub's two-level nesting: the ACCOUNT that authored the
// commit sits at the top level (and is null for an unlinked email), while the
// git trailer — name, date, message — lives under "commit".
type wireCommit struct {
	SHA     string `json:"sha"`
	HTMLURL string `json:"html_url"`
	Commit  struct {
		Message string `json:"message"`
		Author  struct {
			Name string `json:"name"`
			Date string `json:"date"`
		} `json:"author"`
	} `json:"commit"`
	Author *struct {
		Login string `json:"login"`
	} `json:"author"`
}

// defaultCommitLimit is GitHub's own per_page default; used when the caller
// passes a non-positive limit.
const defaultCommitLimit = 30

// maxCommitLimit is GitHub's per_page ceiling.
const maxCommitLimit = 100

// ListRecentCommits returns the most recent commits on the default branch.
func (c *Client) ListRecentCommits(ctx context.Context, ref provider.RepoRef, limit int) ([]provider.Commit, error) {
	if limit <= 0 {
		limit = defaultCommitLimit
	}
	if limit > maxCommitLimit {
		limit = maxCommitLimit
	}
	var wire []wireCommit
	path := fmt.Sprintf("/repos/%s/%s/commits?per_page=%d", url.PathEscape(ref.Owner), url.PathEscape(ref.Name), limit)
	if err := c.get(ctx, path, &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Commit, 0, len(wire))
	for _, w := range wire {
		author := ""
		if w.Author != nil {
			author = w.Author.Login
		}
		if author == "" {
			author = w.Commit.Author.Name
		}
		out = append(out, provider.Commit{
			SHA:     w.SHA,
			Message: w.Commit.Message,
			Author:  author,
			Date:    w.Commit.Author.Date,
			HTMLURL: w.HTMLURL,
		})
	}
	return out, nil
}

// wireReadme is GitHub's contents payload for the README: the file body is
// base64 with embedded newlines.
type wireReadme struct {
	Content  string `json:"content"`
	Encoding string `json:"encoding"`
	HTMLURL  string `json:"html_url"`
}

// GetReadme returns the repository's README as raw markdown plus a link to it
// on github.com.
//
// A repository with NO README is a normal state, not a failure: GitHub answers
// 404 and this returns empty markdown with a nil error, so the detail view
// renders "No README" instead of a red error box in a perfectly healthy repo.
func (c *Client) GetReadme(ctx context.Context, ref provider.RepoRef) (string, string, error) {
	var wire wireReadme
	path := fmt.Sprintf("/repos/%s/%s/readme", url.PathEscape(ref.Owner), url.PathEscape(ref.Name))
	if err := c.get(ctx, path, &wire); err != nil {
		var se *provider.StatusError
		if errors.As(err, &se) && se.Code == http.StatusNotFound {
			return "", "", nil
		}
		return "", "", err
	}
	// Only base64 is documented for this endpoint, but a future "none"
	// encoding (GitHub uses it for over-size files) must not be decoded as if
	// it were base64.
	if wire.Encoding != "" && wire.Encoding != "base64" {
		return "", wire.HTMLURL, fmt.Errorf("github readme: unsupported encoding %q", wire.Encoding)
	}
	// The payload wraps at 60 chars; the newlines are not valid base64.
	raw := strings.NewReplacer("\n", "", "\r", "").Replace(wire.Content)
	decoded, err := base64.StdEncoding.DecodeString(raw)
	if err != nil {
		return "", wire.HTMLURL, fmt.Errorf("github readme: decode: %w", err)
	}
	return string(decoded), wire.HTMLURL, nil
}

// RepoPRs lists one repository's open pull requests.
func (c *Client) RepoPRs(ctx context.Context, ref provider.RepoRef) ([]provider.Item, error) {
	items, err := c.search(ctx, fmt.Sprintf("is:open is:pr repo:%s/%s", ref.Owner, ref.Name), "")
	if err != nil {
		return nil, err
	}
	sortItems(items)
	return items, nil
}

// RepoIssues lists one repository's open issues (pull requests excluded —
// GitHub counts a PR as an issue unless is:issue says otherwise).
func (c *Client) RepoIssues(ctx context.Context, ref provider.RepoRef) ([]provider.Item, error) {
	items, err := c.search(ctx, fmt.Sprintf("is:open is:issue repo:%s/%s", ref.Owner, ref.Name), "")
	if err != nil {
		return nil, err
	}
	sortItems(items)
	return items, nil
}
