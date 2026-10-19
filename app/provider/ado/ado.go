// Package ado is a read-only Azure DevOps REST client (api-version 7.1) that
// implements provider.Client. Config (org, project, PAT) binds at construction;
// the base URL is injectable for tests. ADO is org+project scoped: one client
// serves exactly one (org, project).
package ado

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"phantom-ink/provider"
)

const (
	defaultBase    = "https://dev.azure.com"
	apiVersion     = "7.1"
	requestTimeout = 10 * time.Second
	commitLimit    = 20
)

type Client struct {
	base    string
	org     string
	project string
	hc      *http.Client

	// authFn produces the Authorization header value per request: Basic for a
	// PAT (constant), or Bearer for az login (minted/cached, hence per-call).
	authFn func(ctx context.Context) (string, error)

	mu sync.Mutex
	// meID caches the authenticated user's GUID (from connectionData); ADO PR
	// creator/reviewer queries need it, work-item queries use @Me instead.
	meID string

	// identityKey scopes the PROCESS-WIDE identity cache. Clients are built
	// one per project for the same org and credential, so a per-client cache
	// still cost one connectionData call per project on every Code-panel open.
	// The key is a hash that includes the credential, so two profiles with
	// different PATs against the same org never share an answer.
	identityKey string
}

// identityTTL bounds how long an org's authenticated-user GUID is reused. The
// GUID is stable for the life of a credential; the TTL only exists so a
// revoked or rotated credential is not pinned for the life of the process.
const identityTTL = 10 * time.Minute

type identityEntry struct {
	id string
	at time.Time
}

var (
	identityMu    sync.Mutex
	identityCache = map[string]identityEntry{}
)

// identityKeyFor hashes the request scope AND the credential together. The
// credential is hashed, never stored or logged.
func identityKeyFor(base, org, credential string) string {
	sum := sha256.Sum256([]byte(base + "\x00" + org + "\x00" + credential))
	return hex.EncodeToString(sum[:])
}

// New returns a PAT-authenticated client (Basic auth, empty username + PAT).
func New(org, project, pat string) *Client {
	return NewWithBase(defaultBase, org, project, pat, nil)
}

func NewWithBase(base, org, project, pat string, hc *http.Client) *Client {
	c := newClient(base, org, project, hc)
	basic := "Basic " + base64.StdEncoding.EncodeToString([]byte(":"+pat))
	c.authFn = func(context.Context) (string, error) { return basic, nil }
	c.identityKey = identityKeyFor(base, org, "pat:"+pat)
	return c
}

// NewAzLogin returns a client that authenticates with a bearer token minted from
// an Azure CLI (`az login`) session instead of a PAT. azConfigDir selects WHICH
// session — az sessions are per-profile here (each workspace has its own
// AZURE_CONFIG_DIR), so the caller passes the target profile's dir; "" uses az's
// default location.
func NewAzLogin(org, project, azConfigDir string) *Client {
	return NewAzLoginWithBase(defaultBase, org, project, azConfigDir, nil)
}

func NewAzLoginWithBase(base, org, project, azConfigDir string, hc *http.Client) *Client {
	c := newClient(base, org, project, hc)
	src := &azTokenSource{azConfigDir: azConfigDir}
	c.authFn = src.header
	// The az session directory IS the credential scope here.
	c.identityKey = identityKeyFor(base, org, "az:"+azConfigDir)
	return c
}

func newClient(base, org, project string, hc *http.Client) *Client {
	if hc == nil {
		hc = &http.Client{Timeout: requestTimeout}
	}
	return &Client{base: strings.TrimRight(base, "/"), org: org, project: project, hc: hc}
}

func (c *Client) Kind() provider.Kind { return provider.KindADO }

// withVersion appends api-version to a path that may already carry a query.
func withVersion(path string) string {
	if strings.Contains(path, "?") {
		return path + "&api-version=" + apiVersion
	}
	return path + "?api-version=" + apiVersion
}

func (c *Client) get(ctx context.Context, path string, out any) error {
	return c.do(ctx, http.MethodGet, path, nil, out)
}

