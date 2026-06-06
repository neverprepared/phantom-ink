# Lakeview PR Review

Enumerates repos checked out under `$WORKSPACE_HOME/code/`, queries GitHub and Azure DevOps for open pull requests on each, and produces a triage report showing which PRs need evaluation — flagging review requests, stale unreviewed PRs, and draft vs ready state.

- [ ] Build the repo list: run `find "$WORKSPACE_HOME/code" -maxdepth 2 -name "config" -path "*/.git/config" | sort` to locate all git repos. For each, extract the `url =` line under `[remote "origin"]` from that config file. Collect two buckets — **GitHub** repos (url contains `github.com`) and **ADO** repos (url contains `dev.azure.com` or `visualstudio.com`). For GitHub repos parse `<org>/<repo>` from the URL. For ADO repos parse `<org>/<project>/_git/<repo>` from the URL. Skip repos with no remote. Print the bucketed list so progress is visible.

- [ ] Query GitHub PRs: for each GitHub repo, run `gh pr list --repo <org>/<repo> --state open --json number,title,url,isDraft,createdAt,reviewRequests,reviews,author --limit 50 2>/dev/null`. If the command fails or returns empty, skip silently. Accumulate all results keyed by `<org>/<repo>`. If `gh` is not available or not authenticated, print a warning and skip this bucket.

- [ ] Query ADO PRs: verify `AZURE_DEVOPS_PAT` and `AZURE_DEVOPS_ORG` are set — if either is missing, print "ADO skipped: AZURE_DEVOPS_PAT or AZURE_DEVOPS_ORG not set" and skip this step. For each ADO repo, call the REST API: `curl -s -u ":$AZURE_DEVOPS_PAT" "https://dev.azure.com/$AZURE_DEVOPS_ORG/<project>/_apis/git/repositories/<repo>/pullrequests?searchCriteria.status=active&api-version=7.0"`. Parse the JSON response for `pullRequestId`, `title`, `url`, `isDraft`, `creationDate`, `reviewers` (status: approved/waiting/no vote), and `createdBy`. Accumulate results keyed by `<org>/<project>/<repo>`.

- [ ] Classify each PR into one of three triage categories:
  - **Needs your review** — you are listed as a reviewer with status `waiting` or `noVote` (GitHub: your login appears in `reviewRequests`; ADO: your UPN appears in `reviewers` with vote 0 or −5)
  - **Unreviewed** — open, not draft, no approvals and no review activity yet, older than 24 hours
  - **Informational** — everything else that is open (drafts, already approved, authored by you)
  Determine your GitHub login by running `gh api user --jq .login 2>/dev/null`. Determine your ADO UPN from `AZURE_DEVOPS_UPN` env var if set, otherwise skip reviewer matching for ADO.

- [ ] Produce the report. Print a header with today's date and total counts. Then for each triage category (Needs Your Review first, then Unreviewed, then Informational), print a table:

  ```
  ## Needs Your Review (N)
  | Repo | PR | Title | Age | Draft |
  |------|----|-------|-----|-------|
  | org/repo | #42 | Fix auth bug | 2d | No |

  ## Unreviewed (N)
  ...

  ## Informational (N)
  ...
  ```

  Age = days since `createdAt`/`creationDate`. Omit the Informational section if empty. After the tables, print a one-line summary: "N repos scanned · N PRs found · N need your attention".

- [ ] Store the report summary in memory: `memory_store(title="pr-review/$(date +%Y-%m-%d)", content="<full report text>", tags=["pr-review", "lakeview", "github"], status="active")`. This creates a dated snapshot so trends can be compared across runs.
