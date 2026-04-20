---
name: pr-review
description: Discover, review, and act on GitHub pull requests across tracked repositories
---

# PR Review Skill

## When to Use

- Reviewing open pull requests across multiple tracked GitHub repositories
- Getting an AI-assisted code review summary before a human review
- Submitting a formal review action (approve, request changes, comment) via the agent
- Tracking which repositories have open PRs awaiting review

> Manage pull request reviews across multiple GitHub repositories with agent-assisted analysis.

## Overview

This skill tracks GitHub repositories in a local SQLite database, discovers open pull requests, performs agent-assisted code reviews, and submits formal review actions (approve, request changes, comment).

Capabilities:
- Add, remove, and list tracked repositories
- Fetch open PRs across all tracked repos or a specific repo
- Run an agent review on a PR (diff + context analysis)
- Post inline comments, general comments, approvals, or change requests
- Maintain a review history for auditing and filtering

## Prerequisites

```bash
# GitHub CLI — required for all GitHub operations
brew install gh        # macOS
# https://cli.github.com/

# SQLite3 — for the local repo/review database
brew install sqlite    # macOS (usually pre-installed)

# jq — for JSON processing
brew install jq
```

## Authentication

```bash
# Authenticate with GitHub CLI (one-time)
gh auth login

# Verify auth
gh auth status
```

## Configuration

The database lives outside any Claude Code workspace at:

```
$REFLEX_HOME/data.db        # if REFLEX_HOME is set
$HOME/.config/reflex/data.db  # default
```

The skill resolves the path as:

```bash
DB_DIR="${REFLEX_HOME:-$HOME/.config/reflex}"
DB_PATH="$DB_DIR/data.db"
```

Override for the current session:

```bash
export REFLEX_HOME=/path/to/custom/dir
```

## Database Setup

Initialize the database on first use:

```bash
#!/bin/bash
DB_DIR="${REFLEX_HOME:-$HOME/.config/reflex}"
DB_PATH="$DB_DIR/data.db"

mkdir -p "$DB_DIR"

sqlite3 "$DB_PATH" <<'SQL'
CREATE TABLE IF NOT EXISTS repos (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  owner      TEXT NOT NULL,
  name       TEXT NOT NULL,
  added_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  active     INTEGER DEFAULT 1,
  UNIQUE(owner, name)
);

CREATE TABLE IF NOT EXISTS repo_settings (
  repo_id          INTEGER PRIMARY KEY,
  review_depth     TEXT    DEFAULT 'full_context',  -- 'diff_only' | 'full_context'
  labels_filter    TEXT,                             -- comma-separated labels, NULL = all
  include_drafts   INTEGER DEFAULT 0,               -- 0 = exclude drafts, 1 = include
  milestone_filter TEXT,                            -- milestone title to filter on, NULL = all
  FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pr_reviews (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  owner       TEXT NOT NULL,
  name        TEXT NOT NULL,
  pr_number   INTEGER NOT NULL,
  pr_title    TEXT,
  reviewed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  action      TEXT,   -- 'approved' | 'changes_requested' | 'commented' | 'skipped'
  summary     TEXT
);

CREATE INDEX IF NOT EXISTS idx_pr_reviews_repo  ON pr_reviews(owner, name);
CREATE INDEX IF NOT EXISTS idx_pr_reviews_pr    ON pr_reviews(owner, name, pr_number);
SQL

echo "Database initialized at $DB_PATH"
```

## Repo Management

### Add a repository

```bash
DB_PATH="${REFLEX_HOME:-$HOME/.config/reflex}/data.db"
REPO="$1"  # owner/name format, e.g. ink-bunny/reflex

OWNER="${REPO%%/*}"
NAME="${REPO##*/}"

REPO_ID=$(sqlite3 "$DB_PATH" <<SQL
INSERT OR IGNORE INTO repos (owner, name) VALUES ('$OWNER', '$NAME');
INSERT OR IGNORE INTO repo_settings (repo_id)
  SELECT id FROM repos WHERE owner='$OWNER' AND name='$NAME';
SELECT id FROM repos WHERE owner='$OWNER' AND name='$NAME';
SQL
)

echo "Tracking $REPO (id=$REPO_ID)"
```

### List tracked repositories