func (c *Client) do(ctx context.Context, method, path string, body []byte, out any) error {
	if c.org == "" || c.authFn == nil {
		return errors.New("ADO not configured (need ADO_ORG and either ADO_PAT or an az login)")
	}
	auth, err := c.authFn(ctx)
	if err != nil {
		return err
	}
	var rdr *bytes.Reader
	if body != nil {
		rdr = bytes.NewReader(body)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, rdr)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", auth)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	// ADO answers an unauthenticated/bad-PAT call with 203 Non-Authoritative
	// Information plus a sign-in HTML page, not 401 — surface it as
	// unauthorized so provider.IsUnauthorized sees it (matches
	// checkADOConnection's treatment of 203 in app_ado_token.go), instead of
	// letting a JSON decode of the HTML page fail opaquely.
	if resp.StatusCode == http.StatusNonAuthoritativeInfo {
		return &provider.StatusError{Code: http.StatusUnauthorized, Status: resp.Status, Path: path}
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return &provider.StatusError{Code: resp.StatusCode, Status: resp.Status, Path: path}
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// orgPath / projPath build the two scopes. Project-scoped resources sit under
// /{org}/{project}; identity and work-item hydrate sit under /{org}.
// Path segments are URL-encoded: ADO project names routinely contain spaces
// (e.g. "LAKEVIEW ENTERPRISE AUTOMATION"), which would otherwise break the path.
func (c *Client) orgPath(p string) string {
	return "/" + url.PathEscape(c.org) + p
}
func (c *Client) projPath(p string) string {
	return "/" + url.PathEscape(c.org) + "/" + url.PathEscape(c.project) + p
}

// me resolves the authenticated user's GUID, cached per client and then
// process-wide per (base, org, credential).
//
// Without the shared layer this was one connectionData call per ADO project
// per Code-panel open -- 30 projects meant 30 identical identity lookups for
// the same user.
func (c *Client) me(ctx context.Context) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.meID != "" {
		return c.meID, nil
	}
	if c.identityKey != "" {
		identityMu.Lock()
		e, ok := identityCache[c.identityKey]
		identityMu.Unlock()
		if ok && e.id != "" && time.Since(e.at) < identityTTL {
			c.meID = e.id
			return c.meID, nil
		}
	}
	var cd struct {
		AuthenticatedUser struct {
			ID string `json:"id"`
		} `json:"authenticatedUser"`
	}
	// connectionData is a PREVIEW-only resource: it rejects a plain "7.1"
	// api-version with 400 and demands the -preview flag. (Every other endpoint
	// we call is GA at 7.1.)
	if err := c.get(ctx, c.orgPath("/_apis/connectionData")+"?api-version=7.1-preview.1", &cd); err != nil {
		return "", err
	}
	c.meID = cd.AuthenticatedUser.ID
	if c.identityKey != "" && c.meID != "" {
		identityMu.Lock()
		identityCache[c.identityKey] = identityEntry{id: c.meID, at: time.Now()}
		identityMu.Unlock()
	}
	return c.meID, nil
}

// --- Projects ---------------------------------------------------------------

// ListProjects returns the names of every project in the org. Used to
// auto-discover projects when the profile lists none explicitly. Org-level, so
// it works on a client constructed with an empty project.
func (c *Client) ListProjects(ctx context.Context) ([]string, error) {
	var wire struct {
		Value []struct {
			Name string `json:"name"`
		} `json:"value"`
	}
	// $top well above the ~hundreds an org realistically has; projects is GA at 7.1.
	if err := c.get(ctx, withVersion(c.orgPath("/_apis/projects")+"?$top=1000"), &wire); err != nil {
		return nil, err
	}
	out := make([]string, 0, len(wire.Value))
	for _, p := range wire.Value {
		if p.Name != "" {
			out = append(out, p.Name)
		}
	}
	return out, nil
}

// --- Repos ------------------------------------------------------------------

type wireRepo struct {
	ID            string `json:"id"`
	Name          string `json:"name"`
	DefaultBranch string `json:"defaultBranch"`
	WebURL        string `json:"webUrl"`
	RemoteURL     string `json:"remoteUrl"`
}

func (c *Client) ListRepos(ctx context.Context) ([]provider.Repo, error) {
	var wire struct {
		Value []wireRepo `json:"value"`
	}
	if err := c.get(ctx, withVersion(c.projPath("/_apis/git/repositories")), &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Repo, 0, len(wire.Value))
	for _, w := range wire.Value {
		clone := w.RemoteURL
		if clone == "" {
			clone = c.cloneURLFor(w.Name)
		}
		out = append(out, provider.Repo{
			Provider:      provider.KindADO,
			Owner:         c.project,
			Name:          w.Name,
			FullName:      c.project + "/" + w.Name,
			HTMLURL:       w.WebURL,
			CloneURL:      clone,
			DefaultBranch: strings.TrimPrefix(w.DefaultBranch, "refs/heads/"),
			ID:            w.ID,
		})
	}
	return out, nil
}

func (c *Client) cloneURLFor(repo string) string {
	return fmt.Sprintf("%s/%s/%s/_git/%s", c.base, url.PathEscape(c.org), url.PathEscape(c.project), url.PathEscape(repo))
}

// --- Pull requests ----------------------------------------------------------

type wirePR struct {
	PullRequestID int    `json:"pullRequestId"`
	Title         string `json:"title"`
	Status        string `json:"status"`
	IsDraft       bool   `json:"isDraft"`
	CreationDate  string `json:"creationDate"`
	CreatedBy     struct {
		DisplayName string `json:"displayName"`
	} `json:"createdBy"`
	Repository struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	} `json:"repository"`
}

