---
description: Manage the brainbox API and sandboxed dev environments
allowed-tools: Bash(curl:*), Bash(brainbox:*), Bash(open:*), Bash(kill:*), Bash(cat:*), Bash(mkdir:*), Bash(echo:*), Bash(jq:*)
argument-hint: <start|stop|status|create|ratchet|orchestrate|delete|query|dashboard|config|health>
---

# Brainbox

Manage the brainbox API that provides sandboxed environments (Docker containers and UTM VMs).

## Session Modes

Brainbox supports three distinct session modes. Each has a first-class command and composes with the others:

| Mode | Command | Backend | Lifetime | Auto-cleanup | Claude Teams |
|------|---------|---------|----------|--------------|--------------|
| **Interactive** | `create` | Docker or UTM | Manual | No | Optional |
| **Brownian Ratchet** | `ratchet` | Docker (default) or UTM | Task duration | Yes — on completion | Yes |
| **Orchestrator** | `orchestrate` | UTM (default) or Docker | Project duration | No — explicit | Yes |

- **Interactive** — user-facing terminal session; user stays connected; manual lifecycle.
- **Brownian Ratchet** — fire-and-forget autonomous task: agent explores, commits progress forward (the ratchet), spawns sub-agents as needed via Claude Teams, auto-deletes when task completes.
- **Orchestrator** — long-running supervisor that manages a fleet; spawns ratchet workers for subtasks; lives across many task cycles; explicit teardown.

Claude Teams (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) is automatically enabled for `ratchet` and `orchestrate` modes.

## Paths

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CONFIG_DIR="${CLAUDE_DIR}/reflex"
CONFIG_FILE="${CONFIG_DIR}/brainbox.json"
URL_FILE="${CONFIG_DIR}/.brainbox-url"
PID_FILE="${CONFIG_DIR}/.brainbox-pid"
CONNECT_SCRIPT="${CLAUDE_PLUGIN_ROOT}/scripts/brainbox-connect.sh"
```

## Subcommands

### `/reflex:brainbox start`

Start the brainbox API locally (if not already running).

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
"${CLAUDE_PLUGIN_ROOT}/scripts/brainbox-connect.sh"
```

Show the result to the user. If status is "connected", say it was already running. If "started", confirm the API was started. If "unavailable", explain why (check the `reason` field).

### `/reflex:brainbox stop`

Stop a locally auto-started brainbox API.

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
PID_FILE="${CLAUDE_DIR}/reflex/.brainbox-pid"

if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE")
  kill "$PID" 2>/dev/null && echo "Brainbox API stopped (pid $PID)." || echo "Process $PID not running."
  rm -f "$PID_FILE" "${CLAUDE_DIR}/reflex/.brainbox-url"
else
  echo "No locally-started API to stop (no PID file found)."
  echo "If the API was started externally, stop it from its original process."
fi
```

### `/reflex:brainbox status`

Show connection info, running containers, and dashboard URL.

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
URL_FILE="${CLAUDE_DIR}/reflex/.brainbox-url"
CONFIG_FILE="${CLAUDE_DIR}/reflex/brainbox.json"

echo "## Brainbox Status"
echo ""

# Connection
if [ -f "$URL_FILE" ]; then
  URL=$(cat "$URL_FILE")
  echo "**Connection:** Connected at ${URL}"

  # Container count
  SESSIONS=$(curl -sf "${URL}/api/sessions" --max-time 3 2>/dev/null || echo "[]")
  TOTAL=$(echo "$SESSIONS" | jq 'length' 2>/dev/null || echo "?")
  ACTIVE=$(echo "$SESSIONS" | jq '[.[] | select(.active == true)] | length' 2>/dev/null || echo "?")
  echo "**Containers:** ${ACTIVE} running / ${TOTAL} total"

  # Dashboard
  echo "**Dashboard:** ${URL}"
else
  echo "**Connection:** Not connected"
  echo ""
  echo "Start with: /reflex:brainbox start"
fi

echo ""

# Config
echo "**Configuration:**"
if [ -f "$CONFIG_FILE" ]; then
  cat "$CONFIG_FILE" | jq .
else
  echo "  Using defaults (url: http://127.0.0.1:9999, autostart: true)"
fi

echo ""
echo "**Environment overrides:**"
echo "  BRAINBOX_URL=${BRAINBOX_URL:-<not set>}"
echo "  BRAINBOX_AUTOSTART=${BRAINBOX_AUTOSTART:-<not set>}"
```

### `/reflex:brainbox dashboard`

Open the brainbox dashboard in the default browser.

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
URL_FILE="${CLAUDE_DIR}/reflex/.brainbox-url"