```bash
DB_PATH="${REFLEX_HOME:-$HOME/.config/reflex}/data.db"

sqlite3 -column -header "$DB_PATH" <<'SQL'
SELECT
  r.owner || '/' || r.name AS repo,
  CASE r.active WHEN 1 THEN 'active' ELSE 'paused' END AS status,
  s.review_depth,
  CASE s.include_drafts WHEN 1 THEN 'yes' ELSE 'no' END AS drafts,
  COALESCE(s.labels_filter, '(all)') AS labels,
  COALESCE(s.milestone_filter, '(all)') AS milestone,
  r.added_at
FROM repos r
JOIN repo_settings s ON s.repo_id = r.id
ORDER BY r.added_at DESC;
SQL
```

### Remove a repository

```bash
DB_PATH="${REFLEX_HOME:-$HOME/.config/reflex}/data.db"
REPO="$1"  # owner/name

OWNER="${REPO%%/*}"
NAME="${REPO##*/}"

sqlite3 "$DB_PATH" "DELETE FROM repos WHERE owner='$OWNER' AND name='$NAME';"
echo "Removed $REPO"
```

### Update repo settings

```bash
DB_PATH="${REFLEX_HOME:-$HOME/.config/reflex}/data.db"
REPO="$1"            # owner/name
DEPTH="$2"           # 'diff_only' or 'full_context'
LABELS="$3"          # comma-separated labels, or empty for all
INCLUDE_DRAFTS="$4"  # 0 or 1
MILESTONE="$5"       # milestone title, or empty for all

OWNER="${REPO%%/*}"
NAME="${REPO##*/}"

sqlite3 "$DB_PATH" <<SQL
UPDATE repo_settings
SET review_depth     = '$DEPTH',
    labels_filter    = NULLIF('$LABELS', ''),
    include_drafts   = ${INCLUDE_DRAFTS:-0},
    milestone_filter = NULLIF('$MILESTONE', '')
WHERE repo_id = (SELECT id FROM repos WHERE owner='$OWNER' AND name='$NAME');
SQL
```

## PR Discovery

### Fetch open PRs across all tracked repos

```bash
#!/bin/bash
DB_PATH="${REFLEX_HOME:-$HOME/.config/reflex}/data.db"

# Load active repos
REPOS=$(sqlite3 "$DB_PATH" \
  "SELECT owner || '/' || name FROM repos WHERE active=1;")

if [[ -z "$REPOS" ]]; then
  echo "No tracked repositories. Add one with: add <owner/repo>"
  exit 0
fi

echo "Fetching open PRs..."
echo ""

while IFS= read -r REPO; do
  OWNER="${REPO%%/*}"
  NAME="${REPO##*/}"

  # Load per-repo settings
  SETTINGS=$(sqlite3 "$DB_PATH" \
    "SELECT include_drafts, COALESCE(labels_filter,''), COALESCE(milestone_filter,'')
     FROM repo_settings s JOIN repos r ON s.repo_id=r.id
     WHERE r.owner='$OWNER' AND r.name='$NAME';")

  INCLUDE_DRAFTS=$(echo "$SETTINGS" | cut -d'|' -f1)
  LABELS_FILTER=$(echo "$SETTINGS" | cut -d'|' -f2)
  MILESTONE_FILTER=$(echo "$SETTINGS" | cut -d'|' -f3)

  # Build gh flags
  MILESTONE_FLAG=""
  [[ -n "$MILESTONE_FILTER" ]] && MILESTONE_FLAG="--search \"milestone:\\\"$MILESTONE_FILTER\\\"\""

  PRS=$(gh pr list \
    --repo "$REPO" \
    --state open \
    --json number,title,author,createdAt,labels,reviewDecision,isDraft,additions,deletions,milestone \
    --limit 50 2>/dev/null)

  # Apply draft filter
  if [[ "$INCLUDE_DRAFTS" == "0" ]]; then
    PRS=$(echo "$PRS" | jq '[.[] | select(.isDraft == false)]')
  fi

  # Apply labels filter (PR must have at least one matching label)
  if [[ -n "$LABELS_FILTER" ]]; then
    PRS=$(echo "$PRS" | jq --arg f "$LABELS_FILTER" '
      ($f | split(",")) as $wanted |
      [.[] | select(.labels | map(.name) | any(. as $l | $wanted[] | . == $l))]
    ')
  fi

  # Apply milestone filter
  if [[ -n "$MILESTONE_FILTER" ]]; then
    PRS=$(echo "$PRS" | jq --arg m "$MILESTONE_FILTER" \
      '[.[] | select(.milestone != null and .milestone.title == $m)]')
  fi

  COUNT=$(echo "$PRS" | jq length)

  if [[ "$COUNT" -eq 0 ]]; then
    echo "  $REPO — no open PRs"
    continue
  fi

  echo "  $REPO ($COUNT open)"
  echo "$PRS" | jq -r '.[] |
    "    #\(.number)  \(.title[:60])  [@\(.author.login)]  +\(.additions)/-\(.deletions)  \(if .isDraft then "[DRAFT]" else "" end)\(if .milestone then "[\(.milestone.title)]" else "" end)"'
  echo ""
done <<< "$REPOS"
```

