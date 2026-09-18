// Package githubclient is a tiny READ-ONLY GitHub REST client for the Code
// panel's launchpad view: repositories, open PRs, assigned issues, and
// notifications for whoever the supplied token belongs to.
//
// It is deliberately not a general GitHub SDK. Every method takes the token
// per-call (the Code panel resolves it from the ACTIVE profile's gateway env on
// each request, so nothing is cached across profiles) and returns small structs
// holding only the fields the UI renders. The base URL is injectable so tests
// run against an httptest.Server — the same shape as app/app_github_token.go.
package githubclient

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// DefaultBase is the public GitHub REST endpoint.
const DefaultBase = "https://api.github.com"

// requestTimeout caps a single GitHub call. The panel fans five of these out
// concurrently, so a hung endpoint must not hold the page open.
const requestTimeout = 10 * time.Second

// Reasons a PR or issue made it onto the "needs attention" list. Carried on
// every row so the UI can say WHY it is listed rather than showing a flat pile.
const (
	ReasonAuthored        = "authored"
	ReasonReviewRequested = "review-requested"
	ReasonAssigned        = "assigned"
)

// Client talks to one GitHub REST base.
type Client struct {
	base string
	hc   *http.Client
}

// New returns a client against the public GitHub API.
func New() *Client {
	return NewWithBase(DefaultBase, &http.Client{Timeout: requestTimeout})
}

// NewWithBase returns a client against an arbitrary base — used by tests to
// point at an httptest.Server. A nil client falls back to a timed default.
func NewWithBase(base string, hc *http.Client) *Client {
	if hc == nil {
		hc = &http.Client{Timeout: requestTimeout}
	}
	return &Client{base: strings.TrimRight(base, "/"), hc: hc}
}

// StatusError is a non-2xx response from GitHub. It keeps the status code so
// callers can tell "your token is bad" (401) from "GitHub is having a day"
// (5xx) — the Code panel renders a reconnect banner for the former and a
// per-section error for the latter.
type StatusError struct {
	Code   int
	Status string
	Path   string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("github %s: %s", e.Path, e.Status)
}

// IsUnauthorized reports whether err is GitHub rejecting the credential.
func IsUnauthorized(err error) bool {
	var se *StatusError
	if errors.As(err, &se) {
		return se.Code == http.StatusUnauthorized
	}
	return false
}

// errNoToken is returned before any network call when the profile has no
// GITHUB_TOKEN. Hitting GitHub unauthenticated would return a *different*
// account's public view, which would be a cross-profile leak of sorts.
var errNoToken = errors.New("no GITHUB_TOKEN for this profile")

