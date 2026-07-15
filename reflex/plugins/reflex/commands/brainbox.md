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
- **Brownian Ratchet** — fire-and-forget autonomous task: the worker clones the repo, implements the task, opens a PR, and drives GitHub CI to green (fixing failures on the same branch), then stops with the PR open. Progress only moves forward — every increment is CI-gated (the ratchet). Merging is out of scope (orchestrate it downstream). Auto-cleans on completion.
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

Supports agent role selection via `--role` and additional volume mounts via `--mount`. To run an autonomous repo task (clone → task → PR → fix CI), use `/reflex:brainbox ratchet` instead.

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

> **Note — attaching a repo.** `create` makes an interactive session; it does
> not clone repos (the repo/worktrees subsystem was removed). To make a host
> repo available, mount it with `--mount /host/repo:/container/path`. For an
> **autonomous** clone→task→PR→fix-CI flow, use `/reflex:brainbox ratchet`
> instead — the worker clones the repo itself, no host checkout needed.

**Step 3 — Extra volume mounts**

Ask:
```
Add extra volume mounts? Enter as /host/path:/container/path[:ro], one per line.
Leave blank and press Enter when done.
```
Collect until a blank line is entered.

**Step 4 — Confirm**

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
- `--mount /host/path:/container/path[:mode]` — Additional volume mounts (can be specified multiple times). To make a host repo available in the session, mount it here.
- `--backend docker|utm` — Execution backend (default: `docker`). Use `utm` for macOS/Windows guest OS or long-running workloads that need a full VM.
- `--vm-template <name>` — UTM VM template to clone (required when `--backend utm`; e.g. `brainbox-macos-template`)
- `--guest-os macos|linux|windows` — Guest OS for UTM VMs (default: `linux`)

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

API_KEY=$(curl -sf "${URL}/api/auth/key" --max-time 3 2>/dev/null | jq -r '.key // empty' 2>/dev/null || true)