func (c *Client) prURL(repo string, id int) string {
	return fmt.Sprintf("%s/%s/%s/_git/%s/pullrequest/%d", c.base, url.PathEscape(c.org), url.PathEscape(c.project), url.PathEscape(repo), id)
}

func (c *Client) prItem(w wirePR, reason string) provider.Item {
	return provider.Item{
		Provider:      provider.KindADO,
		RepoFullName:  c.project + "/" + w.Repository.Name,
		Number:        w.PullRequestID,
		Title:         w.Title,
		State:         w.Status,
		HTMLURL:       c.prURL(w.Repository.Name, w.PullRequestID),
		UpdatedAt:     w.CreationDate,
		User:          w.CreatedBy.DisplayName,
		Draft:         w.IsDraft,
		IsPullRequest: true,
		Reason:        reason,
		RepoID:        w.Repository.ID,
	}
}

func (c *Client) searchPRs(ctx context.Context, criteria url.Values) ([]wirePR, error) {
	criteria.Set("searchCriteria.status", "active")
	path := c.projPath("/_apis/git/pullrequests") + "?" + criteria.Encode()
	var wire struct {
		Value []wirePR `json:"value"`
	}
	if err := c.get(ctx, withVersion(path), &wire); err != nil {
		return nil, err
	}
	return wire.Value, nil
}

// SearchMyPRs unions PRs I created and PRs where I'm a reviewer, deduped by
// pullRequestId, reasons merged.
func (c *Client) SearchMyPRs(ctx context.Context) ([]provider.Item, error) {
	id, err := c.me(ctx)
	if err != nil {
		return nil, err
	}
	authored, err := c.searchPRs(ctx, url.Values{"searchCriteria.creatorId": {id}})
	if err != nil {
		return nil, err
	}
	reviews, err := c.searchPRs(ctx, url.Values{"searchCriteria.reviewerId": {id}})
	if err != nil {
		return nil, err
	}
	byID := map[int]int{} // pullRequestId -> index in out
	out := make([]provider.Item, 0, len(authored)+len(reviews))
	add := func(w wirePR, reason string) {
		if i, seen := byID[w.PullRequestID]; seen {
			if !strings.Contains(out[i].Reason, reason) {
				out[i].Reason += ", " + reason
			}
			return
		}
		byID[w.PullRequestID] = len(out)
		out = append(out, c.prItem(w, reason))
	}
	for _, w := range authored {
		add(w, provider.ReasonAuthored)
	}
	for _, w := range reviews {
		add(w, provider.ReasonReviewRequested)
	}
	sortItems(out)
	return out, nil
}

// --- Work items -------------------------------------------------------------

func (c *Client) ListAssignedWork(ctx context.Context) ([]provider.Item, error) {
	wiql := `SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = @Me AND [System.State] <> 'Closed' ORDER BY [System.ChangedDate] DESC`
	body, _ := json.Marshal(map[string]string{"query": wiql})
	var idsResp struct {
		WorkItems []struct {
			ID int `json:"id"`
		} `json:"workItems"`
	}
	if err := c.do(ctx, http.MethodPost, withVersion(c.projPath("/_apis/wit/wiql")), body, &idsResp); err != nil {
		return nil, err
	}
	if len(idsResp.WorkItems) == 0 {
		return nil, nil
	}
	ids := make([]string, 0, len(idsResp.WorkItems))
	for _, w := range idsResp.WorkItems {
		ids = append(ids, fmt.Sprintf("%d", w.ID))
	}
	q := url.Values{
		"ids":    {strings.Join(ids, ",")},
		"fields": {"System.Title,System.State,System.WorkItemType,System.ChangedDate"},
	}
	var wi struct {
		Value []struct {
			ID     int `json:"id"`
			Fields struct {
				Title   string `json:"System.Title"`
				State   string `json:"System.State"`
				Type    string `json:"System.WorkItemType"`
				Changed string `json:"System.ChangedDate"`
			} `json:"fields"`
		} `json:"value"`
	}
	if err := c.get(ctx, withVersion(c.orgPath("/_apis/wit/workitems")+"?"+q.Encode()), &wi); err != nil {
		return nil, err
	}
	out := make([]provider.Item, 0, len(wi.Value))
	for _, w := range wi.Value {
		out = append(out, provider.Item{
			Provider:      provider.KindADO,
			RepoFullName:  c.project,
			Number:        w.ID,
			Title:         w.Fields.Title,
			State:         w.Fields.State,
			HTMLURL:       fmt.Sprintf("%s/%s/%s/_workitems/edit/%d", c.base, url.PathEscape(c.org), url.PathEscape(c.project), w.ID),
			UpdatedAt:     w.Fields.Changed,
			IsPullRequest: false,
			Reason:        provider.ReasonAssigned,
		})
	}
	sortItems(out)
	return out, nil
}

