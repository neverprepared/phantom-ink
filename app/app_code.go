package main

// The Code page: a profile-scoped, single-pane view of the active profile's
// configured git providers (GitHub, Azure DevOps), plus a one-click hand-off
// from a repo/PR/issue straight into a fleet agent task. Read-only; nothing
// here is persisted.
//
// Everything is host-side and profile-scoped: each provider's credential is
// the profile's EXISTING curated env (GITHUB_TOKEN, ADO_ORG/ADO_PROJECT/
// ADO_PAT), read fresh out of the gateway env store on every call. No new
// secret, no new credential storage, no cache — so a profile can only ever
// see its own content, and switching profiles cannot serve stale rows from
// the previous one.

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"phantom-ink/brainbox"
	"phantom-ink/provider"
	"phantom-ink/provider/ado"
	"phantom-ink/provider/github"
)

// CodeOverview is one page-load of the Code panel, merged across every
// provider the profile has configured.
//
// Section errors are per-section STRINGS rather than one returned error on
// purpose: a 401 on one provider's notifications (a scope separate from repo
// scope on fine-grained PATs) must not blank the repositories list, and one
// provider failing must not blank another provider's results. The panel
// renders each section's error inside that section's card.
type CodeOverview struct {
	// Profile echoes back which profile these rows belong to, so a response
	// that lands after a profile switch can be discarded by the panel.
	Profile string `json:"profile"`
	// TokenMissing is set when the profile has NO provider configured at all
	// (no GITHUB_TOKEN and no complete ADO_ORG/ADO_PROJECT/ADO_PAT). It is
	// NOT an error: the panel shows a "connect a provider" banner pointing at
	// the Profiles panel instead of an error toast.
	TokenMissing bool `json:"token_missing"`
	// TokenInvalid is set when ANY configured provider rejected its
	// credential (401).
	TokenInvalid bool `json:"token_invalid"`

	Repos         []provider.Repo         `json:"repos"`
	PullRequests  []provider.Item         `json:"pull_requests"`
	Issues        []provider.Item         `json:"issues"`
	Notifications []provider.Notification `json:"notifications"`

	ReposError         string `json:"repos_error"`
	PullRequestsError  string `json:"pull_requests_error"`
	IssuesError        string `json:"issues_error"`
	NotificationsError string `json:"notifications_error"`
}

// providersFor builds the set of providers a profile has configured in its
// gateway env. Presence is selection: GitHub needs GITHUB_TOKEN; ADO needs all
// of ADO_ORG/ADO_PROJECT/ADO_PAT. The bool reports whether ANY provider is
// configured (drives the "connect something" banner).
func (a *App) providersFor(profile string) ([]provider.Client, bool, error) {
	env, err := a.GetGatewayEnv(profile)
	if err != nil {
		return nil, false, err
	}
	clients := buildProviders(env, a.profileAzureConfigDir(profile))
	return clients, len(clients) > 0, nil
}

// buildProviders maps a profile's gateway env to the set of configured provider
// clients. Pure (no I/O) so the enablement rules are unit-testable. GitHub is
// enabled by GITHUB_TOKEN; ADO by ADO_ORG plus one or more comma-separated
// projects in ADO_PROJECT — one client per project, so the fan-out aggregates
// repos/PRs/work-items across every listed project. Auth is ADO_PAT when
// present, otherwise the profile's az login session (azConfigDir).
func buildProviders(env map[string]string, azConfigDir string) []provider.Client {
	var clients []provider.Client
	if tok := strings.TrimSpace(env["GITHUB_TOKEN"]); tok != "" {
		clients = append(clients, github.New(tok))
	}
	org := strings.TrimSpace(env["ADO_ORG"])
	pat := strings.TrimSpace(env["ADO_PAT"])
	if org != "" {
		for _, p := range splitProjects(env["ADO_PROJECT"]) {
			clients = append(clients, adoClientFor(org, p, pat, azConfigDir))
		}
	}
	return clients
}