if [ -f "$URL_FILE" ]; then
  URL=$(cat "$URL_FILE")
  open "$URL" 2>/dev/null || echo "Dashboard URL: $URL"
  echo "Opened dashboard at $URL"
else
  echo "Brainbox is not connected. Start it first:"
  echo "  /reflex:brainbox start"
fi
```

### `/reflex:brainbox create`

Create a new sandboxed container. Auto-detects the caller's workspace profile and home from environment variables (`WORKSPACE_PROFILE`, `WORKSPACE_HOME`).

Supports agent role selection via `--role`, additional volume mounts via `--mount`, and repo access configuration via `--repo`, `--repo-mode`, and `--branch` flags.

**If `$ARG` is empty (no arguments provided), run the interactive wizard** — ask each question in sequence using `AskUserQuestion`, then proceed to the payload section with the gathered values.

#### Interactive wizard (no-argument path)

**Step 1 — Container name**

Ask:
```
Container name? (leave blank to use your workspace profile name: <WORKSPACE_PROFILE or "default">)
```
Use the answer or fall back to `${WORKSPACE_PROFILE:-default}`.

**Step 2 — Agent role**

Ask:
```
Agent role?

  1) developer     — Interactive Claude Code session (default)
  2) supervisor    — Persistent orchestrator: assigns tasks to workers, monitors progress
  3) worker        — Transient task executor: completes a task and opens a PR
  4) reviewer      — Transient PR reviewer: reads code, flags blocking issues
  5) merge-queue   — Persistent merge gatekeeper: auto-merges PRs that pass CI
  6) pr-shepherd   — Persistent PR monitor: nudges stalled PRs, pings reviewers

Enter choice (1–6) [1]:
```
Map answer to role name (`developer`, `supervisor`, etc.). Default: `developer`.

**Step 3 — Repo**

Ask:
```
Attach a repo? Enter a local path or git remote URL, or leave blank to skip.
```
If blank, skip steps 4–7 and continue to step 8.

**Step 4 — Repo mode** (only if repo provided)

Ask:
```
Repo access mode for <repo>?

  1) worktree-mount   — Create a git worktree on your machine and mount it in.
                        Edits are immediately visible on the host branch.
  2) clone            — Clone fresh inside the container. No host paths modified.
  3) clone-worktree   — Clone fresh, then create an inner worktree for the branch.
  4) ci-ratchet       — Autonomous worker: clones, does task, opens PR. CI merges it.
                        Repo does NOT need to exist locally.

