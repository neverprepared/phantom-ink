# Agent observability — prompt/completion capture and session drill-down

Date: 2026-10-17
Status: design, not yet implemented
Review record: `docs/superpowers/reviews/2026-10-17-agent-work-visibility/`

## Goal

> "an up front quick and easy to navigate set of running tasks. It would be good to
> see the task, perhaps connect to a session or see a brief summary of what the task
> is doing. any data it touches would be nice to see. as an audit, it would be nice
> to see what happened in a complete session tree"
>
> "I do want prompt & completion capture. it would allow me to drill down in the tree
> data we're building. The goal is to see a table, list and click to get more details
> about the session from our agents."

Two jobs. **Operate**: navigate what is running, know what it is doing, attach to its
session. **Audit**: open a finished session and read what happened, down to the prompts
and completions.

## Evidence

Everything below was measured against the live platform on 2026-10-16/17, not inferred.
Figures that contradict earlier working assumptions are called out in *Corrections*.

### What already exists

**`logs-otel-*` in OpenSearch is the richest agent telemetry we have, and the app uses
almost none of it.** `serviceName: claude-code`, ~10k docs/day, **735 distinct sessions**
tracked. Every tool call carries:

`session@id` · `prompt@id` (per turn) · `event@sequence` (ordering) · `tool_name` ·
`tool_use_id` · `duration_ms` · `success` · `error_type` · `decision_source` /
`decision_type` (permission outcome) · `tool_input_size_bytes` /
`tool_result_size_bytes` · **`workspace`** · terminal and user identity.

Tool mix observed: Bash 80,827 · Read 13,518 · mcp_tool 11,547 · Edit 9,524 ·
**Agent 1,688** · AskUserQuestion 1,076 · WebFetch + WebSearch 1,046.

**`metrics-otel-*`** holds 8 `claude_code.*` series: `token.usage` (102,364),
`active_time.total`, `cost.usage` (25,591), `lines_of_code.count`,
`code_edit_tool.decision`, `commit.count`, `pull_request.count`, `session.count`.

**`otel-v1-apm-span-*`** holds ~172k spans with real trace trees
(`traceId`/`spanId`/`parentSpanId`) and `resource.attributes.workspace` — but every span
is infrastructure: `compose` 169,430, `OpenTofu CLI` 2,852, `buildx` 542. `traceGroup`
`cli/ps` alone is 113,038. **Nothing reads this index.**

**The router's LLM plane already emits uniform per-completion records** for every backend
behind one seam, to the envelope bus (Postgres `agent_state`), with:
`profile` · `caller` · `backend` · `model` · `quality` · `allow_paid` · `tokens_in` ·
`tokens_out` · `cost_estimate_usd` · `ok` · `trace_id` · `attempts[]` (the fallback
chain). Implemented backends: `OllamaBackend`, `ClaudeApiBackend`, `ClaudeOAuthBackend`,
`MultiBackend`, `OmniRouteBackend`.

### What does not exist

- **No content anywhere.** Claude Code's OTEL export emits byte *sizes* by design, never
  prompt or completion text. The router records token counts, not text.
- **No local transcripts to fall back on.** `~/.claude/projects` holds exactly one
  `.jsonl` (32K) and not the current session's. Task `.output` files are plain stdout.
- **Codex emits nothing** — `llm/strategy.py:18`: `codex → [codex] # not implemented yet
  → empty registry → LlmError`.
- **opencode (tier0) emits nothing** — no telemetry configuration at all.
- **`Agent` tool events do not name the child.** 1,688 spawns are recorded, with no child
  session id and no agent type. You learn a spawn happened and how long it took.
- **`mcp_tool` does not name the tool**, and `pbrainctl recall` runs under `Bash` with no
  command recorded — so "which memory did this task read" is not answerable from
  telemetry.
- **Two telemetry systems that cannot be joined.** Claude Code → OTEL → OpenSearch; the
  router's LLM plane → envelope bus → Postgres. The router's `trace_id` is a
  self-generated `uuid.uuid4().hex` (`llm/core.py:119`), **not** a W3C trace id, so the
  field named "trace id" on each side cannot correlate with the other.