// splitProjects parses a comma-separated ADO_PROJECT list, trimming blanks. A
// single project name (no commas) yields a one-element list.
func splitProjects(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// adoClientFor builds a single-project ADO client: PAT auth when present, else
// az login scoped to the profile's az config dir.
func adoClientFor(org, project, pat, azConfigDir string) provider.Client {
	if pat != "" {
		return ado.New(org, project, pat)
	}
	return ado.NewAzLogin(org, project, azConfigDir)
}

// profileAzureConfigDir finds the AZURE_CONFIG_DIR for a profile's az session.
// az logins are per-profile here (each workspace points its own .azure at a
// chosen identity). Prefer the value the profile's env resolves; fall back to
// the conventional <workspace>/.azure when that directory exists. "" lets az use
// its default.
func (a *App) profileAzureConfigDir(profile string) string {
	for _, kv := range a.resolveProfileEnv(profile) {
		if v, ok := strings.CutPrefix(kv, "AZURE_CONFIG_DIR="); ok && strings.TrimSpace(v) != "" {
			return v
		}
	}
	if home := a.profileWorkspaceHome(profile); home != "" {
		cand := filepath.Join(home, ".azure")
		if fi, err := os.Stat(cand); err == nil && fi.IsDir() {
			return cand
		}
	}
	return ""
}

// CodeOverview fetches the active profile's launchpad across every configured
// provider (GitHub, ADO), merged. Bound to the UI; one call drives the panel.
func (a *App) CodeOverview(profile string) (CodeOverview, error) {
	clients, any, err := a.providersFor(profile)
	if err != nil {
		return CodeOverview{Profile: profile}, err
	}
	if !any {
		return CodeOverview{Profile: profile, TokenMissing: true}, nil
	}
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	return buildCodeOverview(ctx, clients, profile), nil
}

// buildCodeOverview fans each configured provider's reads out concurrently and
// folds them into one struct, recording failures per section (prefixed by the
// provider that produced them) instead of aborting the page. One provider's
// failure never blanks another provider's results.
func buildCodeOverview(ctx context.Context, clients []provider.Client, profile string) CodeOverview {
	out := CodeOverview{Profile: profile}

	var (
		mu         sync.Mutex // guards the section results below
		wg         sync.WaitGroup
		prs        []provider.Item
		work       []provider.Item
		notifs     []provider.Notification
		reposErrs  []string
		prsErrs    []string
		workErrs   []string
		notifsErrs []string
		auth401    bool
	)

	run := func(fn func()) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			fn()
		}()
	}

	for _, c := range clients {
		c := c
		run(func() {
			repos, err := c.ListRepos(ctx)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				if provider.IsUnauthorized(err) {
					auth401 = true
				}
				reposErrs = append(reposErrs, fmt.Sprintf("%s: %s", c.Kind(), err))
				return
			}
			out.Repos = append(out.Repos, repos...)
		})

		run(func() {
			items, err := c.SearchMyPRs(ctx)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				if provider.IsUnauthorized(err) {
					auth401 = true
				}
				prsErrs = append(prsErrs, fmt.Sprintf("%s: %s", c.Kind(), err))
				return
			}
			prs = append(prs, items...)
		})

		run(func() {
			items, err := c.ListAssignedWork(ctx)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				if provider.IsUnauthorized(err) {
					auth401 = true
				}
				workErrs = append(workErrs, fmt.Sprintf("%s: %s", c.Kind(), err))
				return
			}
			work = append(work, items...)
		})

		if notifier, ok := c.(provider.Notifier); ok {
			run(func() {
				ns, err := notifier.ListNotifications(ctx)
				mu.Lock()
				defer mu.Unlock()
				if err != nil {
					if provider.IsUnauthorized(err) {
						auth401 = true
					}
					notifsErrs = append(notifsErrs, fmt.Sprintf("%s: %s", c.Kind(), err))
					return
				}
				notifs = append(notifs, ns...)
			})
		}
	}

	wg.Wait()

	sortRepos(out.Repos)
	sortItems(prs)
	sortItems(work)

	out.PullRequests = prs
	out.Issues = work
	out.Notifications = notifs

	out.ReposError = strings.Join(reposErrs, "; ")
	out.PullRequestsError = strings.Join(prsErrs, "; ")
	out.IssuesError = strings.Join(workErrs, "; ")
	out.NotificationsError = strings.Join(notifsErrs, "; ")
	out.TokenInvalid = auth401
	return out
}

