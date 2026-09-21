// Package provider is the app's git-host abstraction for the Code panel. A
// Client is a read-only surface over one host (GitHub, Azure DevOps) whose
// credentials/config are bound at construction, so app_code.go can hold a set
// of configured providers for a profile and fan the same reads across them.
package provider

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

// Kind identifies which host a Client (and every row it returns) belongs to.
type Kind string

const (
	KindGitHub Kind = "github"
	KindADO    Kind = "ado"
)

// Reasons a PR/work-item made the "needs attention" list. Carried per row so
// the UI can say WHY it is listed.
const (
	ReasonAuthored        = "authored"
	ReasonReviewRequested = "review-requested"
	ReasonAssigned        = "assigned"
)

// RepoRef identifies a repo across providers. The frontend receives it on each
// repo row and echoes it back on a detail call, so RepoDetail routing needs no
// server-side lookup.
type RepoRef struct {
	Provider      Kind   `json:"provider"`
	Owner         string `json:"owner"` // GitHub: org/user;  ADO: project
	Name          string `json:"name"`
	ID            string `json:"id"` // ADO repo GUID; empty for GitHub
	CloneURL      string `json:"clone_url"`
	DefaultBranch string `json:"default_branch"`
}

// Repo is one repository row on the launchpad.
type Repo struct {
	Provider      Kind   `json:"provider"`
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
	// ID is the ADO repo GUID; empty for GitHub. Needed to build a RepoRef.
	ID string `json:"id"`
}

// Item is one PR / issue / work-item row. The three collapse into one shape so
// the panel renders a merged list; IsPullRequest distinguishes a PR from an
// issue/work-item.
type Item struct {
	Provider      Kind   `json:"provider"`
	RepoFullName  string `json:"repo_full_name"`
	Number        int    `json:"number"`
	Title         string `json:"title"`
	State         string `json:"state"`
	HTMLURL       string `json:"html_url"`
	UpdatedAt     string `json:"updated_at"`
	User          string `json:"user"`
	Draft         bool   `json:"draft"`
	IsPullRequest bool   `json:"is_pull_request"`
	// Reason is why this row is listed (authored/review-requested/assigned);
	// not a host field.
	Reason string `json:"reason"`
	// RepoID carries the ADO repo GUID for a PR row so a follow-up detail call
	// can build a RepoRef; empty for GitHub.
	RepoID string `json:"repo_id"`
}

// Branch is one branch head.
type Branch struct {
	Name string `json:"name"`
	SHA  string `json:"sha"`
}

// Commit is one entry from a repo's log. Message is the FULL message; the panel
// renders only its first line.
type Commit struct {
	SHA     string `json:"sha"`
	Message string `json:"message"`
	Author  string `json:"author"`
	Date    string `json:"date"`
	HTMLURL string `json:"html_url"`
}

// Notification is one GitHub inbox row. GitHub-only in v1.
type Notification struct {
	ID           string `json:"id"`
	RepoFullName string `json:"repo_full_name"`
	SubjectTitle string `json:"subject_title"`
	SubjectType  string `json:"subject_type"`
	Reason       string `json:"reason"`
	UpdatedAt    string `json:"updated_at"`
	URL          string `json:"url"`
}

// StatusError is a non-2xx response from a provider. It keeps the code so
// callers can tell "credential rejected" (401) from "host having a day" (5xx).
type StatusError struct {
	Code   int
	Status string
	Path   string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("provider %s: %s", e.Path, e.Status)
}

// IsUnauthorized reports whether err is a provider rejecting the credential.
func IsUnauthorized(err error) bool {
	var se *StatusError
	if errors.As(err, &se) {
		return se.Code == http.StatusUnauthorized
	}
	return false
}

// Client is the read surface the Code panel needs, one instance per
// (profile, provider), constructed from that provider's config.
type Client interface {
	Kind() Kind

	// Overview
	ListRepos(ctx context.Context) ([]Repo, error)
	SearchMyPRs(ctx context.Context) ([]Item, error)      // authored + review-requested, deduped, reasons tagged
	ListAssignedWork(ctx context.Context) ([]Item, error) // GitHub assigned issues / ADO @Me work items

	// Detail
	ListBranches(ctx context.Context, ref RepoRef) ([]Branch, error)
	ListRecentCommits(ctx context.Context, ref RepoRef, limit int) ([]Commit, error)
	GetReadme(ctx context.Context, ref RepoRef) (md string, htmlURL string, err error)
	RepoPRs(ctx context.Context, ref RepoRef) ([]Item, error)
	RepoIssues(ctx context.Context, ref RepoRef) ([]Item, error) // ADO: empty in v1

	NormalizeCloneURL(url string) string
}

// Notifier is the GitHub-only notifications surface. app_code.go type-asserts
// for it rather than putting a GitHub concept on every provider.
type Notifier interface {
	ListNotifications(ctx context.Context) ([]Notification, error)
}