Enter choice (1–4):
```

**Step 5 — Branch** (modes 1–3 only)

Ask:
```
Branch name? [brainbox/<container-name>]
```
Default: `brainbox/<name>`.

**Step 6 — Task** (ci-ratchet only)

Ask:
```
Task for this worker (what should the agent accomplish?):
```
(Required — re-prompt if blank.)

**Step 7 — Merge queue** (ci-ratchet only)

Ask:
```
Auto-start a merge-queue agent to merge passing PRs? [Y/n]
```
Default: yes.

**Step 8 — Extra volume mounts**

Ask:
```
Add extra volume mounts? Enter as /host/path:/container/path[:ro], one per line.
Leave blank and press Enter when done.
```
Collect until a blank line is entered.

**Step 9 — Confirm**

Display a summary of all collected values and ask:
```
Create container with these settings? [Y/n]
```
If no, abort with "Cancelled."

Then proceed to the payload section using the wizard-collected values.

---

#### Flag-based path (arguments provided)

Parse the `$ARG` from the user's argument:
- First non-flag argument is the container name (defaults to profile name if not provided)
- `--role <role>` — Agent role for this container (default: `developer`). Roles:
  - `developer` — Default interactive Claude Code session with full polyglot toolchain
  - `supervisor` — Persistent orchestrator: assigns tasks to workers, monitors progress, enforces roadmap
  - `worker` — Transient task executor: completes assigned work and opens a PR when done
  - `reviewer` — Transient PR reviewer: reads code, posts comments, flags blocking issues
  - `merge-queue` — Persistent merge gatekeeper: auto-merges PRs that pass CI
  - `pr-shepherd` — Persistent PR monitor: nudges stalled PRs, pings reviewers and authors
- `--mount /host/path:/container/path[:mode]` — Additional volume mounts (can be specified multiple times)
- `--repo <path-or-url>` — Local path or git remote URL to make available inside the container
- `--repo-mode worktree-mount|clone|clone-worktree|ci-ratchet` — How the repo is delivered:
  - `worktree-mount` — Create a git worktree on the host and mount it in (isolated branch, edits visible on host)
  - `clone` — Clone fresh inside the container, no host mount (fully isolated)
  - `clone-worktree` — Clone fresh then create an inner worktree for the branch (fully isolated, extra worktree isolation)
  - `ci-ratchet` — Autonomous worker: clones repo, completes task, opens PR; CI merges it. Repo does NOT need to exist on this machine. (Brownian ratchet concept from [multiclaude](https://github.com/dlorenc/multiclaude) by Dan Lorenc et al.)
- `--branch <name>` — Branch to create/checkout (defaults to `brainbox/<session-name>` for non-ci-ratchet; `work/<session-name>` for ci-ratchet)
- `--container-path <path>` — Where to mount/clone inside the container (default: `/home/developer/workspace/repo`)
- `--task <description>` — Task for the worker agent (required for `ci-ratchet` mode)
- `--no-merge-queue` — Skip auto-starting the merge-queue agent (ci-ratchet only; default: start it)
- `--backend docker|utm` — Execution backend (default: `docker`). Use `utm` for macOS/Windows guest OS or long-running workloads that need a full VM.
- `--vm-template <name>` — UTM VM template to clone (required when `--backend utm`; e.g. `brainbox-macos-template`)
- `--guest-os macos|linux|windows` — Guest OS for UTM VMs (default: `linux`)

**If `--repo` is provided but `--repo-mode` is not specified**, ask the user before proceeding (same mode menu as wizard step 4 above), then ask for branch / task / merge-queue as needed.

---

After gathering all inputs (via wizard or flags), build and send the payload:

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
URL_FILE="${CLAUDE_DIR}/reflex/.brainbox-url"

if [ ! -f "$URL_FILE" ]; then
  echo "Brainbox is not connected. Start it first:"
  echo "  /reflex:brainbox start"
  exit 1
fi

URL=$(cat "$URL_FILE")
PROFILE="${WORKSPACE_PROFILE:-}"
WS_HOME="${WORKSPACE_HOME:-}"

# Parse arguments
NAME=$(echo "$ARG" | sed -E 's/^([^ ]*).*/\1/' | grep -v '^--' || echo "")
if [ -z "$NAME" ] || echo "$NAME" | grep -q '^--'; then
  NAME="${PROFILE:-default}"
fi

ROLE=$(echo "$ARG" | grep -oE -- '--role [^ ]+' | sed 's/--role //' | head -1)
ROLE="${ROLE:-developer}"
REPO_URL=$(echo "$ARG" | grep -oE -- '--repo [^ ]+' | sed 's/--repo //' | head -1)
REPO_MODE=$(echo "$ARG" | grep -oE -- '--repo-mode [^ ]+' | sed 's/--repo-mode //' | head -1)
BRANCH=$(echo "$ARG" | grep -oE -- '--branch [^ ]+' | sed 's/--branch //' | head -1)
CONTAINER_PATH=$(echo "$ARG" | grep -oE -- '--container-path [^ ]+' | sed 's/--container-path //' | head -1)
TASK=$(echo "$ARG" | grep -oE -- '--task [^ ]+.*' | sed 's/--task //' | head -1)
NO_MERGE_QUEUE=$(echo "$ARG" | grep -c -- '--no-merge-queue' || true)
BACKEND=$(echo "$ARG" | grep -oE -- '--backend [^ ]+' | sed 's/--backend //' | head -1)
BACKEND="${BACKEND:-docker}"
VM_TEMPLATE=$(echo "$ARG" | grep -oE -- '--vm-template [^ ]+' | sed 's/--vm-template //' | head -1)
GUEST_OS=$(echo "$ARG" | grep -oE -- '--guest-os [^ ]+' | sed 's/--guest-os //' | head -1)
GUEST_OS="${GUEST_OS:-linux}"

VOLUMES=$(echo "$ARG" | grep -oE -- '--mount [^ ]+' | sed 's/--mount //' | jq -R . | jq -s -c . || echo "[]")
if [ "$VOLUMES" = "[]" ] || [ -z "$VOLUMES" ]; then
  VOLUMES=""
fi

# Build JSON payload
PAYLOAD=$(jq -n \
  --arg name "$NAME" \
  --arg role "$ROLE" \
  --arg profile "$PROFILE" \
  --arg ws_home "$WS_HOME" \
  --arg backend "$BACKEND" \
  --arg vm_template "$VM_TEMPLATE" \
  --arg guest_os "$GUEST_OS" \
  '{name: $name, role: $role, backend: $backend} +
   (if $profile != "" then {workspace_profile: $profile} else {} end) +
   (if $ws_home != "" then {workspace_home: $ws_home} else {} end) +
   (if $vm_template != "" then {vm_template: $vm_template} else {} end) +
   (if $backend == "utm" then {guest_os: $guest_os} else {} end)')

if [ -n "$VOLUMES" ] && [ "$VOLUMES" != "[]" ]; then
  PAYLOAD=$(echo "$PAYLOAD" | jq --argjson vols "$VOLUMES" '. + {volumes: $vols}')
fi

if [ -n "$REPO_URL" ]; then
  CONTAINER_PATH="${CONTAINER_PATH:-/home/developer/workspace/repo}"
  if [ "$REPO_MODE" = "ci-ratchet" ]; then
    # ci-ratchet: branch defaults server-side to work/<name>; task and start_merge_queue included
    START_MQ="true"
    [ "$NO_MERGE_QUEUE" -gt 0 ] && START_MQ="false"
    REPO_OBJ=$(jq -n \
      --arg url "$REPO_URL" \
      --arg mode "$REPO_MODE" \
      --arg branch "$BRANCH" \
      --arg cpath "$CONTAINER_PATH" \
      --arg task "$TASK" \
      --argjson smq "$START_MQ" \
      '{url: $url, mode: $mode, container_path: $cpath, task: $task, start_merge_queue: $smq} +
       (if $branch != "" then {branch: $branch} else {} end)')
  else
    BRANCH="${BRANCH:-brainbox/${NAME}}"
    REPO_OBJ=$(jq -n \
      --arg url "$REPO_URL" \
      --arg mode "$REPO_MODE" \
      --arg branch "$BRANCH" \
      --arg cpath "$CONTAINER_PATH" \
      '{url: $url, mode: $mode, branch: $branch, container_path: $cpath}')
  fi
  PAYLOAD=$(echo "$PAYLOAD" | jq --argjson repo "$REPO_OBJ" '. + {repo: $repo}')
fi

API_KEY=$(curl -sf "${URL}/api/auth/key" --max-time 3 2>/dev/null | jq -r '.key // empty' 2>/dev/null || true)

RESULT=$(curl -sf -X POST "${URL}/api/create" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: ${API_KEY}" \
  -d "$PAYLOAD" --max-time 60 2>&1)

echo "$RESULT"
```