// sortItems orders rows most-recently-updated first. UpdatedAt is RFC3339, so
// a plain string compare is already chronological.
func sortItems(rows []provider.Item) {
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].UpdatedAt > rows[j].UpdatedAt })
}

// sortRepos orders repos most-recently-pushed first. PushedAt is RFC3339, so a
// plain string compare is already chronological.
func sortRepos(rows []provider.Repo) {
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].PushedAt > rows[j].PushedAt })
}

// --- Repository detail ------------------------------------------------------

// RepoDetailResult is one page-load of the Code panel's single-repo view.
//
// Same contract as CodeOverview: per-section error STRINGS rather than one
// returned error, because the five reads have genuinely independent failure
// modes (a fine-grained PAT with no issues scope must still render branches,
// commits, and the README).
type RepoDetailResult struct {
	// Profile / Owner / Repo echo the request back so a response that lands
	// after the operator navigated away can be discarded by the panel.
	Profile string `json:"profile"`
	// Provider echoes which provider this detail view was fetched from.
	Provider provider.Kind `json:"provider"`
	Owner    string        `json:"owner"`
	Repo     string        `json:"repo"`
	// DefaultBranch comes from the caller's already-loaded Repo row rather than
	// a sixth call — the overview fetched it seconds ago.
	DefaultBranch string `json:"default_branch"`

	// TokenMissing / TokenInvalid mirror CodeOverview so the panel reuses one
	// banner for both views.
	TokenMissing bool `json:"token_missing"`
	TokenInvalid bool `json:"token_invalid"`

	Branches []provider.Branch `json:"branches"`
	Commits  []provider.Commit `json:"commits"`
	PRs      []provider.Item   `json:"prs"`
	Issues   []provider.Item   `json:"issues"`
	// Readme is RAW markdown. Rendering (and sanitizing — a README is
	// untrusted repo content) happens in the frontend.
	Readme    string `json:"readme"`
	ReadmeURL string `json:"readme_url"`

	BranchesError string `json:"branches_error"`
	CommitsError  string `json:"commits_error"`
	ReadmeError   string `json:"readme_error"`
	PRsError      string `json:"prs_error"`
	IssuesError   string `json:"issues_error"`
}

// repoDetailCommitLimit is how far back the "recent commits" card reads. Enough
// to see the shape of the week without paging.
const repoDetailCommitLimit = 20

// RepoDetail fetches one repository's detail view. The RepoRef (carried from
// the overview row the operator clicked) names the provider AND the repo, so
// routing needs no lookup.
func (a *App) RepoDetail(profile string, ref provider.RepoRef) (RepoDetailResult, error) {
	base := RepoDetailResult{Profile: profile, Provider: ref.Provider, Owner: ref.Owner, Repo: ref.Name, DefaultBranch: ref.DefaultBranch}
	env, err := a.GetGatewayEnv(profile)
	if err != nil {
		return base, err
	}
	// Build the client that owns this ref. For ADO the project comes from
	// ref.Owner (each ADO repo row carries its own project), so a multi-project
	// profile routes to the right project rather than guessing the first client.
	var client provider.Client
	switch ref.Provider {
	case provider.KindGitHub:
		if tok := strings.TrimSpace(env["GITHUB_TOKEN"]); tok != "" {
			client = github.New(tok)
		}
	case provider.KindADO:
		if org := strings.TrimSpace(env["ADO_ORG"]); org != "" && strings.TrimSpace(ref.Owner) != "" {
			client = adoClientFor(org, ref.Owner, strings.TrimSpace(env["ADO_PAT"]), a.profileAzureConfigDir(profile))
		}
	}
	if client == nil {
		base.TokenMissing = true
		return base, nil
	}
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	return buildRepoDetail(ctx, client, profile, ref), nil
}

