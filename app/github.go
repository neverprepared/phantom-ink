package main

import (
	"encoding/json"
	"os/exec"
	"strings"
)

type RepoPR struct {
	Number    int      `json:"number"`
	Title     string   `json:"title"`
	Branch    string   `json:"branch"`
	Author    string   `json:"author"`
	URL       string   `json:"url"`
	IsDraft   bool     `json:"is_draft"`
	CreatedAt string   `json:"created_at"`
	CIStatus  string   `json:"ci_status"` // "passing", "failing", "pending", ""
	Labels    []string `json:"labels"`
}

type RepoBranch struct {
	Name      string `json:"name"`
	Protected bool   `json:"protected"`
	HasPR     bool   `json:"has_pr"`
	PRNumber  int    `json:"pr_number"`
}

type RepoEvent struct {
	Type      string `json:"type"`
	Actor     string `json:"actor"`
	CreatedAt string `json:"created_at"`
	Summary   string `json:"summary"`
}

type CIStatus struct {
	Branch string  `json:"branch"`
	Runs   []CIRun `json:"runs"`
}

type CIRun struct {
	Name       string `json:"name"`
	Status     string `json:"status"`
	Conclusion string `json:"conclusion"`
	URL        string `json:"url"`
	CreatedAt  string `json:"created_at"`
}

// ownerRepo strips the GitHub URL prefix and returns "owner/repo".
func ownerRepo(repoURL string) string {
	s := strings.TrimPrefix(repoURL, "https://github.com/")
	s = strings.TrimPrefix(s, "http://github.com/")
	return strings.TrimSuffix(s, "/")
}

// GetRepoPRs returns open PRs for the given GitHub repo URL.
func (a *App) GetRepoPRs(repoURL string) ([]RepoPR, error) {
	repo := ownerRepo(repoURL)
	cmd := exec.Command("gh", "pr", "list",
		"--repo", repo,
		"--json", "number,title,headRefName,author,createdAt,statusCheckRollup,url,labels,isDraft",
		"--limit", "20",
	)
	out, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	var raw []struct {
		Number      int    `json:"number"`
		Title       string `json:"title"`
		HeadRefName string `json:"headRefName"`
		Author      struct {
			Login string `json:"login"`
		} `json:"author"`
		CreatedAt          string `json:"createdAt"`
		StatusCheckRollup  []struct {
			Status     string `json:"status"`
			Conclusion string `json:"conclusion"`
		} `json:"statusCheckRollup"`
		URL     string `json:"url"`
		IsDraft bool   `json:"isDraft"`
		Labels  []struct {
			Name string `json:"name"`
		} `json:"labels"`
	}
	if err := json.Unmarshal(out, &raw); err != nil {
		return nil, err
	}

	prs := make([]RepoPR, 0, len(raw))
	for _, r := range raw {
		labels := make([]string, 0, len(r.Labels))
		for _, l := range r.Labels {
			labels = append(labels, l.Name)
		}

		ciStatus := deriveCIStatus(r.StatusCheckRollup)

		prs = append(prs, RepoPR{
			Number:    r.Number,
			Title:     r.Title,
			Branch:    r.HeadRefName,
			Author:    r.Author.Login,
			URL:       r.URL,
			IsDraft:   r.IsDraft,
			CreatedAt: r.CreatedAt,
			CIStatus:  ciStatus,
			Labels:    labels,
		})
	}
	return prs, nil
}

// deriveCIStatus collapses statusCheckRollup entries into a single string.
func deriveCIStatus(checks []struct {
	Status     string `json:"status"`
	Conclusion string `json:"conclusion"`
}) string {
	if len(checks) == 0 {
		return ""
	}
	for _, c := range checks {
		if c.Conclusion == "FAILURE" || c.Conclusion == "TIMED_OUT" || c.Conclusion == "CANCELLED" {
			return "failing"
		}
	}
	for _, c := range checks {
		if c.Status == "IN_PROGRESS" || c.Status == "QUEUED" || c.Status == "WAITING" {
			return "pending"
		}
	}
	return "passing"
}