### Volume, measured

`logs-otel-2026.10.16`: 9,209 docs / 4.1 MB → **472 B/doc**. Recent days 1.2–4.5 MB.
Critically: **73 distinct turns (`prompt@id`) per day across 6 sessions.** Content capture
is per *turn*, so even at 20 KB/turn this adds ~1.5 MB/day against ~4 MB/day already
stored. Volume is not the constraint.

## Decision: capture into OpenSearch, not Langfuse

1. **Langfuse's differentiator is the session drill-down UI, which is the thing being
   built here.** Adopting it means operating a second store for a UI we are replacing.
2. **The join keys already exist.** `session@id` and `prompt@id` are on every tool event.
   Content keyed the same way sits beside the tool timeline in one store — that *is* the
   drill-down.
3. **`workspace` is native on the event**, so profile scoping is correct at the source: no
   inheritance, no derived edges, none of the attribution hazards in the review record.
4. **The app already queries OpenSearch** (three methods), so this extends rather than
   integrates.

Revisit Langfuse if a future agent service emits native GenAI spans richer than what we
capture; it can then be a second OTLP exporter rather than a migration.

## Capture contract

One record per turn, per agent service, keyed to join the existing telemetry:

| Field | Meaning |
|---|---|
| `session_id` | **Must** equal Claude Code's `session@id` where applicable, so the record joins the existing tool timeline |
| `turn_id` | **Must** equal `prompt@id` where applicable |
| `sequence` | Monotonic within the session, for ordering |
| `workspace` | Profile. Stamped at emission, never inherited |
| `service` | `claude-code` · `router.llm` · later `codex`, `opencode` |
| `model`, `backend` | As the service reports them |
| `role` | `user` \| `assistant` \| `tool` |
| `content` | **The message delta** — see the rule below |
| `tokens_in`, `tokens_out`, `cost_estimate_usd` | Where the service reports them |
| `parent_session_id` | For a subagent, the session that spawned it. Absent today; see Follow-ups |

### The delta rule — non-negotiable

**Capture the message delta, never the serialized prompt.** A turn's full prompt re-sends
the entire conversation, so naive capture is quadratic: a 200-turn session re-sending a
large context would write 100 MB+ for one conversation. Store the new user message and the
assistant response; carry token counts for everything else.

## The two emitters

### 1. Router LLM plane — the easy one

`phantom-router/src/phantom_router/llm/core.py` already assembles every attribute at
`complete()` and already mints a correlation id. Changes:

- Record `content` (delta per the rule above) alongside the existing fields.
- Replace the local `uuid.uuid4().hex` trace id with a **W3C trace id**, and emit the call
  as an OTEL span so it lands in the same store as everything else.
- Keep emitting the envelope to the bus — the bus drives existing UI; this is additive.

One chokepoint covers ollama, Claude API, Claude OAuth and OmniRoute.

### 2. Claude Code sessions — needs a hook

OTEL will not provide content, and transcripts are not retained. The viable mechanism is a
**Claude Code hook**, which this environment already uses (a `PreToolUse` secret-guard and
a `Stop` hook are both live). A hook captures the turn and ships it to the OTLP collector.

This is the emitter that matters most: Claude Code is where the sessions and subagents
actually are.

### Not in scope

Codex (backend unimplemented) and opencode (no telemetry) emit nothing. Instrumenting them
is net-new work independent of this design and does not affect the store decision.

## Drill-down, once captured

- **Table / list** — running and recent sessions, profile-scoped, from the existing tool
  events: last `tool_name` as the activity line, `duration_ms`, `success`, elapsed.
- **Click a session** — its turn timeline by `session@id` ordered by `event@sequence`,
  interleaving tool calls with the captured prompt/completion for each `prompt@id`.
- **Attach** — the existing ttyd slide-out from `SessionsPanel`, lifted into a shared
  component.