Show the result: on success report the container URL, detected profile, and (if a repo was configured) the mode and branch used. For `worktree-mount`, note where the worktree was created on the host. For `ci-ratchet`, report:
- Container URL (for observation via ttyd)
- Branch: `work/<name>`
- Merge-queue started: yes/no
- "Watch CI at: https://github.com/<owner>/<repo>/actions"

On failure show the error.

**Examples:**
```bash
# Create with auto-detected profile name
/reflex:brainbox create

# Create with custom name
/reflex:brainbox create myproject

# Create with a specific role
/reflex:brainbox create orchestrator --role supervisor
/reflex:brainbox create pr-guard --role merge-queue
/reflex:brainbox create task-1 --role worker

# Create with additional volume mounts
/reflex:brainbox create myproject --mount /data:/workspace/data:ro

# Create with repo (prompts for mode if not specified)
/reflex:brainbox create myproject --repo /path/to/ink-bunny

# Create with explicit worktree-mount mode
/reflex:brainbox create myproject --repo /path/to/ink-bunny --repo-mode worktree-mount --branch fix/my-changes

# Create with fresh clone
/reflex:brainbox create myproject --repo git@github.com:neverprepared/ink-bunny --repo-mode clone --branch feature/new-thing

# Create with clone + inner worktree
/reflex:brainbox create myproject --repo git@github.com:neverprepared/ink-bunny --repo-mode clone-worktree --branch feature/new-thing

# Create ci-ratchet worker (autonomous: clones, completes task, opens PR; CI merges it)
/reflex:brainbox create fix-highs \
  --repo git@github.com:neverprepared/ink-bunny \
  --repo-mode ci-ratchet \
  --task "Fix BB-H4, BB-H7, BB-H9 from tasks/code-review.md"

# ci-ratchet without auto-starting merge-queue
/reflex:brainbox create fix-highs \
  --repo git@github.com:neverprepared/ink-bunny \
  --repo-mode ci-ratchet \
  --task "Fix the HIGH-priority items from tasks/code-review.md" \
  --no-merge-queue
```

### `/reflex:brainbox query`

Send a query to a running container and get the response via tmux. This is the primary way to interact with containers for orchestration workflows.

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
URL_FILE="${CLAUDE_DIR}/reflex/.brainbox-url"

if [ ! -f "$URL_FILE" ]; then
  echo "Brainbox is not connected. Start it first:"
  echo "  /reflex:brainbox start"
  exit 1
fi

URL=$(cat "$URL_FILE")