// GetRepoBranches returns branches for the repo, annotated with open PR info.
func (a *App) GetRepoBranches(repoURL string) ([]RepoBranch, error) {
	repo := ownerRepo(repoURL)

	// Fetch branches via GitHub API
	cmd := exec.Command("gh", "api", "repos/"+repo+"/branches",
		"--paginate",
		"--jq", ".[] | {name: .name, protected: .protected}",
	)
	out, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	type branchRaw struct {
		Name      string `json:"name"`
		Protected bool   `json:"protected"`
	}

	var branches []RepoBranch
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line == "" {
			continue
		}
		var b branchRaw
		if err := json.Unmarshal([]byte(line), &b); err != nil {
			continue
		}
		branches = append(branches, RepoBranch{Name: b.Name, Protected: b.Protected})
	}

	// Cross-reference with open PRs to populate HasPR / PRNumber
	prs, err := a.GetRepoPRs(repoURL)
	if err == nil {
		prByBranch := make(map[string]int, len(prs))
		for _, pr := range prs {
			prByBranch[pr.Branch] = pr.Number
		}
		for i := range branches {
			if num, ok := prByBranch[branches[i].Name]; ok {
				branches[i].HasPR = true
				branches[i].PRNumber = num
			}
		}
	}

	return branches, nil
}

// GetRepoActivity returns the last 20 events for the repo.
func (a *App) GetRepoActivity(repoURL string) ([]RepoEvent, error) {
	repo := ownerRepo(repoURL)
	cmd := exec.Command("gh", "api",
		"repos/"+repo+"/events",
		"--jq", `.[:20][] | {type: .type, actor: .actor.login, created_at: .created_at, payload: .payload}`,
	)
	out, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	type rawEvent struct {
		Type      string `json:"type"`
		Actor     string `json:"actor"`
		CreatedAt string `json:"created_at"`
		Payload   struct {
			Ref     string `json:"ref"`
			Action  string `json:"action"`
			Commits []struct {
				Message string `json:"message"`
			} `json:"commits"`
			PullRequest struct {
				Title string `json:"title"`
			} `json:"pull_request"`
			Issue struct {
				Title string `json:"title"`
			} `json:"issue"`
		} `json:"payload"`
	}

	var events []RepoEvent
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line == "" {
			continue
		}
		var e rawEvent
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			continue
		}
		summary := summarizeEvent(e.Type, e.Payload.Action, e.Payload.Ref,
			e.Payload.Commits, e.Payload.PullRequest.Title, e.Payload.Issue.Title)
		events = append(events, RepoEvent{
			Type:      e.Type,
			Actor:     e.Actor,
			CreatedAt: e.CreatedAt,
			Summary:   summary,
		})
	}
	return events, nil
}

func summarizeEvent(typ, action, ref string, commits []struct{ Message string }, prTitle, issueTitle string) string {
	switch typ {
	case "PushEvent":
		branch := strings.TrimPrefix(ref, "refs/heads/")
		if len(commits) > 0 {
			msg := strings.SplitN(commits[0].Message, "\n", 2)[0]
			return "Pushed to " + branch + ": " + msg
		}
		return "Pushed to " + branch
	case "PullRequestEvent":
		if prTitle != "" {
			return strings.Title(action) + " PR: " + prTitle
		}
		return "PR " + action
	case "IssuesEvent":
		if issueTitle != "" {
			return strings.Title(action) + " issue: " + issueTitle
		}
		return "Issue " + action
	default:
		return typ
	}
}

// GetRepoCIStatus returns recent CI run statuses for a branch.
func (a *App) GetRepoCIStatus(repoURL string, branch string) (CIStatus, error) {
	repo := ownerRepo(repoURL)
	cmd := exec.Command("gh", "run", "list",
		"--repo", repo,
		"--branch", branch,
		"--limit", "5",
		"--json", "status,conclusion,name,createdAt,url",
	)
	out, err := cmd.Output()
	if err != nil {
		return CIStatus{Branch: branch}, err
	}

	var raw []struct {
		Name       string `json:"name"`
		Status     string `json:"status"`
		Conclusion string `json:"conclusion"`
		CreatedAt  string `json:"createdAt"`
		URL        string `json:"url"`
	}
	if err := json.Unmarshal(out, &raw); err != nil {
		return CIStatus{Branch: branch}, err
	}

	runs := make([]CIRun, 0, len(raw))
	for _, r := range raw {
		runs = append(runs, CIRun{
			Name:       r.Name,
			Status:     r.Status,
			Conclusion: r.Conclusion,
			URL:        r.URL,
			CreatedAt:  r.CreatedAt,
		})
	}
	return CIStatus{Branch: branch, Runs: runs}, nil
}
