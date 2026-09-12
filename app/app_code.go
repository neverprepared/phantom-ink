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
	"context"
	"sort"
	"strings"
	"sync"

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