// buildRepoDetail fans the five reads out concurrently and folds them into one
// struct, recording failures per section (prefixed by the provider that
// produced them) instead of aborting the view.
func buildRepoDetail(ctx context.Context, c provider.Client, profile string, ref provider.RepoRef) RepoDetailResult {
	out := RepoDetailResult{Profile: profile, Provider: ref.Provider, Owner: ref.Owner, Repo: ref.Name, DefaultBranch: ref.DefaultBranch}

	var (
		mu      sync.Mutex // guards out and auth401
		wg      sync.WaitGroup
		auth401 bool
	)

	note := func(err error, dst *string) {
		mu.Lock()
		defer mu.Unlock()
		if provider.IsUnauthorized(err) {
			auth401 = true
		}
		*dst = fmt.Sprintf("%s: %s", c.Kind(), err)
	}

	run := func(fn func()) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			fn()
		}()
	}

	run(func() {
		bs, err := c.ListBranches(ctx, ref)
		if err != nil {
			note(err, &out.BranchesError)
			return
		}
		mu.Lock()
		out.Branches = bs
		mu.Unlock()
	})

	run(func() {
		cs, err := c.ListRecentCommits(ctx, ref, repoDetailCommitLimit)
		if err != nil {
			note(err, &out.CommitsError)
			return
		}
		mu.Lock()
		out.Commits = cs
		mu.Unlock()
	})

	run(func() {
		// A repo with no README returns empty markdown and a nil error — it is
		// not a failure, and must not paint a red box on a healthy repo.
		md, htmlURL, err := c.GetReadme(ctx, ref)
		if err != nil {
			note(err, &out.ReadmeError)
			return
		}
		mu.Lock()
		out.Readme, out.ReadmeURL = md, htmlURL
		mu.Unlock()
	})

	run(func() {
		prs, err := c.RepoPRs(ctx, ref)
		if err != nil {
			note(err, &out.PRsError)
			return
		}
		sortItems(prs)
		mu.Lock()
		out.PRs = prs
		mu.Unlock()
	})

	run(func() {
		issues, err := c.RepoIssues(ctx, ref)
		if err != nil {
			note(err, &out.IssuesError)
			return
		}
		sortItems(issues)
		mu.Lock()
		out.Issues = issues
		mu.Unlock()
	})

	wg.Wait()

	out.TokenInvalid = auth401
	return out
}

// --- Dispatch ---------------------------------------------------------------

// DispatchRepoRequest is the Code panel's "start work here" payload: the
// templated-but-edited prompt, the agent to run it, and the repo to run it in.
type DispatchRepoRequest struct {
	Profile     string `json:"profile"`
	RepoURL     string `json:"repo_url"`
	AgentName   string `json:"agent_name"`
	Description string `json:"description"`
}

// DispatchRepoTask hands one repo/PR/issue to a fleet agent through the
// EXISTING hub task-submit path. Fire-and-forget: it returns as soon as the hub
// has the task, matching the Jobs panel's UX.
func (a *App) DispatchRepoTask(req DispatchRepoRequest) (brainbox.Task, error) {
	return a.client.SubmitTask(brainbox.SubmitTaskRequest{
		Description:      req.Description,
		AgentName:        req.AgentName,
		RepoURL:          req.RepoURL,
		WorkspaceProfile: req.Profile,
	})
}

// --- Begin-work lanes -------------------------------------------------------
//
// Two ways to START work on a row, alongside ⚡ Dispatch (which hands the work
// to an autonomous fleet agent and walks away):
//
//   LOCAL     — clone the repo into the profile's own workspace and open a host
//               terminal running claude in it. The operator drives.
//   CONTAINER — an interactive brainbox session that clones the repo ITSELF
//               (GITHUB_TOKEN reaches the container through the profile env the
//               existing CreateSession already forwards).
//
// Both are profile-scoped: the destination directory comes from the profile's
// workspace home, and the session carries workspace_profile, so a lane opened
// under profile A can never land in profile B's tree or credentials.

