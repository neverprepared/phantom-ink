---
name: phantom-router
description: Operational reference for the phantom-router MCP — launch and drive container agent sessions, submit autonomous hub tasks, fire ratchet (clone→PR→fix-CI→stop) workers, and run playbooks. Use when starting/stopping/querying sessions, submitting or monitoring tasks, running a CI-ratchet on a repo, orchestrating multi-step work, or identifying which running sessions are ratchet/worker runs.
---

# phantom-router: the session & task plane

> phantom-router owns the *execution plane* — the containers that run Claude Code. The MCP is a thin HTTP client over the router's REST API. Every call is **profile-scoped**: the server is pinned to one workspace profile (`CL_WORKSPACE_PROFILE`), that profile is forced onto every create/submit, and by-name operations on a session owned by another profile are refused. You never pass an API key — the wrapper authenticates.

## Two execution shapes

phantom-router runs work as either a **session** or a **task**. Pick by lifetime and control:

| | Session (`create_session`) | Task (`submit_task`) |
|---|---|---|
| Shape | A named container you drive turn-by-turn | Fire-and-forget autonomous worker |
| Lifetime | Persistent until you stop it | Transient — self-stops, container removed on completion |
| You interact via | `query_session`, `exec_session` | `list_tasks`, `get_task`, `get_message_log` |
| Use for | Interactive dev, orchestration, long-lived roles | "Do X and open a PR" without babysitting |

### Sessions — interactive containers
```
create_session(name="feX", role="developer", volume="/host/repo:/work")
  → {success, backend:"docker", url:"http://localhost:7681"}   # web terminal
query_session(name="feX", prompt="run the tests and summarize failures", timeout=300)
exec_session(name="feX", command="pytest -q")                  # raw shell, no LLM
push_config(name="feX")   # re-inject ~/.claude (plugins/skills/hooks) into a live container
stop_session / start_session / delete_session(name="feX")
```
Container is named `{role}-{name}` (e.g. `developer-feX`). Roles: `developer` (default, interactive), `supervisor` (orchestrates, spawns workers), `worker` (does one task + PR, transient), `reviewer` (reviews a PR, transient), `merge-queue` / `pr-shepherd` (persistent, auto-restart). Persistent roles auto-restart on failure; transient roles remove their container when done.

### Tasks — autonomous workers
```
submit_task(description="Add retry to the S3 client", agent_name="worker", repo_url="https://github.com/org/repo")
  → a Task (PENDING); scheduler dispatches to a container within seconds
get_task(task_id) / list_tasks(status="running")   # poll; task carries session_name once assigned
cancel_task(task_id)
```
A `worker` clones agentically using the mounted profile credentials, implements the task, and opens a PR. `supervisor` tasks spawn their own workers (pass the supervisor's task id as `job_id` when fanning out).

## Ratchet (CI-ratchet) — the clone→PR→fix-CI→stop loop

A **ratchet** is a `worker` task pointed at a repo whose role prompt: clones `repo_url`, implements `task`, opens a PR, watches GitHub CI, **fixes failures until green**, then **stops with the PR open**. There is **no auto-merge** — a green CI + open PR is the clean handoff for downstream merge orchestration (an event rule or a human).

**There is no `ratchet` MCP tool.** The REST endpoint `POST /api/ratchet` is a semantic alias over `submit_task` with `agent_name="worker"` + `repo_url`. From the MCP, fire a ratchet by calling `submit_task` directly:
```
submit_task(
  description="Migrate config loading to pydantic-settings.\n\nOpen the PR from a branch named `ratchet/pydantic-config`.",
  agent_name="worker",
  repo_url="https://github.com/org/repo",
)
```
The clone→PR→CI-fix loop is driven by the **worker role prompt**, and only engages when the worker has a `repo_url` to clone. Append a branch hint to the description (as above) if you want a named PR branch; otherwise the worker picks a unique one.

### Identifying ratchet / worker sessions

A ratchet has no dedicated flag — its signature is **`agent_name == "worker"` AND `repo_url` is set**. To find live ratchet runs:
- `list_tasks(status="running")` → keep entries where `agent_name == "worker"` and `repo_url` is non-null. Each such Task carries `session_name` (the worker container), `job_id`, and `status`.
- `list_sessions()` → containers named **`worker-*`** are transient task/ratchet sessions; `developer-*` are interactive; `supervisor-*` / `merge-queue-*` / `pr-shepherd-*` are orchestration/persistent.
- REST-initiated ratchets emit a **`session.ratchet`** audit event (visible on the event bus / Stream), distinguishing them from plain `submit_task` workers.
- A `worker` task submitted **without** a `repo_url` is a generic worker, **not** a ratchet — it has nothing to clone, so the CI loop never starts.

## Playbooks — sequential multi-step

For ordered multi-step work where each step is its own fresh worker:
```
create_playbook(name="release-cut", markdown="- [ ] bump version\n- [ ] update changelog\n- [ ] tag", workspace_profile="global")
run_playbook(playbook_id)      # steps execute one at a time, each in an isolated worker
get_playbook(playbook_id)      # poll progress;  cancel_playbook to stop after the current step
```
Each `- [ ]` line becomes one step dispatched to a fresh ephemeral worker with the profile's credentials injected. When this MCP is profile-pinned, the `workspace_profile` arg is ignored and the playbook is forced to the pinned profile.

## Monitoring & introspection

- `multiclaude_status()` — one-call summary of the whole hub (agents, tasks, message log).
- `get_hub_state()` — full hub state; `get_message_log(limit)` — inter-agent messages (supervisor→worker, lifecycle events).
- `get_metrics()` — per-container CPU/mem/uptime/trace/error counts.
- `list_sessions()` / `get_session(name)` — session inventory & detail.
- LangFuse: `get_langfuse_session_summary(name)` for a health snapshot, then `get_langfuse_session_traces(name)` → `get_langfuse_trace_detail(trace_id)` to see exactly which tools a session called.
- `list_agents()`, `list_tokens()`, `api_info()`, `get_event_schema()` (live timeline-entry contract).

## Gotchas

- **Profile isolation is enforced server-side.** The pinned profile overrides any `workspace_profile` argument, and by-name ops (`stop`/`query`/`exec`/…) on another profile's session return a guard error instead of acting. Don't try to reach across profiles.
- **Sessions are hard-killed on stop** (`docker stop`) — Claude Code's in-container `SessionEnd` hook does NOT fire for platform sessions. Don't build session-end automation as an in-container hook; the router emits a `session.summary` event at stop instead.
- **Transient vs persistent**: `worker`/`reviewer` remove their container on completion; `supervisor`/`merge-queue`/`pr-shepherd` persist and auto-restart. Choose the role to match the lifetime you want.
- **Ratchet ≠ merge.** It stops at green-CI + open-PR by design. If you need the PR merged, that's a separate merge-queue role or an event rule — the ratchet won't do it.

See the `phantom-brain` skill for the memory plane, and `gateway-access` for reaching profile-scoped third-party tools.
