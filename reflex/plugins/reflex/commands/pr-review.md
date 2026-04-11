---
description: Review GitHub pull requests across tracked repositories (list/add/remove/repos/history)
allowed-tools: Bash(*), Read(*), AskUserQuestion(*), Write(*)
argument-hint: [list|add|remove|repos|history] [owner/repo] [pr-number]
---

# PR Review

Manage and review GitHub pull requests across a set of tracked repositories. Uses a local SQLite database at `${REFLEX_HOME:-$HOME/.config/reflex}/data.db` to store repos, settings, and review history.

## Paths

```bash
DB_DIR="${REFLEX_HOME:-$HOME/.config/reflex}"
DB_PATH="$DB_DIR/data.db"
```

## Initialization

Before any subcommand runs, verify the database exists:

```bash
if [[ ! -f "$DB_PATH" ]]; then
  echo "First run — initializing PR review database at $DB_PATH"
  # Run the database setup SQL from the pr-review skill
fi
```

If `gh auth status` fails, instruct the user to run `gh auth login` before continuing.

---

## Subcommands

### `/reflex:pr-review` or `/reflex:pr-review list`

Fetch and display open PRs across all active tracked repositories.

**Instructions:**

1. Resolve `DB_PATH`. Initialize database if absent.
2. Load active repos from `repos` table where `active=1`.
3. If no repos tracked, show:
   ```
   No repositories tracked yet.
   Add one with: /reflex:pr-review add <owner/repo>
   ```
4. For each repo, run:
   ```bash
   gh pr list --repo "$REPO" --state open \
     --json number,title,author,createdAt,labels,reviewDecision,isDraft,additions,deletions,milestone \
     --limit 50
   ```
5. Apply per-repo filters from `repo_settings`:
   - **labels_filter**: skip PRs that don't have at least one matching label (if filter is set)
   - **include_drafts**: if false (default), skip PRs where `isDraft=true`
   - **milestone_filter**: if set, skip PRs not in that milestone
6. Display grouped by repo:
   ```
   owner/repo  (3 open)
     #42  Add OAuth support          [@alice]  +120/-15
     #39  Fix rate limiting bug      [@bob]    +8/-3    [DRAFT]
     #37  Update dependencies        [@carol]  +2/-2    [needs-review]

   owner/other-repo  (1 open)
     #11  Refactor auth middleware   [@alice]  +340/-180
   ```
7. Prompt: `Select a PR to review (e.g. owner/repo#42), or [q] to quit:`
8. On selection, run the **review workflow** (see below).

---

### `/reflex:pr-review add <owner/repo>`

Add a repository to the tracking list.

**Instructions:**

1. Validate argument is in `owner/name` format.
2. Verify the repo is accessible: `gh repo view "$REPO" --json name` — if this fails, show the error and stop.
3. Insert into `repos` and `repo_settings` (use `INSERT OR IGNORE`).
4. Confirm:
   ```
   Now tracking ink-bunny/reflex
   Settings: review_depth=full_context, drafts=excluded, labels=(all), milestone=(all)
   Run /reflex:pr-review to see open PRs.
   ```

---

### `/reflex:pr-review remove <owner/repo>`

Remove a repository from the tracking list.

**Instructions:**

1. Confirm the repo exists in the database; if not, say so.
2. Use `AskUserQuestion`: "Remove ink-bunny/reflex from tracked repos? Review history will be preserved."
   - Options: **Remove** / **Cancel**
3. If confirmed: `DELETE FROM repos WHERE owner=? AND name=?`
4. Show: `Removed ink-bunny/reflex. Review history retained.`

---

### `/reflex:pr-review repos`

List all tracked repositories and their settings.

**Instructions:**

1. Query:
   ```sql
   SELECT r.owner || '/' || r.name AS repo,
          CASE r.active WHEN 1 THEN 'active' ELSE 'paused' END AS status,
          s.review_depth,
          CASE s.include_drafts WHEN 1 THEN 'yes' ELSE 'no' END AS drafts,
          COALESCE(s.labels_filter, '(all)') AS labels,
          COALESCE(s.milestone_filter, '(all)') AS milestone,
          r.added_at
   FROM repos r JOIN repo_settings s ON s.repo_id = r.id
   ORDER BY r.added_at DESC;
   ```
2. Display as a table.
3. Offer: `[e] Edit settings for a repo  [a] Add repo  [r] Remove repo  [q] Back`

---

### `/reflex:pr-review history [owner/repo]`

Show review history, optionally filtered to a specific repo.

**Instructions:**

1. If `owner/repo` argument given, filter to that repo; otherwise show all.
2. Query `pr_reviews` ordered by `reviewed_at DESC`, limit 50.
3. Display:
   ```
   Recent Reviews

   2026-03-31  ink-bunny/reflex      #42  approved          Add OAuth support
   2026-03-30  ink-bunny/brainbox    #17  changes_requested Fix session leak
   2026-03-29  ink-bunny/reflex      #39  commented         Update dependencies
   ```
4. Offer: `[f] Filter by action  [r] Filter by repo  [q] Back`

---

## Review Workflow

When a user selects a PR (e.g. `ink-bunny/reflex#42`):

1. **Fetch context**:
   - PR metadata: `gh pr view $PR_NUM --repo $REPO --json ...`
   - Diff: `gh pr diff $PR_NUM --repo $REPO`
   - Existing comments/reviews via `gh api`

2. **Fetch file context** (if `review_depth=full_context`):
   - Read full content of each changed file at HEAD

3. **Apply draft guard**:
   - If PR is a draft and `include_drafts=false` for this repo, warn: "This is a draft PR. Review anyway?" — Options: **Yes** / **No**

4. **Run agent review** following the analysis framework in the `pr-review` skill:
   - Correctness, security, test coverage, consistency, complexity, breaking changes, documentation

5. **Present findings** in structured format (critical / warnings / suggestions / recommended action)

6. **Ask for action**:
   ```
   Submit review:
   [a] Approve
   [r] Request changes
   [c] Comment only
   [s] Skip
   ```

7. **If approve or request changes**: optionally prompt for a summary comment body.

8. **Submit** via `gh pr review`.

9. **Record** in `pr_reviews` table.

10. **Return** to the PR list.

---

### No argument match

If the argument doesn't match any subcommand, show usage:

```
Usage: /reflex:pr-review [subcommand] [args...]

Subcommands:
  list                   List open PRs across tracked repos (default)
  add <owner/repo>       Track a new repository
  remove <owner/repo>    Stop tracking a repository
  repos                  List tracked repos and settings
  history [owner/repo]   Show review history

Examples:
  /reflex:pr-review
  /reflex:pr-review add ink-bunny/reflex
  /reflex:pr-review history ink-bunny/reflex
```