// runGitClone shells out to the host's git. A package var, not a method, so a
// test can assert the clone-vs-open-existing BRANCH with a temp dir and no
// network. git's own stderr is returned verbatim: on a private repo the useful
// part of a failure ("Repository not found", "could not read Username") is in
// git's wording, not ours.
//
// Auth is deliberately the HOST's git credentials (the operator's `gh` /
// credential helper). The profile's GITHUB_TOKEN is NOT embedded in the URL or
// an http.extraHeader — either would persist the secret into the clone's
// remote/reflog, on disk, forever.
var runGitClone = func(cloneURL, dest string) error {
	cmd := exec.Command("git", "clone", cloneURL, dest)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		if msg := strings.TrimSpace(stderr.String()); msg != "" {
			return fmt.Errorf("git clone: %s", msg)
		}
		return fmt.Errorf("git clone: %w", err)
	}
	return nil
}

// isADOCloneURL reports whether a normalized clone URL points at Azure DevOps.
func isADOCloneURL(cloneURL string) bool {
	return strings.Contains(cloneURL, "dev.azure.com/") || strings.Contains(cloneURL, ".visualstudio.com/")
}

// runGitCloneAuth clones with an inline Authorization header (never persisted to
// the clone's config). Used for ADO, where host git has no credential. A package
// var so a test can assert the header-carrying branch without a network.
var runGitCloneAuth = func(cloneURL, dest, authHeader string) error {
	cmd := exec.Command("git", "-c", "http.extraheader=Authorization: "+authHeader, "clone", cloneURL, dest)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		if msg := strings.TrimSpace(stderr.String()); msg != "" {
			return fmt.Errorf("git clone: %s", msg)
		}
		return fmt.Errorf("git clone: %w", err)
	}
	return nil
}

// readProfileGatewayEnv resolves a profile's gateway env for the clone path. A
// package var (like runGitClone) so a test can supply an ADO_PAT without a live
// broker; production delegates to the real GetGatewayEnv.
var readProfileGatewayEnv = func(a *App, profile string) (map[string]string, error) {
	return a.GetGatewayEnv(profile)
}

// adoAzAuthHeader mints a one-shot ADO bearer header from the operator's az
// login session. A package var (like runGitClone) so the PAT-less clone and
// validation paths are testable without a real az binary.
var adoAzAuthHeader = ado.AzAuthHeader

// openTerminalAt opens a host terminal tab running claude in a directory. A var
// over the EXISTING opener (shared with OpenLocalSession) so tests don't drive
// AppleScript and there is exactly one implementation of "open a tab".
var openTerminalAt = openLocalSessionTab