// --- Repo detail ------------------------------------------------------------

func (c *Client) repoBase(ref provider.RepoRef) string {
	return c.projPath("/_apis/git/repositories/" + url.PathEscape(ref.ID))
}

func (c *Client) ListBranches(ctx context.Context, ref provider.RepoRef) ([]provider.Branch, error) {
	var wire struct {
		Value []struct {
			Name     string `json:"name"`
			ObjectID string `json:"objectId"`
		} `json:"value"`
	}
	if err := c.get(ctx, withVersion(c.repoBase(ref)+"/refs?filter=heads/"), &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Branch, 0, len(wire.Value))
	for _, w := range wire.Value {
		out = append(out, provider.Branch{Name: strings.TrimPrefix(w.Name, "refs/heads/"), SHA: w.ObjectID})
	}
	return out, nil
}

func (c *Client) ListRecentCommits(ctx context.Context, ref provider.RepoRef, limit int) ([]provider.Commit, error) {
	if limit <= 0 {
		limit = commitLimit
	}
	path := fmt.Sprintf("%s/commits?searchCriteria.$top=%d", c.repoBase(ref), limit)
	var wire struct {
		Value []struct {
			CommitID  string `json:"commitId"`
			Comment   string `json:"comment"`
			RemoteURL string `json:"remoteUrl"`
			Author    struct {
				Name string `json:"name"`
				Date string `json:"date"`
			} `json:"author"`
		} `json:"value"`
	}
	if err := c.get(ctx, withVersion(path), &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Commit, 0, len(wire.Value))
	for _, w := range wire.Value {
		out = append(out, provider.Commit{
			SHA:     w.CommitID,
			Message: w.Comment,
			Author:  w.Author.Name,
			Date:    w.Author.Date,
			HTMLURL: w.RemoteURL,
		})
	}
	return out, nil
}

func (c *Client) GetReadme(ctx context.Context, ref provider.RepoRef) (string, string, error) {
	path := c.repoBase(ref) + "/items?path=/README.md&includeContent=true"
	var wire struct {
		Content string `json:"content"`
	}
	if err := c.get(ctx, withVersion(path), &wire); err != nil {
		var se *provider.StatusError
		if errors.As(err, &se) && se.Code == http.StatusNotFound {
			return "", "", nil
		}
		return "", "", err
	}
	htmlURL := fmt.Sprintf("%s/%s/%s/_git/%s?path=/README.md", c.base, url.PathEscape(c.org), url.PathEscape(c.project), url.PathEscape(ref.Name))
	return wire.Content, htmlURL, nil
}

// RepoPRs lists one repository's active PRs (repo-scoped).
func (c *Client) RepoPRs(ctx context.Context, ref provider.RepoRef) ([]provider.Item, error) {
	path := c.repoBase(ref) + "/pullrequests?searchCriteria.status=active"
	var wire struct {
		Value []wirePR `json:"value"`
	}
	if err := c.get(ctx, withVersion(path), &wire); err != nil {
		return nil, err
	}
	out := make([]provider.Item, 0, len(wire.Value))
	for _, w := range wire.Value {
		out = append(out, c.prItem(w, ""))
	}
	sortItems(out)
	return out, nil
}

// RepoIssues is empty in v1: ADO work items are project-scoped, not repo-scoped,
// so they surface in the overview's assigned-work section instead.
func (c *Client) RepoIssues(ctx context.Context, ref provider.RepoRef) ([]provider.Item, error) {
	return nil, nil
}

// --- Clone URL --------------------------------------------------------------