// get issues one authenticated GET and decodes the JSON body into out.
func (c *Client) get(ctx context.Context, token, path string, out any) error {
	if token == "" {
		return errNoToken
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+path, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return &StatusError{Code: resp.StatusCode, Status: resp.Status, Path: path}
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// --- Repositories -----------------------------------------------------------

// Repo is one repository as the launchpad lists it.
type Repo struct {
	Owner         string `json:"owner"`
	Name          string `json:"name"`
	FullName      string `json:"full_name"`
	Description   string `json:"description"`
	HTMLURL       string `json:"html_url"`
	CloneURL      string `json:"clone_url"`
	DefaultBranch string `json:"default_branch"`
	PushedAt      string `json:"pushed_at"`
	Stars         int    `json:"stars"`
	OpenIssues    int    `json:"open_issues"`
}

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
func (c *Client) ListRepos(ctx context.Context, token string) ([]Repo, error) {
	q := url.Values{
		"sort":        {"pushed"},
		"per_page":    {"50"},
		"affiliation": {"owner,collaborator,organization_member"},
	}
	var wire []wireRepo
	if err := c.get(ctx, token, "/user/repos?"+q.Encode(), &wire); err != nil {
		return nil, err
	}
	out := make([]Repo, 0, len(wire))
	for _, w := range wire {
		out = append(out, Repo{
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

// Issue is one PR or issue row. GitHub's search API returns both through the
// same shape; IsPullRequest distinguishes them.
type Issue struct {
	RepoFullName  string `json:"repo_full_name"`
	Number        int    `json:"number"`
	Title         string `json:"title"`
	State         string `json:"state"`
	HTMLURL       string `json:"html_url"`
	UpdatedAt     string `json:"updated_at"`
	User          string `json:"user"`
	Draft         bool   `json:"draft"`
	IsPullRequest bool   `json:"is_pull_request"`
	// Reason is why this row is on the list (authored / review-requested /
	// assigned), not a GitHub field.
	Reason string `json:"reason"`
}

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

// SearchPRsAuthored lists the token owner's own open pull requests.
func (c *Client) SearchPRsAuthored(ctx context.Context, token string) ([]Issue, error) {
	return c.search(ctx, token, "is:open is:pr author:@me", ReasonAuthored)
}

// SearchPRsReviewRequested lists open PRs waiting on the owner's review.
func (c *Client) SearchPRsReviewRequested(ctx context.Context, token string) ([]Issue, error) {
	return c.search(ctx, token, "is:open is:pr review-requested:@me", ReasonReviewRequested)
}

// SearchIssuesAssigned lists open issues assigned to the owner.
func (c *Client) SearchIssuesAssigned(ctx context.Context, token string) ([]Issue, error) {
	return c.search(ctx, token, "is:open is:issue assignee:@me", ReasonAssigned)
}

func (c *Client) search(ctx context.Context, token, q, reason string) ([]Issue, error) {
	var wire wireSearch
	path := "/search/issues?" + url.Values{"q": {q}, "per_page": {"50"}}.Encode()
	if err := c.get(ctx, token, path, &wire); err != nil {
		return nil, err
	}
	out := make([]Issue, 0, len(wire.Items))
	for _, it := range wire.Items {
		out = append(out, Issue{
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
		})
	}
	return out, nil
}

// --- Notifications ----------------------------------------------------------

// Notification is one inbox row.
type Notification struct {
	ID           string `json:"id"`
	RepoFullName string `json:"repo_full_name"`
	SubjectTitle string `json:"subject_title"`
	SubjectType  string `json:"subject_type"`
	Reason       string `json:"reason"`
	UpdatedAt    string `json:"updated_at"`
	// URL is a github.com link (the API's subject.url is not browsable).
	URL string `json:"url"`
}

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
func (c *Client) ListNotifications(ctx context.Context, token string) ([]Notification, error) {
	var wire []wireNotification
	if err := c.get(ctx, token, "/notifications", &wire); err != nil {
		return nil, err
	}
	out := make([]Notification, 0, len(wire))
	for _, w := range wire {
		out = append(out, Notification{
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

// --- Repository detail ------------------------------------------------------
//
// The five reads behind the Code panel's single-repo view. Same shape as the
// launchpad methods: token per call, small structs, no caching. Every one is a
// plain GET — nothing here can mutate a repository.

// Branch is one branch head.
type Branch struct {
	Name string `json:"name"`
	SHA  string `json:"sha"`
}

type wireBranch struct {
	Name   string `json:"name"`
	Commit struct {
		SHA string `json:"sha"`
	} `json:"commit"`
}

// ListBranches returns up to 50 branches of one repository.
func (c *Client) ListBranches(ctx context.Context, token, owner, repo string) ([]Branch, error) {
	var wire []wireBranch
	path := fmt.Sprintf("/repos/%s/%s/branches?per_page=50", url.PathEscape(owner), url.PathEscape(repo))
	if err := c.get(ctx, token, path, &wire); err != nil {
		return nil, err
	}
	out := make([]Branch, 0, len(wire))
	for _, w := range wire {
		out = append(out, Branch{Name: w.Name, SHA: w.Commit.SHA})
	}
	return out, nil
}

// Commit is one entry from a repository's commit log.
//
// Message is the FULL commit message; the panel renders only its first line.
// Truncating here would throw away the body for every future caller to save a
// few bytes on the wire.
type Commit struct {
	SHA     string `json:"sha"`
	Message string `json:"message"`
	// Author is the GitHub login when the commit is attributed to an account,
	// falling back to the git author name when it isn't (an unlinked email, a
	// bot, a rewritten history). One of the two is nearly always present.
	Author  string `json:"author"`
	Date    string `json:"date"`
	HTMLURL string `json:"html_url"`
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
func (c *Client) ListRecentCommits(ctx context.Context, token, owner, repo string, limit int) ([]Commit, error) {
	if limit <= 0 {
		limit = defaultCommitLimit
	}
	if limit > maxCommitLimit {
		limit = maxCommitLimit
	}
	var wire []wireCommit
	path := fmt.Sprintf("/repos/%s/%s/commits?per_page=%d", url.PathEscape(owner), url.PathEscape(repo), limit)
	if err := c.get(ctx, token, path, &wire); err != nil {
		return nil, err
	}
	out := make([]Commit, 0, len(wire))
	for _, w := range wire {
		author := ""
		if w.Author != nil {
			author = w.Author.Login
		}
		if author == "" {
			author = w.Commit.Author.Name
		}
		out = append(out, Commit{
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
func (c *Client) GetReadme(ctx context.Context, token, owner, repo string) (string, string, error) {
	var wire wireReadme
	path := fmt.Sprintf("/repos/%s/%s/readme", url.PathEscape(owner), url.PathEscape(repo))
	if err := c.get(ctx, token, path, &wire); err != nil {
		var se *StatusError
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

// SearchPRsByRepo lists one repository's open pull requests.
func (c *Client) SearchPRsByRepo(ctx context.Context, token, owner, repo string) ([]Issue, error) {
	return c.search(ctx, token, fmt.Sprintf("is:open is:pr repo:%s/%s", owner, repo), "")
}

// SearchIssuesByRepo lists one repository's open issues (pull requests
// excluded — GitHub counts a PR as an issue unless is:issue says otherwise).
func (c *Client) SearchIssuesByRepo(ctx context.Context, token, owner, repo string) ([]Issue, error) {
	return c.search(ctx, token, fmt.Sprintf("is:open is:issue repo:%s/%s", owner, repo), "")
}