### Fetch PRs for a single repo

```bash
REPO="$1"  # owner/name

gh pr list \
  --repo "$REPO" \
  --state open \
  --json number,title,author,createdAt,labels,reviewDecision,isDraft,additions,deletions \
  | jq -r '.[] | "#\(.number)  +\(.additions)/-\(.deletions)  \(.title)  [@\(.author.login)]"'
```

## PR Review Workflow

When a user selects a PR to review, follow this workflow:

### Step 0 — Draft guard

```bash
IS_DRAFT=$(gh pr view "$PR_NUM" --repo "$REPO" --json isDraft --jq '.isDraft')
INCLUDE_DRAFTS=$(sqlite3 "$DB_PATH" \
  "SELECT s.include_drafts FROM repo_settings s
   JOIN repos r ON s.repo_id=r.id
   WHERE r.owner='$OWNER' AND r.name='$NAME';")

if [[ "$IS_DRAFT" == "true" && "$INCLUDE_DRAFTS" == "0" ]]; then
  echo "⚠  PR #$PR_NUM is a draft. Proceed with review anyway? [y/N]"
  # If user says no, return to PR list without recording a review
fi
```

### Step 1 — Fetch PR context

```bash
REPO="$1"    # owner/name
PR_NUM="$2"  # PR number

# PR metadata
gh pr view "$PR_NUM" --repo "$REPO" \
  --json number,title,body,author,createdAt,labels,additions,deletions,changedFiles,baseRefName,headRefName

# Full diff
gh pr diff "$PR_NUM" --repo "$REPO"

# File list
gh pr view "$PR_NUM" --repo "$REPO" --json files --jq '.files[].path'

# Existing review comments (for context)
gh api "repos/$REPO/pulls/$PR_NUM/reviews" | jq '.[] | {user:.user.login, state:.state, body:.body}'
gh api "repos/$REPO/pulls/$PR_NUM/comments" | jq '.[] | {path:.path, line:.line, body:.body}'
```

### Step 2 — Fetch surrounding file context (if review_depth = full_context)

```bash
REPO="$1"
PR_NUM="$2"

# Get changed file paths
FILES=$(gh pr view "$PR_NUM" --repo "$REPO" --json files --jq '.files[].path')

# For each changed file, fetch the current HEAD version for full context
while IFS= read -r FILE; do
  echo "=== $FILE ==="
  gh api "repos/$REPO/contents/$FILE" \
    --jq '.content' | base64 --decode 2>/dev/null \
    || echo "(binary or unavailable)"
  echo ""
done <<< "$FILES"
```

### Step 3 — Agent review analysis

When reviewing a PR, analyze:

1. **Correctness** — does the logic achieve the stated goal? Are there bugs or edge cases?
2. **Security** — SQL injection, XSS, command injection, exposed secrets, unsafe deserialization
3. **Test coverage** — are the changes adequately tested? Missing edge cases?
4. **Consistency** — does the code follow existing patterns in the repo?
5. **Complexity** — is there unnecessary complexity? Could this be simpler?
6. **Breaking changes** — does this break existing interfaces or contracts?
7. **Documentation** — are significant changes reflected in docs/comments?

Produce a structured review summary:

```
## PR Review: <title> (#<number>)

**Repo**: owner/repo
**Author**: @username
**Diff**: +N/-N across N files

### Summary
<2-3 sentence overview of what this PR does>

### Findings

#### Critical (must fix before merge)
- [ ] <issue> — `path/to/file.py:42` — <explanation>

#### Warnings (should fix)
- [ ] <issue> — `path/to/file.py:99` — <explanation>

#### Suggestions (optional improvements)
- [ ] <suggestion>

### Decision
**Recommended action**: APPROVE | REQUEST_CHANGES | COMMENT

**Rationale**: <one-line justification>
```

