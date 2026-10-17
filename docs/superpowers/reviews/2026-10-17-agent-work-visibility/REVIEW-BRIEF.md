# Review brief — agent work visualisation for phantom-ink

## The goal, in the user's own words

> "what I'm looking for in the wheel is an up front quick and easy to navigate set of
> running tasks. It would be good to see the task, perhaps connect to a session or see a
> brief summary of what the task is doing. any data it touches would be nice to see. as an
> audit, it would be nice to see what happened in a complete session tree"

Earlier, unprompted: *"active/live agents and their subagents as a default. maybe a
toggleable completed jobs"*, *"the wheel should be draggable too (rotating)"*,
*"I like looking at data in different forms"*, and — correcting a wrong assumption of mine
— *"fleet work is not workspace-less, it might be independent, but fleet tasks use profile
credentials"*. Also: *"I think I wanted session isolation"*.

Two jobs, then: **operate** (navigate what is running now, know what it is doing, jump into
its session) and **audit** (what happened across a finished session tree). Profile scoping
matters: the user's standing rule is that every feature is profile-scoped and cross-profile
leakage is a bug.

## What the platform actually contains (all measured, not assumed)

Source: `GET /api/agent_state?limit=500` on api.neverprepared.com, plus `/api/brain/*`.

**Liveness is near-empty.** `/api/sessions` → 0. `/api/hub/tasks?status=running` → 0.
`status=active` → 2 rows, both `runner.heartbeat`. Of 500 rows: done 482, failed 16,
active 2. The fleet is idle most of the time.

**Hierarchy, as recorded:** 403 roots, 96 at depth 1, **1** at depth 2. Max fan-out 6.
Edge kinds present: `session→session-cmd` 75, `hub-task→rule-exec` 16,
`hub-task→action` 4, `session-cmd→action` 1, `llm→action` 1, `hub-task→hub-task` **1**.
Most of those are an object and its own UI affordance, not work and subwork.
`runner-work` is **295 rows (59%)** — the actual fleet work — and is **always a root**.

**Session isolation is partly free.** Sessions are 53 rows, **all roots, none with a
parent**. Every one of the 38 `hub-task` rows carries `metadata.session_name`, and 37 of 38
resolve to a session inside the window. So `hub-task→session` is derivable client-side with
no backend change: roots 403→366, depth-2 nodes 1→20, parents-with-children 38→75.

**The last level is not derivable.** `runner-work` metadata is only
`{kind, runner, work_id}` — no session, no task. Giving it a `parent_id` pointing at its
dispatching `hub-task` yields `session → hub-task → runner-work`: roots **403→71**,
depth-2 nodes **1→307**, max fan-out 6→11.

**Profile attribution.** Only 177/500 rows carry a `workspace`; `runner-work` carries none
at all, though it runs with a profile's credentials. With a child inheriting its parent's
profile, the same one field takes attribution from **35% → 94%** (470/500). Profiles seen:
`personal` 137, `lakeview` 40, no `gsa`. NOTE: the app today lets workspace-less envelopes
*pass* the profile filter, so those 295 rows would appear under every profile at once —
logged as a known bug (`applyAgentEvent`, StreamPanel).

**Field fill-rates.** `title`/`subtitle`/`type`/`status`/`source`/`start_at`/`end_at`/
`tags`/`metadata` are 100% filled. **`description` is 0% filled across all 500 rows** —
the field that would say what a job is doing is unused. There is no `current_activity`
anywhere. `hub-task` metadata is rich: `agent_name`, `runner_name`, `backend`,
`session_name`, `attempts`, `max_attempts`.

**In-session subagents do not exist to the platform.** The 28 subagents that built a feature
in this very session produced zero platform records. Nothing models "an agent spawned
another agent".

**Memory provenance is absent.** `/api/brain/graph` gives 400 nodes / 907 edges, but every
edge is record↔record (`similar` 893, `supersedes` 14). A record carries
sha/title/topic/kind/memory_type/tags/reliability/created_at/superseded and **no session,
task or agent id**. `/links`, `/audit`, `/record_links`, `/provenance` all 404. Writes are
attributable only by convention — a `task_summary`'s title is its task's goal, covering
88/400 records (~22%). **Reads are not logged at all**, and reads are most of "touched".

## Existing app surfaces that overlap

- There is a **planned but unbuilt** StreamPanel `active` tab: "rename `live`→`logs`, add an
  active tab tracking running hub agents with terminal/focus shortcuts and a per-agent
  'what is happening' line." Its data source is already identified (`hubTasks` filtered to
  running, profile-scoped). Two decisions were left open: what `focus` means for a
  container agent, and whether the activity line is frontend SSE aggregation or a backend
  `current_activity` field.
- `StreamPanel.svelte` is a **1430-line monolith**; its own improvement note says extract
  components first. It also has the workspace-filter leak above.
- A **terminal slide-out already exists** in `SessionsPanel.svelte` (ttyd); the TODO says to
  lift it into a shared component — this would be its second consumer.
- `MemoryGraphPanel` already renders the brain's similar/supersedes graph.
- `DashboardPanel` exposes `hubTasks` via a store.

## The prototype as it stands

A published artifact with: 5 scopes (this session 38 / platform live 2 / as-recorded 500 /
session-isolated 500 / fully-parented 500); a profile filter with inheritance; colour-by
status or profile; and three views over the same filtered set — **list** (when, task
indented by depth, kind, status, profile, took, kids, session), **tree** (d3 tidy tree,
horizontal, depth on x, siblings sorted by start time so reading order is chronological),
and **radial** (drag to rotate). Click any node for a detail panel showing every field the
row holds, plus an explicit line naming what that row *cannot* tell you.

Honest limits of the prototype: the "this session" scope is assembled from conversation
history, not telemetry; its middle layer is a task grouping invented for readability, not a
session boundary; the session column is a labelled chip, not a working Connect button.

## What I want from you

Not a restatement. Specific, prioritised improvements that move this toward the stated
goal, with the cheapest high-value change named first, and anything you think is a dead end
called out as such. Disagree with the current design where you think it is wrong — the
author has already been wrong several times in this session and would rather be corrected
than agreed with.