# Parse arguments: session_name and query text
# Format: /reflex:brainbox query <session-name> <query-text>
SESSION_NAME=$(echo "$ARG" | awk '{print $1}')
QUERY=$(echo "$ARG" | cut -d' ' -f2-)

if [ -z "$SESSION_NAME" ] || [ -z "$QUERY" ]; then
  echo "Usage: /reflex:brainbox query <session-name> <query>"
  echo ""
  echo "Examples:"
  echo "  /reflex:brainbox query test-1 'What files are in the current directory?'"
  echo "  /reflex:brainbox query myproject 'Run the tests'"
  exit 1
fi

# Build JSON payload
PAYLOAD=$(jq -n --arg q "$QUERY" '{prompt: $q, timeout: 300}')

API_KEY=$(curl -sf "${URL}/api/auth/key" --max-time 3 2>/dev/null | jq -r '.key // empty' 2>/dev/null || true)

RESULT=$(curl -sf -X POST "${URL}/api/sessions/${SESSION_NAME}/query" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: ${API_KEY}" \
  -d "$PAYLOAD" --max-time 320 2>&1)

echo "$RESULT"
```

Show the result to the user. On success, display the container's response. On timeout or error, show the error message.

**Examples:**
```bash
# Query a container
/reflex:brainbox query test-1 Create a Python script that prints hello world

# Run commands
/reflex:brainbox query myproject List all files in the workspace
```

### `/reflex:brainbox ratchet`

Launch a **Brownian Ratchet** session: a fire-and-forget autonomous task that runs to completion and then auto-deletes itself. Claude Teams is enabled automatically so the agent can spawn sub-agents in parallel.

The ratchet metaphor: the agent explores the problem space freely (Brownian motion), but progress only moves forward — completed work is committed to git and cannot be undone (the ratchet clicks). Failed branches are discarded. The session self-destructs when the task is done.

**Flags:**
- First non-flag argument — task description (required; re-prompt if blank)
- `--name <name>` — Session name (default: `ratchet-<timestamp>`)
- `--repo <url-or-path>` — Repo to work in (required)
- `--branch <name>` — Working branch (default: `work/<name>`)
- `--backend docker|utm` — Execution backend (default: `docker`)
- `--vm-template <name>` — UTM template (required if `--backend utm`)
- `--guest-os macos|linux|windows` — Guest OS for UTM (default: `linux`)
- `--no-merge-queue` — Skip auto-starting merge-queue agent

**If `$ARG` is empty**, run the interactive wizard:
1. Ask for task description (required)
2. Ask for repo URL or path (required)
3. Ask for session name (default: `ratchet-<timestamp>`)
4. Ask: `Backend? docker (fast, default) / utm (full VM)`
5. If utm: ask for vm-template
6. Ask: `Auto-start merge-queue to merge passing PRs? [Y/n]`
7. Confirm and proceed

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
URL_FILE="${CLAUDE_DIR}/reflex/.brainbox-url"

if [ ! -f "$URL_FILE" ]; then
  echo "Brainbox is not connected. Start it first:"
  echo "  /reflex:brainbox start"
  exit 1
fi

URL=$(cat "$URL_FILE")
PROFILE="${WORKSPACE_PROFILE:-}"
WS_HOME="${WORKSPACE_HOME:-}"
TIMESTAMP=$(date +%s)

# Parse flags
TASK=$(echo "$ARG" | sed -E 's/--[a-z-]+ [^ ]+//g' | xargs)
NAME=$(echo "$ARG" | grep -oE -- '--name [^ ]+' | sed 's/--name //' | head -1)
NAME="${NAME:-ratchet-${TIMESTAMP}}"
REPO_URL=$(echo "$ARG" | grep -oE -- '--repo [^ ]+' | sed 's/--repo //' | head -1)
BRANCH=$(echo "$ARG" | grep -oE -- '--branch [^ ]+' | sed 's/--branch //' | head -1)
BACKEND=$(echo "$ARG" | grep -oE -- '--backend [^ ]+' | sed 's/--backend //' | head -1)
BACKEND="${BACKEND:-docker}"
VM_TEMPLATE=$(echo "$ARG" | grep -oE -- '--vm-template [^ ]+' | sed 's/--vm-template //' | head -1)
GUEST_OS=$(echo "$ARG" | grep -oE -- '--guest-os [^ ]+' | sed 's/--guest-os //' | head -1)
GUEST_OS="${GUEST_OS:-linux}"
NO_MERGE_QUEUE=$(echo "$ARG" | grep -c -- '--no-merge-queue' || true)
START_MQ="true"
[ "$NO_MERGE_QUEUE" -gt 0 ] && START_MQ="false"

API_KEY=$(curl -sf "${URL}/api/auth/key" --max-time 3 2>/dev/null | jq -r '.key // empty' 2>/dev/null || true)

# Build repo object
REPO_OBJ=$(jq -n \
  --arg url "$REPO_URL" \
  --arg branch "$BRANCH" \
  --arg task "$TASK" \
  --argjson smq "$START_MQ" \
  '{url: $url, mode: "ci-ratchet", container_path: "/home/developer/workspace/repo", task: $task, start_merge_queue: $smq} +
   (if $branch != "" then {branch: $branch} else {} end)')

PAYLOAD=$(jq -n \
  --arg name "$NAME" \
  --arg profile "$PROFILE" \
  --arg ws_home "$WS_HOME" \
  --arg backend "$BACKEND" \
  --arg vm_template "$VM_TEMPLATE" \
  --arg guest_os "$GUEST_OS" \
  --argjson repo "$REPO_OBJ" \
  '{name: $name, role: "worker", backend: $backend, repo: $repo} +
   (if $profile != "" then {workspace_profile: $profile} else {} end) +
   (if $ws_home != "" then {workspace_home: $ws_home} else {} end) +
   (if $vm_template != "" then {vm_template: $vm_template} else {} end) +
   (if $backend == "utm" then {guest_os: $guest_os} else {} end)')

RESULT=$(curl -sf -X POST "${URL}/api/create" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: ${API_KEY}" \
  -d "$PAYLOAD" --max-time 60 2>&1)

echo "$RESULT"
```

On success, report:
- Session name and backend
- Task description (truncated to 80 chars if long)
- Branch: `work/<name>` (or custom)
- Merge-queue: started / skipped
- "Session will auto-delete when the task completes."
- For Docker: container URL for observation (ttyd)
- For UTM: SSH port

**Examples:**
```bash
# Minimal — wizard prompts for task + repo
/reflex:brainbox ratchet

# Fully specified
/reflex:brainbox ratchet --task "Fix all HIGH-priority items from tasks/code-review.md" \
  --repo git@github.com:neverprepared/ink-bunny \
  --name fix-highs

# UTM macOS ratchet
/reflex:brainbox ratchet --task "Port the auth middleware to Swift" \
  --repo git@github.com:myorg/myapp \
  --backend utm --vm-template brainbox-macos-template --guest-os macos
```

---

### `/reflex:brainbox orchestrate`

Launch a long-running **Orchestrator** session: a persistent supervisor agent that manages a fleet of ratchet workers. The orchestrator lives across many task cycles, spawns workers as needed via Claude Teams, and tracks fleet-wide progress. Use UTM for stability on long projects.

The orchestrator is NOT auto-cleaned up — it requires explicit deletion via `/reflex:brainbox delete`.

**Flags:**
- First non-flag argument — session name (default: `orchestrator`)
- `--repo <url-or-path>` — Repo the orchestrator manages
- `--task <description>` — High-level goal / project description
- `--backend docker|utm` — Backend (default: `utm`)
- `--vm-template <name>` — UTM template (default: `brainbox-macos-template` if utm)
- `--guest-os macos|linux|windows` — Guest OS for UTM (default: `linux`)

**If `$ARG` is empty**, run the interactive wizard:
1. Ask for session name (default: `orchestrator`)
2. Ask for repo URL or path (required)
3. Ask for high-level goal/task for the orchestrator
4. Ask: `Backend? utm (persistent, default) / docker`
5. If utm: confirm vm-template (default: `brainbox-macos-template`)
6. Confirm and proceed

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
URL_FILE="${CLAUDE_DIR}/reflex/.brainbox-url"

if [ ! -f "$URL_FILE" ]; then
  echo "Brainbox is not connected. Start it first:"
  echo "  /reflex:brainbox start"
  exit 1
fi

URL=$(cat "$URL_FILE")
PROFILE="${WORKSPACE_PROFILE:-}"
WS_HOME="${WORKSPACE_HOME:-}"

# Parse flags
NAME=$(echo "$ARG" | sed -E 's/^([^ -][^ ]*).*/\1/' | grep -v '^--' | head -1)
NAME="${NAME:-orchestrator}"
REPO_URL=$(echo "$ARG" | grep -oE -- '--repo [^ ]+' | sed 's/--repo //' | head -1)
TASK=$(echo "$ARG" | grep -oE -- '--task .+' | sed 's/--task //' | head -1)
BACKEND=$(echo "$ARG" | grep -oE -- '--backend [^ ]+' | sed 's/--backend //' | head -1)
BACKEND="${BACKEND:-utm}"
VM_TEMPLATE=$(echo "$ARG" | grep -oE -- '--vm-template [^ ]+' | sed 's/--vm-template //' | head -1)
# Default vm-template for utm backend
[ "$BACKEND" = "utm" ] && VM_TEMPLATE="${VM_TEMPLATE:-brainbox-macos-template}"
GUEST_OS=$(echo "$ARG" | grep -oE -- '--guest-os [^ ]+' | sed 's/--guest-os //' | head -1)
GUEST_OS="${GUEST_OS:-linux}"

API_KEY=$(curl -sf "${URL}/api/auth/key" --max-time 3 2>/dev/null | jq -r '.key // empty' 2>/dev/null || true)

PAYLOAD=$(jq -n \
  --arg name "$NAME" \
  --arg role "supervisor" \
  --arg profile "$PROFILE" \
  --arg ws_home "$WS_HOME" \
  --arg backend "$BACKEND" \
  --arg vm_template "$VM_TEMPLATE" \
  --arg guest_os "$GUEST_OS" \
  --arg task "$TASK" \
  '{name: $name, role: $role, backend: $backend} +
   (if $profile != "" then {workspace_profile: $profile} else {} end) +
   (if $ws_home != "" then {workspace_home: $ws_home} else {} end) +
   (if $vm_template != "" then {vm_template: $vm_template} else {} end) +
   (if $backend == "utm" then {guest_os: $guest_os} else {} end) +
   (if $task != "" then {task: $task} else {} end)')

if [ -n "$REPO_URL" ]; then
  REPO_OBJ=$(jq -n --arg url "$REPO_URL" '{url: $url, mode: "clone", container_path: "/home/developer/workspace/repo"}')
  PAYLOAD=$(echo "$PAYLOAD" | jq --argjson repo "$REPO_OBJ" '. + {repo: $repo}')
fi

RESULT=$(curl -sf -X POST "${URL}/api/create" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: ${API_KEY}" \
  -d "$PAYLOAD" --max-time 120 2>&1)

echo "$RESULT"
```

On success, report:
- Session name and backend (UTM or Docker)
- Role: supervisor
- Goal/task if provided
- "Session is persistent — delete explicitly with: /reflex:brainbox delete <name>"
- For UTM: SSH port and VM name
- For Docker: container URL

**Examples:**
```bash
# Wizard
/reflex:brainbox orchestrate

# Fully specified (UTM, default)
/reflex:brainbox orchestrate ink-bunny-supervisor \
  --repo git@github.com:neverprepared/ink-bunny \
  --task "Drive all open HIGH-priority issues to merged PRs"

# Docker orchestrator (lighter weight)
/reflex:brainbox orchestrate sprint-supervisor \
  --backend docker \
  --repo git@github.com:myorg/myapp \
  --task "Implement the features from sprint-42-tasks.md"
```

---

### `/reflex:brainbox delete`

Explicitly delete a session. For Docker sessions, stops and removes the container. For UTM sessions, stops and deletes the VM. Use this to clean up interactive or orchestrator sessions when done.

Ratchet sessions self-delete on completion — only use this to force-delete a stalled ratchet.

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
URL_FILE="${CLAUDE_DIR}/reflex/.brainbox-url"

if [ ! -f "$URL_FILE" ]; then
  echo "Brainbox is not connected."
  exit 1
fi

URL=$(cat "$URL_FILE")
NAME=$(echo "$ARG" | awk '{print $1}')

if [ -z "$NAME" ]; then
  echo "Usage: /reflex:brainbox delete <session-name>"
  exit 1
fi

API_KEY=$(curl -sf "${URL}/api/auth/key" --max-time 3 2>/dev/null | jq -r '.key // empty' 2>/dev/null || true)

RESULT=$(curl -sf -X POST "${URL}/api/delete" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: ${API_KEY}" \
  -d "$(jq -n --arg name "$NAME" '{name: $name}')" --max-time 60 2>&1)

echo "$RESULT"
```

Report success or error. On success: "Session `<name>` deleted."

**Examples:**
```bash
/reflex:brainbox delete myproject
/reflex:brainbox delete ink-bunny-supervisor
/reflex:brainbox delete ratchet-1714000000
```

---

### `/reflex:brainbox health`

Check health status of observability services.

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
URL_FILE="${CLAUDE_DIR}/reflex/.brainbox-url"

if [ ! -f "$URL_FILE" ]; then
  echo "Brainbox is not connected. Start it first:"
  echo "  /reflex:brainbox start"
  exit 1
fi

URL=$(cat "$URL_FILE")

echo "## Observability Health"
echo ""

# LangFuse
LANGFUSE=$(curl -sf "${URL}/api/langfuse/health" --max-time 3 2>/dev/null || echo '{"healthy":false}')
LANGFUSE_STATUS=$(echo "$LANGFUSE" | jq -r 'if .healthy then "Online" else "Offline" end')
echo "**LangFuse:** ${LANGFUSE_STATUS}"
```

### `/reflex:brainbox config`

Show or set configuration values. With no extra arguments, show current config. With key=value pairs, update the config file.

**Show config:**
```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CONFIG_FILE="${CLAUDE_DIR}/reflex/brainbox.json"

if [ -f "$CONFIG_FILE" ]; then
  echo "## Brainbox Config"
  echo '```json'
  cat "$CONFIG_FILE" | jq .
  echo '```'
else
  echo "No config file. Using defaults:"
  echo '```json'
  echo '{"url": "http://127.0.0.1:9999", "autostart": true}'
  echo '```'
fi
echo ""
echo "Config file: ${CONFIG_FILE}"
echo ""
echo "Set values with:"
echo "  /reflex:brainbox config url=http://host:port"
echo "  /reflex:brainbox config autostart=false"
```

**Set config (e.g. `url=http://remote:8080`):**

Parse the key=value argument. Read the existing config (or start with defaults), update the key, and write back:

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CONFIG_FILE="${CLAUDE_DIR}/reflex/brainbox.json"
mkdir -p "${CLAUDE_DIR}/reflex"

# Read existing or defaults
if [ -f "$CONFIG_FILE" ]; then
  CONFIG=$(cat "$CONFIG_FILE")
else
  CONFIG='{"url": "http://127.0.0.1:9999", "autostart": true}'
fi

# Apply the update — the key and value come from the user's argument
# e.g. for "url=http://remote:8080":
echo "$CONFIG" | jq --arg key "$KEY" --arg val "$VALUE" '.[$key] = (if $val == "true" then true elif $val == "false" then false else $val end)' > "$CONFIG_FILE"

echo "Updated $KEY = $VALUE"
cat "$CONFIG_FILE" | jq .
```

### No argument or invalid

If no argument or invalid argument provided, show usage:

```
Usage: /reflex:brainbox <start|stop|status|create|ratchet|orchestrate|delete|query|dashboard|health|config>

Manage the brainbox API for sandboxed dev environments (Docker containers and UTM VMs).

Session modes:
  create       Interactive session — user-facing, manual lifecycle
               Syntax: create [name] [--role <role>] [--backend docker|utm] [--vm-template <t>]
                              [--mount /host:/container[:mode]] [--repo <url>] [--repo-mode <mode>]
  ratchet      Brownian Ratchet — fire-and-forget autonomous task, auto-deletes on completion
               Syntax: ratchet [--task <desc>] [--repo <url>] [--name <n>] [--backend docker|utm]
  orchestrate  Long-running Orchestrator — persistent supervisor that spawns ratchet workers
               Syntax: orchestrate [name] [--repo <url>] [--task <goal>] [--backend utm|docker]
  delete       Delete a session (Docker: stop+rm; UTM: stop+delete VM)
               Syntax: delete <session-name>

Management:
  start      Start the API locally
  stop       Stop a locally auto-started API
  status     Show connection info and running sessions
  query      Send a query to a running session
             Syntax: query <session-name> <query-text>
  dashboard  Open the dashboard in browser
  health     Check observability services (LangFuse)
  config     Show/set configuration (url, autostart)

Roles (for create): developer (default), supervisor, worker, reviewer, merge-queue, pr-shepherd
Repo modes (for create): worktree-mount, clone, clone-worktree, ci-ratchet
Backends: docker (default for interactive/ratchet), utm (default for orchestrate)

Claude Teams is automatically enabled for ratchet and orchestrate modes.

Configuration:
  Config file: ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/reflex/brainbox.json
  Default API port: 9999

  Environment variable overrides:
    BRAINBOX_URL        API endpoint (local or remote)
    BRAINBOX_AUTOSTART  true/false (default: true)

Examples:
  /reflex:brainbox create                                          # interactive wizard
  /reflex:brainbox create myproject --backend utm --vm-template brainbox-macos-template --guest-os macos
  /reflex:brainbox ratchet --task "Fix HIGH items from tasks/code-review.md" --repo git@github.com:org/repo
  /reflex:brainbox orchestrate sprint-super --repo git@github.com:org/repo --task "Drive sprint 42 to done"
  /reflex:brainbox delete myproject
  /reflex:brainbox query test-1 'List all files in the workspace'
  /reflex:brainbox status
  /reflex:brainbox config url=http://remote:9999
```