RESULT=$(curl -sf -X POST "${URL}/api/create" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: ${API_KEY}" \
  -d "$PAYLOAD" --max-time 60 2>&1)

echo "$RESULT"
```

Show the result: on success report the container URL and detected profile. On failure show the error.

**Examples:**
```bash
# Create with auto-detected profile name
/reflex:brainbox create

# Create with custom name
/reflex:brainbox create myproject

# Create with a specific role
/reflex:brainbox create orchestrator --role supervisor
/reflex:brainbox create task-1 --role worker

# Create with additional volume mounts (e.g. make a host repo available)
/reflex:brainbox create myproject --mount /path/to/ink-bunny:/home/developer/workspace/repo

# For an autonomous clone → task → PR → fix-CI flow, use ratchet instead:
/reflex:brainbox ratchet --task "Fix HIGH items from tasks/code-review.md" \
  --repo git@github.com:neverprepared/ink-bunny
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

Launch a **ci-ratchet worker**: a fire-and-forget autonomous task. The worker clones the repo (which does **not** need to exist on this machine), implements the task, opens a PR, watches GitHub CI, and **fixes failures until CI is green — then stops with the PR open.**

The ratchet metaphor (from [multiclaude](https://github.com/dlorenc/multiclaude) by Dan Lorenc et al.): the agent explores freely, but progress only moves forward — every increment is gated by CI, so nothing regresses. **Merging is intentionally not part of the ratchet** — the clean stop point (open PR, green CI) is the handoff for whatever merges downstream (an event rule, a human, or a separate orchestration).

This posts to `POST /api/ratchet`, a thin convenience over the hub task API: it queues one `worker` task with `repo_url`. The worker clones agentically via `$BRAINBOX_REPO_URL` using the mounted profile credentials — there is no daemon-side clone and no `repo` object on `/api/create` (that subsystem was removed).

**Flags:**
- First non-flag argument — task description (required; re-prompt if blank)
- `--repo <url>` — Git remote the worker clones, HTTPS or SSH (required)
- `--branch <name>` — PR branch hint (optional; the worker picks a unique branch otherwise)
- `--backend docker|utm` — Execution backend (default: `docker`)
- `--runner <name>` — Pin to a specific runner (optional; auto-selected otherwise)

**If `$ARG` is empty**, run the interactive wizard:
1. Ask for task description (required)
2. Ask for repo URL (required)
3. Ask: `Backend? docker (fast, default) / utm (full VM)`
4. Confirm and proceed

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
TASK=$(echo "$ARG" | sed -E 's/--[a-z-]+ [^ ]+//g' | xargs)
REPO_URL=$(echo "$ARG" | grep -oE -- '--repo [^ ]+' | sed 's/--repo //' | head -1)
BRANCH=$(echo "$ARG" | grep -oE -- '--branch [^ ]+' | sed 's/--branch //' | head -1)
BACKEND=$(echo "$ARG" | grep -oE -- '--backend [^ ]+' | sed 's/--backend //' | head -1)
BACKEND="${BACKEND:-docker}"
RUNNER=$(echo "$ARG" | grep -oE -- '--runner [^ ]+' | sed 's/--runner //' | head -1)

if [ -z "$REPO_URL" ]; then
  echo "A repo is required. Pass --repo <git-url>."
  exit 1
fi

API_KEY=$(curl -sf "${URL}/api/auth/key" --max-time 3 2>/dev/null | jq -r '.key // empty' 2>/dev/null || true)

PAYLOAD=$(jq -n \
  --arg repo "$REPO_URL" \
  --arg task "$TASK" \
  --arg branch "$BRANCH" \
  --arg profile "$PROFILE" \
  --arg ws_home "$WS_HOME" \
  --arg backend "$BACKEND" \
  --arg runner "$RUNNER" \
  '{repo_url: $repo, task: $task, backend: $backend} +
   (if $branch != "" then {branch: $branch} else {} end) +
   (if $profile != "" then {workspace_profile: $profile} else {} end) +
   (if $ws_home != "" then {workspace_home: $ws_home} else {} end) +
   (if $runner != "" then {runner: $runner} else {} end)')

RESULT=$(curl -sf -X POST "${URL}/api/ratchet" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: ${API_KEY}" \
  -d "$PAYLOAD" --max-time 60 2>&1)

echo "$RESULT"
```

The response is `{"success": true, "job_id": ..., "task_id": ..., "repo_url": ...}`. The task starts **PENDING**; the scheduler dispatches it to a container within seconds. On success, report:
- Task ID and job ID
- Task description (truncated to 80 chars if long)
- Repo and (if given) branch hint
- "The worker will open a PR and drive it until CI is green, then stop. Merge it yourself or via downstream orchestration."
- "Track it: `/reflex:brainbox query` or poll `GET /api/hub/tasks/<task_id>` for status and the assigned session (for ttyd observation)."

**Examples:**
```bash
# Minimal — wizard prompts for task + repo
/reflex:brainbox ratchet

# Fully specified
/reflex:brainbox ratchet --task "Fix all HIGH-priority items from tasks/code-review.md" \
  --repo git@github.com:neverprepared/ink-bunny

# UTM macOS ratchet, pinned to a runner
/reflex:brainbox ratchet --task "Port the auth middleware to Swift" \
  --repo git@github.com:myorg/myapp \
  --backend utm --runner mac-studio
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

# Fold the repo into the supervisor's goal. /api/create has no repo object
# (the repo/worktrees subsystem was removed); the supervisor reads the repo
# from its task and hands it to each worker it spawns as repo_url.
if [ -n "$REPO_URL" ]; then
  if [ -n "$TASK" ]; then
    TASK="Repository: ${REPO_URL}"$'\n\n'"${TASK}"
  else
    TASK="Manage and drive work on the repository: ${REPO_URL}"
  fi
fi

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
                              [--mount /host:/container[:mode]]
  ratchet      ci-ratchet worker — fire-and-forget: clones repo, does task, opens PR,
               drives CI to green, then stops with the PR open (no auto-merge)
               Syntax: ratchet <task> --repo <url> [--branch <n>] [--backend docker|utm] [--runner <n>]
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

Roles (for create): developer (default), supervisor, worker, reviewer
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