// OpenRepoLocally clones a repo into the profile's workspace (once) and opens a
// host terminal running claude in it. Returns the checkout path.
//
// An EXISTING checkout is opened as-is — no clone, and deliberately no pull:
// this button must never touch a working tree the operator may have dirty. Any
// fetching is their call, in the terminal it just opened.
func (a *App) OpenRepoLocally(profile, repoURL string) (string, error) {
	prof, err := a.findProfile(profile)
	if err != nil {
		return "", err
	}
	if prof.WorkspaceHome == "" {
		return "", fmt.Errorf("profile %q has no workspace home", profile)
	}
	dest := deriveCloneDest(prof.WorkspaceHome, repoURL)
	if dest == "" {
		return "", fmt.Errorf("cannot derive a repository name from %q", repoURL)
	}

	if _, statErr := os.Stat(dest); statErr != nil {
		if !os.IsNotExist(statErr) {
			return "", statErr
		}
		cloneURL := normalizeCloneURL(repoURL)
		if cloneURL == "" {
			return "", fmt.Errorf("cannot derive a clone URL from %q", repoURL)
		}
		if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
			return "", err
		}
		if isADOCloneURL(cloneURL) {
			env, err := readProfileGatewayEnv(a, profile)
			if err != nil {
				return "", err
			}
			var auth string
			if pat := strings.TrimSpace(env["ADO_PAT"]); pat != "" {
				auth = "Basic " + base64.StdEncoding.EncodeToString([]byte(":"+pat))
			} else {
				// No PAT: mint a bearer from the profile's az login session.
				ctx := a.ctx
				if ctx == nil {
					ctx = context.Background()
				}
				h, azErr := adoAzAuthHeader(ctx, a.profileAzureConfigDir(profile))
				if azErr != nil {
					return "", fmt.Errorf("cloning %s needs an ADO_PAT or an az login: %w", cloneURL, azErr)
				}
				auth = h
			}
			if err := runGitCloneAuth(cloneURL, dest, auth); err != nil {
				return "", err
			}
		} else if err := runGitClone(cloneURL, dest); err != nil {
			return "", err
		}
	}

	if err := openTerminalAt(dest); err != nil {
		// The clone is on disk either way — say where, so the operator can open
		// it by hand if the terminal automation is what failed.
		return dest, fmt.Errorf("cloned to %s but could not open a terminal: %w", dest, err)
	}
	return dest, nil
}

// InteractiveSessionRequest is the container lane's payload. Task is the edited
// prompt from the modal; it already carries the repo URL and a "clone it,
// GITHUB_TOKEN is available" instruction, because a brainbox session has no
// repo field — the repo arrives through the seeded task.
type InteractiveSessionRequest struct {
	Profile string `json:"profile"`
	RepoURL string `json:"repo_url"`
	Task    string `json:"task"`
}

// LaunchInteractiveSession starts an interactive (tmux REPL) session seeded
// with the prompt, reusing CreateSession so the profile env forwarding and the
// PROFILE_ENV_KEY/image delivery it already does apply unchanged.
func (a *App) LaunchInteractiveSession(req InteractiveSessionRequest) (brainbox.SessionActionResponse, error) {
	prof, err := a.findProfile(req.Profile)
	if err != nil {
		return brainbox.SessionActionResponse{}, err
	}
	if prof.WorkspaceHome == "" {
		return brainbox.SessionActionResponse{}, fmt.Errorf("profile %q has no workspace home", req.Profile)
	}
	name := sessionNameFor(req.RepoURL)
	return a.CreateSession(brainbox.CreateSessionRequest{
		Name:             name,
		ExecMode:         "interactive",
		WorkspaceProfile: req.Profile,
		WorkspaceHome:    prof.WorkspaceHome,
		Task:             req.Task,
	})
}

// sessionNameFor builds a unique, container-safe session name for a repo. The
// suffix keeps a second session on the SAME repo from colliding with the first.
func sessionNameFor(repoURL string) string {
	name := sanitizeNameSegment(repoNameFromURL(repoURL))
	if name == "" {
		name = "repo"
	}
	return fmt.Sprintf("code-%s-%06x", name, time.Now().UnixNano()&0xffffff)
}

// sanitizeNameSegment reduces a repo name to lowercase [a-z0-9-], the shape
// docker and the nginx route names downstream will accept.
func sanitizeNameSegment(s string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(s) {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9':
			b.WriteRune(r)
		default:
			b.WriteRune('-')
		}
	}
	return strings.Trim(b.String(), "-")
}

// deriveCloneDest is where a repo lands for a profile: <workspaceHome>/code/<repo>.
// Returns "" when either input yields nothing usable, so the caller reports a
// clear error instead of cloning into a surprising path.
func deriveCloneDest(workspaceHome, repoURL string) string {
	name := repoNameFromURL(repoURL)
	if workspaceHome == "" || name == "" {
		return ""
	}
	return filepath.Join(workspaceHome, "code", name)
}