### Step 4 — Submit review action

After the agent review, present the user with options:

```
[a] Approve
[r] Request changes
[c] Comment only
[s] Skip (no review submitted)
```

#### Approve

```bash
REPO="$1"
PR_NUM="$2"
BODY="$3"  # Optional summary comment

gh pr review "$PR_NUM" --repo "$REPO" --approve \
  ${BODY:+--body "$BODY"}
```

#### Request changes

```bash
REPO="$1"
PR_NUM="$2"
BODY="$3"  # Required: summary of what needs fixing

gh pr review "$PR_NUM" --repo "$REPO" --request-changes \
  --body "$BODY"
```

#### Comment only

```bash
REPO="$1"
PR_NUM="$2"
BODY="$3"

gh pr review "$PR_NUM" --repo "$REPO" --comment \
  --body "$BODY"
```

#### Post inline comment on a specific line

```bash
REPO="$1"
PR_NUM="$2"
COMMIT_SHA=$(gh pr view "$PR_NUM" --repo "$REPO" --json headRefOid --jq '.headRefOid')

gh api "repos/$REPO/pulls/$PR_NUM/comments" \
  --method POST \
  --field body="$COMMENT_BODY" \
  --field commit_id="$COMMIT_SHA" \
  --field path="$FILE_PATH" \
  --field line="$LINE_NUMBER" \
  --field side="RIGHT"
```

## Review History

### Record a completed review

```bash
DB_PATH="${REFLEX_HOME:-$HOME/.config/reflex}/data.db"
OWNER="$1"
NAME="$2"
PR_NUM="$3"
PR_TITLE="$4"
ACTION="$5"   # approved | changes_requested | commented | skipped
SUMMARY="$6"

sqlite3 "$DB_PATH" <<SQL
INSERT INTO pr_reviews (owner, name, pr_number, pr_title, action, summary)
VALUES ('$OWNER', '$NAME', $PR_NUM, '$PR_TITLE', '$ACTION', '$SUMMARY');
SQL
```

### Query review history

```bash
DB_PATH="${REFLEX_HOME:-$HOME/.config/reflex}/data.db"

# Recent reviews across all repos
sqlite3 -column -header "$DB_PATH" <<'SQL'
SELECT
  owner || '/' || name AS repo,
  '#' || pr_number     AS pr,
  action,
  substr(reviewed_at, 1, 10) AS date,
  substr(pr_title, 1, 50)    AS title
FROM pr_reviews
ORDER BY reviewed_at DESC
LIMIT 25;
SQL

# PRs reviewed in a specific repo
sqlite3 -column -header "$DB_PATH" \
  "SELECT pr_number, pr_title, action, reviewed_at FROM pr_reviews
   WHERE owner='$OWNER' AND name='$NAME'
   ORDER BY reviewed_at DESC;"

# PRs not yet reviewed (cross-reference with open PRs)
# Usage: pass in a list of open PR numbers and find ones missing from history
```

## Full Workflow

When invoked as `/reflex:pr-review`, follow this interactive flow:

1. **Initialize** — ensure the database exists (run setup if `data.db` is missing)
2. **Show menu**:
   ```
   PR Review
   ─────────────────────────────
   [l] List open PRs (all repos)
   [a] Add a repository
   [r] Remove a repository
   [s] Repository settings
   [h] Review history
   [q] Quit
   ```
3. **PR list** — when the user selects a PR:
   - Fetch diff and context per repo_settings.review_depth
   - Run agent review analysis (Step 3 above)
   - Present findings summary
   - Ask: approve / request changes / comment / skip
   - Submit review via `gh pr review`
   - Record result in `pr_reviews` table
4. **After each action** — return to the PR list or main menu

## Tips

- **Draft PRs**: Hidden by default (`include_drafts=0`); shown with `[DRAFT]` tag when included. The draft guard prompts before reviewing even if surfaced directly.
- **Milestone filter**: Use to focus on a release milestone, e.g. `v2.0.0`. PRs with no milestone are excluded when a filter is set.
- **Labels filter**: Comma-separated list, e.g. `needs-review,ready`. A PR matches if it has any one of the listed labels.
- **Large diffs**: For PRs with > 500 line changes, use `diff_only` depth to stay focused on the delta rather than full file context.
- **Re-review**: A PR can appear in history multiple times — the latest entry reflects the current decision.
- **Token auth**: If `gh auth status` shows an expired token, run `gh auth refresh`.