## Corrections to earlier assumptions

Recorded so they are not re-derived. Full reasoning in the review record.

- **"`runner-work.parent_id` is one field" — wrong twice.** The envelope is built in the
  Swift runner (`phantom-runner/.../RunnerCore.swift:329`) from a `WorkItem` carrying only
  `{id, kind, payload}` — the runner cannot know its dispatcher. Setting it there means a
  notarized release across a fleet whose auto-updater is known broken. And a *uniform*
  parent rule is wrong anyway: of 295 `runner-work` rows, `platform.ps` is 150 (**UI
  polling, not agent work**) and 143 are `session.*` ops whose parent is the session, not a
  hub task.
- **The "403 roots → 71, depth-2 1 → 307" figure was inflated.** It was produced by
  stapling polling and session ops under hub tasks round-robin. Honest figures, excluding
  polling: 348 agent-work rows, 214 roots, 20 at depth 2, max fan-out 6.
- **`Task.spawned_by` exists and is discarded.** `models.py:285-286` carries both
  `job_id` and `spawned_by`; `agent_store.py:545` builds the envelope from `job_id` only,
  flattening every spawn chain to its job root — which is why only **one**
  `hub-task→hub-task` edge exists in a 500-row window.
- **`phantom-events` clobbers parentage.** `store.py:252`:
  `parent_id = EXCLUDED.parent_id`, with only `status` and `workspace` COALESCE-protected.
  Any edge one producer writes is erased by the next envelope that omits it. The correct
  semantic already exists as precedent in `phantom-router/agent_store.py:213`.
- **Profile must be stamped, never inherited.** Inheriting across a *derived* edge turns a
  cosmetic error into cross-profile leakage. A derived edge may group visually; it must
  never confer identity.
- **A live cross-profile leak exists** at
  `app/frontend/src/lib/stores/streamLive.svelte.ts:213` — `if (env.workspace && ...)`
  short-circuits on an empty workspace, so workspace-less envelopes are retained under
  every profile.

## Cut, with reasons

- **Per-query memory read logging.** Volume (recall runs before every unit of work),
  value ("I read my own note" is not an audit finding), and privacy (a per-query log of
  what you asked your own memory, on vaults that p2p-sync). If "data touched" is wanted,
  record it at task granularity on `task_summary`, which already promotes.
- **"Which memory did this read"** from telemetry — not answerable: `mcp_tool` does not
  name the tool and CLI recalls run under `Bash` with no command captured.
- **The prototype's "this session" scope** and its invented middle layer — assembled from
  conversation history, presented in the same visual language as real telemetry.
- **The scope switcher.** Operators do not choose join hypotheses; that is design-record
  content, not a product control.

## Open questions — resolve before implementation

1. **Retention and access control.** Prompts and completions are the most sensitive data
   this platform would hold — more so than the read log argued against above. OpenSearch is
   fronted by Traefik on a real domain. This needs an explicit retention policy and
   index-level access control, which the current setup does not have. **Blocking.**
2. **Redaction at capture.** Secrets appear in prompts and in tool output. The
   `secret-guard` hook already exists for commands; capture needs an equivalent before text
   is persisted. **Blocking.**
3. **Subagent identity.** Giving `Agent` events a child session id is what turns 1,688
   recorded spawns into an actual tree. Where does that id come from, and does the hook or
   the harness emit it?
4. Whether the router should keep emitting to the envelope bus indefinitely, or the bus
   read-path should move to OpenSearch once content lands there.

## Follow-ups, independent of this design

- One line: `parent_id = COALESCE(EXCLUDED.parent_id, agent_state.parent_id)` in
  `phantom-events/store.py`. Prerequisite for any multi-producer parentage.
- Two lines: prefer `task.spawned_by` over `task.job_id` in `agent_store.py`.
- Fix the cross-profile leak in `streamLive.svelte.ts:213`.
- Nothing reads `otel-v1-apm-span-*`. Either consume it or stop writing 172k spans of
  `cli/ps` polling.