// repoNameFromURL is the last path segment without ".git". Path separators and
// dot-segments are rejected rather than sanitized — a URL that produces one is
// malformed, and quietly "fixing" it would pick a directory nobody asked for.
func repoNameFromURL(repoURL string) string {
	clone := normalizeCloneURL(repoURL)
	if clone == "" {
		return ""
	}
	name := strings.TrimSuffix(clone[strings.LastIndex(clone, "/")+1:], ".git")
	if name == "" || name == "." || name == ".." || strings.ContainsAny(name, `/\`) {
		return ""
	}
	return name
}

// normalizeADOCloneURL turns any Azure DevOps reference into an https _git
// clone URL, handling dev.azure.com (with or without a user@ prefix) and
// legacy {org}.visualstudio.com. Returns "" when it can't parse a full
// {org}/{project}/_git/{repo} — mirrors provider/ado.Client.NormalizeCloneURL
// so the main-package free function used by the local-clone lane agrees with
// the ado provider client on what counts as a valid ADO clone URL.
func normalizeADOCloneURL(repoURL string) string {
	s := strings.TrimSpace(repoURL)
	s = strings.TrimSuffix(s, "/")
	if s == "" {
		return ""
	}
	if i := strings.Index(s, "://"); i >= 0 {
		s = s[i+3:]
	}
	if at := strings.Index(s, "@"); at >= 0 {
		s = s[at+1:]
	}
	slash := strings.Index(s, "/")
	if slash < 0 {
		return "" // bare host, no path
	}
	host, rest := s[:slash], s[slash+1:]
	var org, tail string
	switch {
	case host == "dev.azure.com":
		i := strings.Index(rest, "/")
		if i < 0 {
			return ""
		}
		org, tail = rest[:i], rest[i+1:]
	case strings.HasSuffix(host, ".visualstudio.com"):
		org, tail = strings.TrimSuffix(host, ".visualstudio.com"), rest
	default:
		return ""
	}
	segs := strings.Split(tail, "/")
	if org == "" || len(segs) < 3 || segs[0] == "" || segs[1] != "_git" || segs[2] == "" {
		return ""
	}
	return "https://dev.azure.com/" + org + "/" + segs[0] + "/_git/" + segs[2]
}

// isADOCloneHost reports whether repoURL's host looks like Azure DevOps, so
// normalizeCloneURL knows to route it through normalizeADOCloneURL instead of
// the GitHub logic below.
func isADOCloneHost(repoURL string) bool {
	s := strings.TrimSpace(repoURL)
	if i := strings.Index(s, "://"); i >= 0 {
		s = s[i+3:]
	}
	if at := strings.Index(s, "@"); at >= 0 {
		s = s[at+1:]
	}
	host := s
	if slash := strings.Index(host, "/"); slash >= 0 {
		host = host[:slash]
	}
	return host == "dev.azure.com" || strings.HasSuffix(host, ".visualstudio.com")
}

// normalizeCloneURL turns any GitHub or Azure DevOps reference to a repo into
// an https clone URL, so the same helper serves a repo row's clone_url, a
// search hit's html_url, an api.github.com URL, and an ssh remote pasted by
// hand:
//
//	git@github.com:o/r.git              → https://github.com/o/r.git
//	ssh://git@github.com/o/r            → https://github.com/o/r.git
//	https://github.com/o/r              → https://github.com/o/r.git
//	https://api.github.com/repos/o/r    → https://github.com/o/r.git
//	https://dev.azure.com/o/p/_git/r    → https://dev.azure.com/o/p/_git/r
//	https://o.visualstudio.com/p/_git/r → https://dev.azure.com/o/p/_git/r
//
// ADO hosts are detected and normalized BEFORE the GitHub logic below; GitHub
// URLs fall through unchanged. The host is otherwise PRESERVED (only
// api.github.com is rewritten to github.com) so a GitHub Enterprise remote
// still clones from its own host. Returns "" when no owner/repo pair (or,
// for ADO, no org/project/_git/repo) can be read out.
func normalizeCloneURL(repoURL string) string {
	if isADOCloneHost(repoURL) {
		return normalizeADOCloneURL(repoURL)
	}

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
