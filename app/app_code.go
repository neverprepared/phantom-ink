package main

// The Code page: a profile-scoped, single-pane view of the active profile's
// GitHub account, plus a one-click hand-off from a repo/PR/issue straight into
// a fleet agent task. Read-only; nothing here is persisted.
//
// Everything is host-side and profile-scoped: the token is the profile's
// EXISTING curated GITHUB_TOKEN, read fresh out of the gateway env store on
// every call. No new secret, no new credential storage, no cache — so a
// profile can only ever see its own GitHub content, and switching profiles
// cannot serve stale rows from the previous one.

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"phantom-ink/brainbox"
	"phantom-ink/githubclient"
)

// CodeOverview is one page-load of the Code panel.
//
// Section errors are per-section STRINGS rather than one returned error on
// purpose: a 401 on /notifications (the notifications scope is separate from
// repo scope on fine-grained PATs) must not blank the repositories list. The
// panel renders each section's error inside that section's card.
type CodeOverview struct {
	// Profile echoes back which profile these rows belong to, so a response
	// that lands after a profile switch can be discarded by the panel.
	Profile string `json:"profile"`
	// TokenMissing is set when the profile has no GITHUB_TOKEN at all. It is
	// NOT an error: the panel shows a "connect a token" banner pointing at the
	// Profiles panel instead of an error toast.
	TokenMissing bool `json:"token_missing"`
	// TokenInvalid is set when GitHub rejected the credential (401).
	TokenInvalid bool `json:"token_invalid"`

	Repos         []githubclient.Repo         `json:"repos"`
	PullRequests  []githubclient.Issue        `json:"pull_requests"`
	Issues        []githubclient.Issue        `json:"issues"`
	Notifications []githubclient.Notification `json:"notifications"`

	ReposError         string `json:"repos_error"`
	PullRequestsError  string `json:"pull_requests_error"`
	IssuesError        string `json:"issues_error"`
	NotificationsError string `json:"notifications_error"`
}

// githubFetcher is the read surface the overview needs. An interface (rather
// than the concrete *githubclient.Client) keeps buildCodeOverview testable
// with per-section failures that a live server can't easily be made to produce.
type githubFetcher interface {
	ListRepos(ctx context.Context, token string) ([]githubclient.Repo, error)
	SearchPRsAuthored(ctx context.Context, token string) ([]githubclient.Issue, error)
	SearchPRsReviewRequested(ctx context.Context, token string) ([]githubclient.Issue, error)
	SearchIssuesAssigned(ctx context.Context, token string) ([]githubclient.Issue, error)
	ListNotifications(ctx context.Context, token string) ([]githubclient.Notification, error)
}

// GitHubOverview fetches the active profile's GitHub launchpad view. Bound to
// the UI; one call drives the whole panel.
func (a *App) GitHubOverview(profile string) (CodeOverview, error) {
	env, err := a.GetGatewayEnv(profile)
	if err != nil {
		return CodeOverview{Profile: profile}, err
	}
	token := strings.TrimSpace(env["GITHUB_TOKEN"])
	if token == "" {
		// Not an error — a profile that hasn't curated a token yet is a
		// normal, actionable state.
		return CodeOverview{Profile: profile, TokenMissing: true}, nil
	}
	ctx := a.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	return buildCodeOverview(ctx, githubclient.New(), profile, token), nil
}

// buildCodeOverview fans the five GitHub reads out concurrently (one page-open
// is ~5 calls against a 5000/hr authenticated budget) and folds them into one
// struct, recording failures per section instead of aborting the page.
func buildCodeOverview(ctx context.Context, gh githubFetcher, profile, token string) CodeOverview {
	out := CodeOverview{Profile: profile}

	var (
		mu       sync.Mutex // guards the section results below
		wg       sync.WaitGroup
		authored []githubclient.Issue
		reviews  []githubclient.Issue
		prErrs   []string
		auth401  bool
	)

	// note records a section failure. A 401 anywhere means the credential
	// itself is rejected, which the panel surfaces as a reconnect banner.
	note := func(err error, dst *string) {
		mu.Lock()
		defer mu.Unlock()
		if githubclient.IsUnauthorized(err) {
			auth401 = true
		}
		*dst = err.Error()
	}

	run := func(fn func()) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			fn()
		}()
	}

	run(func() {
		repos, err := gh.ListRepos(ctx, token)
		if err != nil {
			note(err, &out.ReposError)
			return
		}
		mu.Lock()
		out.Repos = repos
		mu.Unlock()
	})

	run(func() {
		prs, err := gh.SearchPRsAuthored(ctx, token)
		mu.Lock()
		defer mu.Unlock()
		if err != nil {
			if githubclient.IsUnauthorized(err) {
				auth401 = true
			}
			prErrs = append(prErrs, err.Error())
			return
		}
		authored = prs
	})

	run(func() {
		prs, err := gh.SearchPRsReviewRequested(ctx, token)
		mu.Lock()
		defer mu.Unlock()
		if err != nil {
			if githubclient.IsUnauthorized(err) {
				auth401 = true
			}
			prErrs = append(prErrs, err.Error())
			return
		}
		reviews = prs
	})

	run(func() {
		issues, err := gh.SearchIssuesAssigned(ctx, token)
		if err != nil {
			note(err, &out.IssuesError)
			return
		}
		sortIssues(issues)
		mu.Lock()
		out.Issues = issues
		mu.Unlock()
	})

	run(func() {
		ns, err := gh.ListNotifications(ctx, token)
		if err != nil {
			note(err, &out.NotificationsError)
			return
		}
		mu.Lock()
		out.Notifications = ns
		mu.Unlock()
	})

	wg.Wait()

	out.PullRequests = mergePullRequests(authored, reviews)
	// A half-failed PR section still renders the half that worked, with the
	// failure named alongside it.
	out.PullRequestsError = strings.Join(prErrs, "; ")
	out.TokenInvalid = auth401
	return out
}

// mergePullRequests unions the authored and review-requested lists, deduping on
// html_url. A PR that is both mine AND waiting on my review appears once,
// tagged with both reasons, so the row explains why it is on the list.
func mergePullRequests(lists ...[]githubclient.Issue) []githubclient.Issue {
	byURL := map[string]int{}
	out := make([]githubclient.Issue, 0, 16)
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
	sortIssues(out)
	return out
}

// sortIssues orders rows most-recently-updated first. UpdatedAt is RFC3339 from
// GitHub, so a plain string compare is already chronological.
func sortIssues(rows []githubclient.Issue) {
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].UpdatedAt > rows[j].UpdatedAt })
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
		if err := runGitClone(cloneURL, dest); err != nil {
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

// normalizeCloneURL turns any GitHub reference to a repo into an https clone
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
func normalizeCloneURL(repoURL string) string {
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