// NormalizeCloneURL turns any ADO reference into an https _git clone URL,
// handling dev.azure.com (with or without a user@ prefix) and legacy
// {org}.visualstudio.com. Returns "" when it can't parse a full
// {org}/{project}/_git/{repo}.
func (c *Client) NormalizeCloneURL(repoURL string) string {
	return provider.NormalizeADOCloneURL(repoURL)
}

// sortItems orders rows most-recently-updated first (RFC3339 string compare).
func sortItems(rows []provider.Item) {
	for i := 1; i < len(rows); i++ {
		for j := i; j > 0 && rows[j].UpdatedAt > rows[j-1].UpdatedAt; j-- {
			rows[j], rows[j-1] = rows[j-1], rows[j]
		}
	}
}

// --- Delivery metrics (DORA) ------------------------------------------------

// mergedPRsTop caps how many completed PRs one call pulls back. ADO returns
// completed PRs newest-first, and the dashboard's widest window is 90 days, so
// a page this size covers a very busy repo without paging.
const mergedPRsTop = 250

// wireCompletedPR is the subset of a completed pull request the delivery
// metrics need. It is separate from wirePR because that one models an ACTIVE
// PR: it has no close date, no merge commit, and no target branch, and adding
// them there would put empty fields on every open-PR row in the Code panel.
type wireCompletedPR struct {
	PullRequestID int    `json:"pullRequestId"`
	Title         string `json:"title"`
	Status        string `json:"status"`
	CreationDate  string `json:"creationDate"`
	ClosedDate    string `json:"closedDate"`
	TargetRefName string `json:"targetRefName"`
	CreatedBy     struct {
		UniqueName  string `json:"uniqueName"`
		DisplayName string `json:"displayName"`
	} `json:"createdBy"`
	LastMergeCommit struct {
		CommitID string `json:"commitId"`
	} `json:"lastMergeCommit"`
	Repository struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	} `json:"repository"`
}

// ListMergedPRs returns the repo's completed pull requests closed at or after
// since — the ADO half of the DORA deploy signal.
//
// targetRefName is stripped to a bare branch name and reported as BaseRef but
// NOT filtered on: the default branch lives on provider.Repo, so the caller
// decides which merges were deployments. ListRepos strips refs/heads/ from
// DefaultBranch the same way, so the two compare directly.
func (c *Client) ListMergedPRs(ctx context.Context, ref provider.RepoRef, since time.Time) ([]provider.MergedPR, error) {
	q := url.Values{
		"searchCriteria.status": {"completed"},
		"$top":                  {fmt.Sprint(mergedPRsTop)},
	}
	path := c.repoBase(ref) + "/pullrequests?" + q.Encode()
	var wire struct {
		Value []wireCompletedPR `json:"value"`
	}
	if err := c.get(ctx, withVersion(path), &wire); err != nil {
		return nil, err
	}

	fallbackFullName := ref.Owner + "/" + ref.Name
	out := make([]provider.MergedPR, 0, len(wire.Value))
	for _, w := range wire.Value {
		// Three ways a row is not a deployment: it never completed, it has no
		// close date to time the deploy by, or it completed with no merge
		// commit (cherry-picked elsewhere). None of them is an error.
		if w.Status != "completed" || w.ClosedDate == "" || w.LastMergeCommit.CommitID == "" {
			continue
		}
		if !isAtOrAfter(w.ClosedDate, since) {
			continue
		}
		full := fallbackFullName
		if w.Repository.Name != "" {
			full = c.project + "/" + w.Repository.Name
		}
		author := w.CreatedBy.UniqueName
		if author == "" {
			author = w.CreatedBy.DisplayName
		}
		out = append(out, provider.MergedPR{
			Provider:     provider.KindADO,
			RepoFullName: full,
			Number:       w.PullRequestID,
			Title:        w.Title,
			Author:       author,
			CreatedAt:    w.CreationDate,
			MergedAt:     w.ClosedDate,
			MergeSHA:     w.LastMergeCommit.CommitID,
			BaseRef:      strings.TrimPrefix(w.TargetRefName, "refs/heads/"),
			HTMLURL:      c.prURL(w.Repository.Name, w.PullRequestID),
		})
	}
	return out, nil
}

// isAtOrAfter reports whether an RFC3339 timestamp is at or after cutoff. An
// UNPARSEABLE timestamp reports true, keeping the row so a format surprise
// surfaces as data to inspect rather than as a silently shorter history. A zero
// cutoff (never synced) admits everything.
func isAtOrAfter(ts string, cutoff time.Time) bool {
	return provider.IsAtOrAfter(ts, cutoff)
}

var _ provider.Client = (*Client)(nil)
