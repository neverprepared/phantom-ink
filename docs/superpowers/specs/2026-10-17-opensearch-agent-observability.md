# SPEC — OpenSearch-native, multi-harness agent observability

- **Date**: 2026-10-17
- **Status**: proposed, not implemented
- **Revision**: r2 — r1 proposed "enable OTLP traces from Claude Code". That rested on a false premise and is **withdrawn**; see §1-P2. The trace story is now split: real spans for harnesses we instrument ourselves, derived trees for Claude Code.
- **Repos touched**: `phantom-platform` (Data Prepper pipelines, dashboards provisioner), `phantom-router` (`complete()` seam), `phantom-ink` (`app/opensearch`, `AgentWorkPanel.svelte`)
- **Companion**: `docs/superpowers/specs/2026-10-17-agent-observability-design.md`

Every number carries its source. `[CONTEXT]` = measured in the preceding session and
accepted. `[VERIFIED 2026-10-17]` = re-measured while writing this spec, query in §6.
§8 lists what I could not establish.

---

## 1. Problem statement

### P1 — Cost and tool-call figures are wrong by ~2x depending on which store you read

Same session, two stores: OpenSearch 1,786 tool calls / $416.26; Langfuse 813 / $309.05
`[CONTEXT]`. The delta is work Langfuse's transcript-tailing hook never sees:
`agent:custom` (1,136 calls, $60.80), `away_summary`, `prompt_suggestion`, `compact`. The
transcript the hook tails has 6,704 rows and **zero** `isSidechain` rows — subagent work
lives in `<session>/subagents/*.jsonl`, a different file. The official
`langfuse/claude-observability-plugin` does walk those files and still reports, in its own
issue #100, **18/220** capture for agent-team/teammate subagents, 0/4 for
subagent-launched subagents, ~52% of spend overall. This repo's workflow is teammate-heavy.

Langfuse therefore cannot be the source of truth. That decision is already taken.

### P2 — Claude Code does not emit OTLP spans, and this is not a configuration problem

> **SUPERSEDED BY §10.** This heading is wrong: it *is* a configuration problem.
> Span emission is independently gated on `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`,
> which the binary's own admin help text documents alongside
> `OTEL_TRACES_EXPORTER=otlp`. The flag was set and **has now taken effect**:
> 210 `claude-code` spans across 2 traces are in the span index — see §11. Everything below correctly describes the state as MEASURED, and the
> designs built on it remain sound, but do not read the premise as permanent.

**r1 was wrong about this.** The claim "traces are available via env var, currently unset"
`[CONTEXT]` does not survive measurement.

- The workspace env file at `/Users/cdowning/workspaces/profiles/personal/.env` **already
  sets** `CLAUDE_CODE_ENABLE_TELEMETRY` (:96), `OTEL_TRACES_EXPORTER` (:99),
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (:103) and `OTEL_RESOURCE_ATTRIBUTES` (:106)
  `[VERIFIED 2026-10-17]`.
- That config demonstrably reaches Claude Code: logs and metrics arrive by the identical
  mechanism, and log documents carry `serviceName: "claude-code"` plus
  `resource.attributes.service@version: "2.1.267"` `[VERIFIED 2026-10-17]`.
- The traces pipeline works. `otel-v1-apm-span-*` received a span 2 minutes before this
  was written, and Data Prepper runs five pipelines — `otel-traces-entry`,
  `otel-traces-raw`, `otel-service-map`, `otel-metrics`, `otel-logs`
  (`phantom-platform/config/opensearch/pipelines.yaml` `[VERIFIED 2026-10-17]`), with
  Traefik routing all three signals (otel-traces → h2c :21890, metrics :21891, logs
  :21892).
- And yet: over 7 days the span index holds 28,142 spans with `serviceName` taking
  **exactly one value, `compose`**. All-time: `compose` 180,646, `OpenTofu CLI` 2,852,
  `buildx` 542 — **no `claude-code` bucket, ever**. Spans whose `name` starts
  `claude_code` : **0, all time** `[VERIFIED 2026-10-17]`.

The machinery is in the binary (`BatchSpanProcessor` ×4, `OTEL_TRACES_EXPORT_INTERVAL`,
`OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT`), and so are the span names r1 leaned on
(`claude_code.interaction`, `.llm_request`, `.tool.execution`, `.subagent.spawn`,
`.mcp.rpc`, `.bash.subprocess`, `.hook`, `.compaction`, `.tool.blocked_on_user`
`[VERIFIED 2026-10-17]`). But the binary also carries a *parallel internal* tracing
configuration alongside the public one `[VERIFIED 2026-10-17]`: `BETA_TRACING_ENDPOINT`
(×7), `ENABLE_BETA_TRACING_DETAILED`, and an `ANT_`-prefixed twin of each public variable
— `ANT_OTEL_TRACES_EXPORTER`, `ANT_OTEL_LOGS_EXPORTER`, `ANT_OTEL_METRICS_EXPORTER`,
`ANT_OTEL_RESOURCE_ATTRIBUTES`.

**Conclusion**: everything downstream of Claude Code is already built and working. Claude
Code's tracing is an internal beta path, not the public OTLP one. There is no env change
that produces claude-code spans, and this spec must not contain a step that implies there
is. Span names existing in a binary is not evidence of spans being emitted — r1 treated it
as such, which was the error.

### P3 — A Claude Code tool call cannot be attributed to the agent that ran it

Two candidate join keys, both measured, both insufficient.

**`query_source` does not exist on tool events.** It is present only on `api_request` and
`assistant_response` `[CONTEXT]`.

**`prompt@id` is not a subagent discriminator.** It is universally present — every
`api_request`, `tool_decision`, `tool_result` and `assistant_response` carries one
`[VERIFIED 2026-10-17]`, which made it the obvious replacement. It is not one. A single
`prompt@id` in one session spans the main thread *and* every subagent it spawned
`[VERIFIED 2026-10-17]`:

| `prompt@id` | `api_request` docs | `query_source` split |
|---|---|---|
| `e1c2e3d6-0047-45…` | 253 | `repl_main_thread` 148, `agent:custom` 102, `away_summary` 2, `compact` 1 |
| `e44782c5-1b58-4c…` | 142 | `agent:custom` 122, `repl_main_thread` 17, + 3 auxiliary |
| `b03123be-073f-43…` | 135 | `agent:custom` 103, `repl_main_thread` 30, `away_summary` 2 |

`prompt@id` is **one user turn**, not one agent. The same ids carry the turn's
`tool_result` docs (246, 145, 120 respectively). So `tool_result → prompt@id →
query_source` is a many-to-many join with no way to pick the right side.

**Therefore**: in the derived tree a tool call can be placed in a *turn* but not assigned
to an *agent*. This is a hard limit of the available data, it is not solved by this spec,
and §3.1 states it in the UI rather than guessing.

### P4 — Subagent completion reporting captures a small minority of subagent work

`subagent_completed` is the only event that names a child. Its coverage is bimodal — 64
spawns/70 reports, 27/28, 27/33, but also 38/0, 45/0, 34/0 `[CONTEXT]`. Measured now, it is
worse than bimodal at the aggregate:

- Over 7 days: **10** `subagent_completed` docs against **55** `tool_result` docs with
  `tool_name: "Agent"` and 64 with `SendMessage` `[VERIFIED 2026-10-17]`. Roughly 18%
  coverage of spawns — the same ratio as the Langfuse plugin's own 18/220 (P1).
- One session (`9c8c62c3…`): **1,164 `agent:custom` api_requests and zero
  `subagent_completed` docs** `[VERIFIED 2026-10-17]`. Heavy subagent work, no reports at
  all.

A useful corollary, and a design input for §3.1: `tool_result` with `tool_name: "Agent"`
is a **5.5x better spawn signal** than `subagent_completed` (55 vs 10 over 7 days). Where
`subagent_completed` does land it is rich — a real document carries `agent_type`
(`"Explore"`), `agent@source` (`"built-in"`), `is_built_in`, `is_async`, `duration_ms`
(217534), `total_tokens` (103628), `total_tool_uses` (42), `model`, `final_model`,
`model_swapped`, and the **parent's** `session@id` `[VERIFIED 2026-10-17]`. So per-subagent
tool *counts* are available for the minority that report, even though individual tool calls
are never attributable (P3).

### P5 — The liveness indicator is measurably broken

`AgentWorkPanel.svelte:100` uses `ACTIVE_WITHIN_MS = 150_000` against the
`claude_code.active_time.total` heartbeat, justified by a code comment asserting "a strict
60s cadence" from a single 46s sample (`:93-99`). It is not strict. Across 3 days / 21
sessions / 1,538 gaps: median 60s, **p90 180s**, p99 7,440s, max 97,081s, **10.0% of gaps
exceed 150s** `[CONTEXT]`. Re-measured on one live 489-beat session
`[VERIFIED 2026-10-17]`: median 60.001s, p90 120.0s, p99 9,000s, max 41,912s, **9.0%
exceed 150s**. Two independent samples agree the threshold misfires on roughly one interval
in ten.

The series also carries **no `query_source` on any document** — 0 docs match
`exists: metric.attributes.query_source` on `claude_code.active_time.total`
`[VERIFIED 2026-10-17]`, across 26,566 docs `[CONTEXT]`. It is main-thread-only, so the
beat stops during subagent fan-out, exactly when the operator most needs it. Measured
during fan-out: log recency median lag 40s vs heartbeat median 264s `[CONTEXT]` — the
signal explicitly rejected in a code comment at `app/opensearch/sessions.go` outperforms
the one that shipped.

### P6 — No harness other than Claude Code emits anything

`codex`: nothing (`phantom-router/src/phantom_router/llm/strategy.py` — "not implemented
yet"). `opencode` (tier0): no telemetry configuration at all. `[CONTEXT]`

### P7 — The panel is sized for a volume that does not exist

735 distinct sessions all-time across 65 daily indices (2026-08-04 → 2026-10-17), 176 MB
logs + 51.7 MB metrics, **2 distinct sessions in the last 24h, ~10/day all-time**
`[CONTEXT]`. `AgentWorkPanel.svelte` is 996 lines with a 500-event timeline, truncation
handling, agent breakdown and per-capture-flag warnings — infrastructure for hundreds of
sessions a day.

### P8 — The log index template pins one field, so the rest mis-map

`pipelines.yaml`'s `otel-logs` pipeline declares a template with
`log.attributes.duration_ms: long` and nothing else `[VERIFIED 2026-10-17]`. Everything
else takes dynamic mapping from the first document's JSON type. Result, on
`logs-otel-2026.10.17/_mapping/field` `[VERIFIED 2026-10-17]`:

| field | type |
|---|---|
| `log.attributes.duration_ms` | `long` |
| `log.attributes.input_tokens` | `long` |
| `log.attributes.event@sequence` | `long` |
| `log.attributes.prompt_length` | **`text`** |
| `log.attributes.tool_result_size_bytes` | **`text`** |
| `log.attributes.success` | **`text`** |

Consistent across all 65 daily indices `[CONTEXT]`. A numeric aggregation on the `text`
ones returns 0, silently. Root cause with a one-file fix, not a permanent trap.

### Explicitly NOT solved by this spec

- **N1 — Retention.** No policy deletes anything. The only ISM policy is
  `raw-span-policy` on `otel-v1-apm-span-*` with a single state, `current_write_index`
  `[VERIFIED 2026-10-17]` — rollover, no delete. `logs-otel-*` and `metrics-otel-*` have no
  policy. Langfuse retention is Enterprise-only; with `LANGFUSE_INIT_PROJECT_RETENTION`
  reaching the container a probe project's `retention_days` came back null `[CONTEXT]`.
  Both stores retain indefinitely. Prerequisite for any content capture (N2).
- **N2 — Content capture.** `OTEL_LOG_USER_PROMPTS`, `_ASSISTANT_RESPONSES`,
  `_TOOL_CONTENT`, `_RAW_API_BODIES` stay unset. Out by instruction, and correct while N1
  and N3 are open. `OTEL_LOG_TOOL_DETAILS` is a separate user decision — §3.6.
- **N3 — Access control.** OpenSearch runs `DISABLE_SECURITY_PLUGIN=true`; an
  unauthenticated DELETE was *processed* (HTTP 400 bad-name, not 401) `[CONTEXT]`.
  Anything that can reach the host can read and delete. Mitigated only by tailnet
  reachability. Unchanged here, and the largest unaddressed problem in this area.
- **N4 — Per-agent tool attribution for Claude Code.** P3. Not solvable from available
  data. Not solvable by the withdrawn trace plan either (P2).
- **N5 — Claude Code span emission.** P2. Outside our control. §7-R2 is the watch item.
- **N6 — Scores, LLM-as-judge, prompt management, datasets, experiments.** Out by
  instruction. Langfuse stays installed for reading individual sessions by deep link.
- **N7 — The broken Langfuse read client.**
  `phantom-router/src/phantom_router/langfuse_client.py` calls removed v3 routes, so MCP
  tools `get_langfuse_session_traces`, `get_langfuse_trace_detail`,
  `get_langfuse_session_summary` are broken against this v4 `events_only` deployment
  `[CONTEXT]`. Not fixed. If the §3.4 deep link is the only Langfuse entry point we keep,
  those three tools should be deleted rather than repaired — noted, not decided.
- **N8 — Reasoning/thinking capture.** `Completion` in the router carries only `text`; no
  backend extracts reasoning blocks (documented in `llm/tracing.py`'s header).

---

## 2. Common schema for multi-harness

### 2.1 The split, stated plainly

There are two classes of harness and they get different machinery. This is the central
design fact of r2.

| | **Harnesses we instrument** (phantom-router `complete()`; any future OTel-capable harness) | **Claude Code** |
|---|---|---|
| Transport | real OTLP spans → Data Prepper :21890 | OTel **logs** + **metrics** (already flowing) |
| Index | `otel-v1-apm-span-*`, `otel-v1-apm-service-map` | `logs-otel-*`, `metrics-otel-*` |
| Tree | real span tree via `parentSpanId` | **derived** tree via `session@id` + `prompt@id` + `event@sequence` |
| Trace Analytics waterfall | yes, free | **no — not usable** |
| Service map | yes, free | **no** |
| Per-agent tool attribution | yes (`parentSpanId`) | **no** (P3) |
| Built by us | §3.3 | §3.1 |

**The service map will only ever show harnesses that emit spans.** It is therefore not a
complete agent graph and must not be presented as one, unless and until Claude Code gains
public trace support (N5). A service map that silently omits the dominant harness is worse
than no service map if its incompleteness is not labelled — §3.4 labels it.

### 2.2 Span convention — for harnesses we instrument only

OTel GenAI semantic conventions. The pipeline is proven working by 180,646 `compose` spans
`[VERIFIED 2026-10-17]`, so no ingest work is required.

| Level | Span name | `kind` | Parent |
|---|---|---|---|
| call | `<harness>.llm_request` | `SPAN_KIND_CLIENT` | none, or a propagated parent |
| attempt | `<harness>.llm_attempt` | `SPAN_KIND_CLIENT` | the call |

Two levels, not four. r1's four-level tree was shaped by Claude Code's internal span names;
with those withdrawn, the only spans we can actually emit are the ones the
`complete()` seam sees (§2.5). Do not specify a session or turn span level until a harness
exists that can fill it.

Required attributes:

| Attribute | Where | Value | Why |
|---|---|---|---|
| `service.name` | resource | harness id: `phantom-router-llm`, later `codex`, `opencode` | **Harness identity.** Lands as `serviceName`, a top-level indexed field, already the Trace Analytics facet and the service-map pivot. Claude Code's log documents already carry `serviceName: "claude-code"` `[VERIFIED]`, so the convention is consistent across both classes. |
| `workspace` | resource | profile name | **Profile scoping.** Lands as `resource.attributes.workspace` — the same key `logs-otel-*` and `metrics-otel-*` already use `[VERIFIED]`, so one filter spans all three indices. Aggregate on `.keyword`. |
| `session.id` | resource | harness session identifier | Joins a span to a derived tree where both exist. |
| `gen_ai.system` | attempt | `anthropic`, `ollama`, … | |
| `gen_ai.request.model` | attempt | model id | |
| `gen_ai.request.attempt` | attempt | 0-based index in the fallback chain | |
| `gen_ai.usage.input_tokens` / `.output_tokens` | attempt | token counts | We control this emitter, so emit the standard names. |
| `phantom.cost_usd` | attempt | cost | No standard OTel key; namespaced to avoid colliding with a future one. |

**Do not key anything on `gen_ai.operation.name`** — absent from the Claude binary (0
occurrences `[VERIFIED]`) and not emitted by the router. Span `name` is the operation
discriminator.

### 2.3 Derived-tree convention — Claude Code

The tree is reconstructed at query time from `logs-otel-*`. Keys, all verified present:

| Level | Key | Notes |
|---|---|---|
| session | `log.attributes.session@id` | `text` with a `.keyword` subfield. Subagents do **not** get their own `session@id` — a `subagent_completed` doc carries the **parent's** `[VERIFIED]`. |
| turn | `log.attributes.prompt@id` | `text` + `.keyword` `[VERIFIED]`. Present on every `api_request`, `tool_decision`, `tool_result`, `assistant_response`, and on 639/642 `subagent_completed` `[VERIFIED]`. One **turn**, not one agent (P3). |
| order | `log.attributes.event@sequence` | `long`. **Not unique**: one session showed 13,240 docs with a max sequence of 10,667 `[VERIFIED 2026-10-17]`, so ties exist — sort `event@sequence` then `time`. Two August indices lack the mapping and **fail the sort while returning HTTP 200 with `_shards.failed: 2`** `[CONTEXT]`; `unmapped_type` is mandatory. |
| agent (model calls only) | `log.attributes.query_source` | `repl_main_thread`, `agent:custom`, `agent:builtin:<Type>`, plus overhead `away_summary`, `prompt_suggestion`, `compact`, `web_fetch_apply`, `web_search_tool` `[VERIFIED]`. On `api_request` and `assistant_response` **only**. |
| named children | `event@name: subagent_completed` | `agent_type`, `agent@source`, `duration_ms`, `total_tokens`, `total_tool_uses`, `is_built_in`, `is_async`, `model`, `final_model` `[VERIFIED]`. ~18% coverage (P4). |
| spawn count | `event@name: tool_result` AND `tool_name: "Agent"` | 5.5x better spawn signal than `subagent_completed` (P4). |

### 2.4 Harnesses that can emit neither

Route them through `phantom-router`'s `complete()` seam
(`phantom-router/src/phantom_router/llm/core.py`) — the universal chokepoint for ollama,
claude_api, claude_oauth, omni and multi `[CONTEXT]`, confirmed by the module docstring and
the existing `_tracing.*` call sites at `core.py:136,155,162,167,169,187`
`[VERIFIED 2026-10-17]`. `codex` and `opencode` land here today.

The honest ceiling: the seam sees one prompt in and one completion out. It produces
**generation spans only** — no turns, no tools. A `codex` row in a dashboard is a
generation list, not a session tree. State this in the UI, not just here.

---

## 3. Per-component design

### 3.1 Derived session tree for Claude Code (replaces r1's §3.1)

This is the audit view from the user's goal — "see what happened in a complete session
tree" — built from `logs-otel-*` because no span tree exists (P2).

Extend `app/opensearch/sessions.go`, which already implements `GetSessionTimeline`
(`sessions.go:430`) as a flat ordered event list with 27 tests `[CONTEXT]`. The change is
**grouping, not re-querying**: fold the existing flat list into turns.

```
SessionTree
  session_id, workspace, first_seen, last_seen
  turns[]  (one per distinct prompt@id, ordered by min event@sequence)
    prompt_id
    user_prompt            — the user_prompt event, if present (text redacted per N2)
    model_calls_by_source  — api_request counts/cost/tokens grouped by query_source
    tool_calls[]           — tool_result events in event@sequence order
                             NO agent attribution (P3)
    spawns                 — count of tool_result where tool_name == "Agent"
    reported_children[]    — subagent_completed docs for this prompt_id
                             (agent_type, duration_ms, total_tokens, total_tool_uses)
```

Query shape: one `terms` aggregation on `log.attributes.prompt@id.keyword` ordered by
`min(event@sequence)`, with sub-aggregations per turn. One round trip, no N+1.

**Mandatory correctness rules**, each from a measured trap:

1. Count tools on `event@name: tool_result` only. Every tool call emits **two** docs,
   `tool_decision` and `tool_result`, both carrying `tool_name` `[CONTEXT]` — 7d: 6,024 and
   5,960 `[VERIFIED]`. Counting on `tool_name` alone doubles every figure.
2. Exclude `hook_execution_start` / `_complete` everywhere. Highest-volume pair in the
   index — 10,450 + 10,450 of ~44k docs in 7 days `[VERIFIED]` — and local hook plumbing,
   not agent work `[CONTEXT]`. The `activityEvents` allowlist at `sessions.go:32` is the
   right mechanism.
3. Sort `event@sequence` with `unmapped_type: "long"`, tie-break on `time`, and route the
   result through `shardReport.describe()` (`sessions.go:41-67`) so a partial answer is
   visible. A silent `_shards.failed: 2` with HTTP 200 is the failure mode `[CONTEXT]`.
4. Report `cache_read_tokens` and `cache_creation_tokens` as separate figures from
   `input_tokens`, which **excludes** cache reads — 186M cached vs 7.8k fresh on one
   session `[CONTEXT]`. One merged "tokens" number reads as a contradiction against cost.
5. `success` is tri-state — `"true"`, `"false"`, **absent** on events with no outcome
   `[CONTEXT]`. Never fold absent into failure.

**Mandatory honesty rules in the UI** — the derived tree's value depends on not
overclaiming:

- A turn's tool list is labelled *"tools used in this turn (main thread and subagents
  combined — not separable)"*. P3 is a data limit; hiding it manufactures a false audit
  trail, which is worse than an incomplete one.
- Where `spawns > len(reported_children)`, say so with both numbers. The existing notice at
  `AgentWorkPanel.svelte:538-549` already does this and should be kept and strengthened —
  measured coverage is ~18% (P4), and one real session showed 1,164 `agent:custom` model
  calls with **zero** reports `[VERIFIED]`.
- `query_source` figures are labelled as covering *model calls*, not work.

### 3.2 Fix the log index template (prerequisite for §3.1's numeric aggregations)

Extend `otel-logs` `template_content` in `pipelines.yaml` to pin, under
`log.attributes.properties` alongside the existing `duration_ms: long`:

| field | type | today |
|---|---|---|
| `input_tokens`, `output_tokens`, `response_length` | `long` | `long` (already correct) |
| `cache_read_tokens`, `cache_creation_tokens` | `long` | unpinned |
| `prompt_length` | `long` | **`text`** |
| `tool_input_size_bytes`, `tool_result_size_bytes` | `long` | **`text`** |
| `event@sequence` | `long` | `long` |
| `success` | `keyword` | **`text`** |
| `session@id`, `prompt@id`, `query_source`, `tool_name`, `event@name`, `agent_type` | `keyword` | `text` + `.keyword` |

`success` as `keyword`, not `boolean`: it is absent on events with no outcome `[CONTEXT]`,
so it is tri-state and a boolean mapping invites readers to treat absent as false.

**Forward-only.** A template applies at index creation, so the 65 existing daily indices
keep their mappings. Therefore:

- Keep `floatOf`, `numOf`, `boolOf` (`sessions.go:660,679,697`) — they parse either
  representation. This fix shrinks the trap's blast radius; it does not retire the helpers.
- Every aggregation on a newly-pinned field sets `unmapped_type`.
- **Do not reindex.** 176 MB is small, but a reindex is a write against a store with no
  access control (N3) and no verified backup in scope.

### 3.3 OTel spans from the router `complete()` seam

The one place in this spec where real spans are produced — and the pipeline is already
proven, so this is the cheapest path to a working waterfall.

`phantom-router` branch `feat/llm-langfuse-tracing` already instruments the seam
(`llm/tracing.py`; public surface `session()`, `completion_span()`,
`attempt_generation()`, `record_success()`, `record_failure()`, `flush()`, `enabled()`
`[VERIFIED 2026-10-17]`), and `CallCtx.trace_id` (`llm/types.py:81` `[VERIFIED]`) holds a
`uuid4().hex` — a valid 32-hex OTel trace id `[CONTEXT]`.

**Design: a parallel exporter behind the same five call sites, not a rewrite.** Add
`llm/otel.py` mirroring `tracing.py`'s contextmanager shape; `core.py` fans out to both.
Rationale: `tracing.py` holds two hard-won invariants in its own header — inert without
credentials, and a completion must never fail because tracing failed — and it deliberately
keeps content *off* `LlmCompletionRecord` so the agent-event bus stays metadata-only.
Reimplementing risks both. The OTel path inherits both and adds a third: **no content at
all**, not even redacted. Content stays Langfuse-only, where N1/N2 exposure is bounded to
one store.

| `complete()` concept | span | attributes |
|---|---|---|
| the call | `phantom-router-llm.llm_request` | `service.name`, `workspace=<record.profile>`, `session.id=<CallCtx.session_name or trace_id>`, `phantom.caller=<CallCtx.caller>` |
| each `attempts[]` entry | child `phantom-router-llm.llm_attempt` | `gen_ai.system`, `gen_ai.request.model`, `gen_ai.request.attempt`, `gen_ai.usage.input_tokens`, `.output_tokens`, `phantom.cost_usd`, `phantom.backend` |
| a failed attempt | same span, `status=ERROR` | description = `LlmError.reason` |

The fallback chain as sibling spans under one parent is the point: "ollama timed out,
claude_api answered" becomes one glance at a waterfall and is currently invisible anywhere.

Use `CallCtx.trace_id` as the OTel `traceId` so the metering record, the Langfuse trace and
the OpenSearch span key on one value. `_is_otel_trace_id` (`tracing.py:349` `[VERIFIED]`)
already validates exactly this.

Config: new `CL_OTEL__ROUTER_TRACES`, default **off**, so merging is inert. Endpoint reuses
`settings.otel.endpoint_host` + `traces_port` (`config.py:483-486` `[VERIFIED]`), with
`127.0.0.1` rather than `host.docker.internal` when the router runs on the host.

**No change to session container env.** `backends/docker/__init__.py:170-180` already sets
`OTEL_TRACES_EXPORTER`-adjacent config correctly and the traces endpoint is already wired;
P2 means adding or changing anything there produces nothing. Leave it.

### 3.4 Dashboards saved objects as code

`phantom-platform/config/opensearch/dashboards-provision.py` already does this — 310
lines, idempotent, with `save()`, `delete()`, `refresh_index_pattern()`, run by
`dashboards-init` (`docker-compose.yml:644-652`) which `depends_on`
`opensearch-dashboards: service_healthy` `[VERIFIED 2026-10-17]`. Extend it; do not
replace it.

1. Add the §5 ids to the delete-then-recreate list at `provision.py:65-73` so re-running
   stays safe.
2. `refresh_index_pattern` for `logs-otel` and `metrics-otel` — the §5 views are all
   log/metric based, so unlike r1 there is no cold-start ordering problem: the fields
   already exist.
3. A `markdown` saved object with **two** things: the Langfuse per-session deep-link
   template (Langfuse's entire remaining role, N6 — do **not** build a data integration,
   the read client is broken, N7), and a **coverage statement**: which harnesses appear in
   Trace Analytics and the service map, and that Claude Code does not. Without that label,
   the service map reads as a complete agent graph and it is not (§2.1).

**Trace Analytics and the service map are usable for router spans only.** They are
built-in screens over `otel-v1-apm-span-*` and `otel-v1-apm-service-map` (20 docs
`[VERIFIED]`), require no code, and will show `phantom-router-llm` once §3.3 is enabled.
They will never show Claude Code (P2). The §3.1 derived tree in the app panel is Claude
Code's equivalent, and the two are not interchangeable — do not cross-link them as if they
were the same view.

### 3.5 Liveness fix and panel trim

Liveness: §4. Panel trim, against P7 (~10 sessions/day):

**Keep** — session list, profile scoping, the drill-down timeline (now grouped into turns
per §3.1), per-event tool/model/duration/cost, the spawn-vs-report discrepancy notice
(`:538-549`, strengthened per §3.1), and the `timeline.warning` shard-failure surface
(`:571`) because silent partial results are the §3.2 trap.

**Delete**:
- The `30d` range (`:118`). 735 sessions over 74 days; a 30-day list is not navigable and
  is not the question the panel answers.
- The `promptCaptureOff` warning block (`:592`). N2 is a standing decision, not a transient
  state. Replace with one line of static help text.
- The 500-event timeline cap drops to 200 (`:609`); keep the truncation notice.

**Keep, contrary to r1** — the `agentBreakdown` section (`:552`). r1 proposed deleting it
once traces gave a better answer with correct attribution. P2 means traces never arrive and
P3 means correct attribution is unavailable, so `query_source` on model calls is the **only**
subagent view that will ever exist. Keep it, and relabel it from an agent breakdown to
*"model calls by source"*, which is what it actually measures.

**Scope split**: the panel owns *triage and audit* for Claude Code; Dashboards owns
aggregate trends and the router's waterfalls. r1 assigned audit to Dashboards via Trace
Analytics; P2 moves it back into the panel, which is the main reason to resist shrinking
the panel further than listed above.

### 3.6 Alerting and anomaly detection

`opensearch-alerting` and `opensearch-anomaly-detection` are both installed `[CONTEXT]`.

**Two monitors.**

1. **Daily cost ceiling.** Per-bucket monitor over `metrics-otel-*`, `name:
   claude_code.cost.usage`, `sum(value)` grouped by `metric.attributes.workspace.keyword`
   over 24h. The source split is real: `main` $11,168.74, `subagent` $940.70, `auxiliary`
   $916.45 all-time via `metric.attributes.query_source.keyword`
   `[VERIFIED 2026-10-17]`. Those are cumulative sums of a delta counter and are **not**
   daily dollars — do not seed a threshold from them; §6.5 derives one.
2. **API failure rate.** Over `logs-otel-*`, `event@name: api_request`, alerting when the
   share with `success` not `"true"` exceeds a measured baseline over 1h. **Depends on
   §3.2**: `success` is `text` today, so scope it to indices created after the template fix
   with a date floor, or it silently never fires — which is worse than no alerting.

**Anomaly detection: not now.** At 2 sessions in 24h and ~10/day `[CONTEXT]`, RCF has no
density to learn from and would produce either constant anomalies or none. Revisit above
~50 sessions/day. Deliberate deletion, not an omission.

**`OTEL_LOG_TOOL_DETAILS` — a user decision, not part of this build.** It puts tool
*parameters* into `logs-otel-*`: the difference between "Bash ran 4s" and "Bash ran `rm -rf
./build` for 4s". It is the highest-value remaining addition for the "any data it touches"
goal — and **more so after P2**, since the derived tree is now the only audit view and
currently shows tool names without arguments. It is also where secrets appear, in a store
with no retention policy (N1) and no access control (N3). Volume is modest (the full
content set measured 0.31 MB/day `[CONTEXT]`; this is a subset). **Recommendation: hold
until N1 has an ISM delete policy, then enable it alone.** Surfaced for the user to rule
on; the spec assumes it stays off.

---

## 4. The liveness design

### 4.1 What is available

- **No session-start and no session-end event** exists in any of the 23 distinct event
  names the index has ever held `[CONTEXT]`. "Finished" is **not derivable**. Any design
  claiming to show "done" is lying.
- `claude_code.active_time.total`: median gap 60s, p90 120–180s, 9–10% of gaps over 150s
  across two samples (P5); main-thread only (0 of 26,566 docs carry `query_source`).
- Metric beats land ~75s behind wall clock, export cadence ~60s `[CONTEXT]`.
- Log recency: median lag 40s during fan-out vs the heartbeat's 264s `[CONTEXT]`. Logs
  export every 10s (`OTEL_LOGS_EXPORT_INTERVAL` default) `[CONTEXT]`.

### 4.2 Design: two independent signals, tri-state, no fused score

```
recency   = now - max(time) over logs-otel-* for this session@id,
            excluding hook_execution_start / hook_execution_complete
heartbeat = now - max(time) over metrics-otel-*,
            name=claude_code.active_time.total, this session@id

ACTIVE    if recency < 120s   → "activity 34s ago"
IDLE      if recency < 900s   → "last activity 6m ago"
DORMANT   otherwise           → "last activity 2h ago"
```

1. **Recency is primary, heartbeat secondary.** This reverses the code comment at
   `AgentWorkPanel.svelte:93-99`, and the measurement is the reason: during fan-out,
   recency lags 40s and the heartbeat 264s `[CONTEXT]`. The comment's objection — live
   sessions have 80s log gaps — is why the threshold is 120s not 60s, not a reason to
   prefer a signal with a 180s p90.
2. **The hook-event exclusion is load-bearing.** `hook_execution_*` is the highest-volume
   pair (10,450 + 10,450 of ~44k docs in 7d `[VERIFIED]`) and is local plumbing `[CONTEXT]`.
   Without the exclusion, recency measures the hook loop and every session looks alive.
   Reuse the `activityEvents` allowlist at `sessions.go:32`.
3. **Three states, not two.** The current binary dot cannot say "I do not know", which is
   the actual state after 10 minutes of silence.
4. **Heartbeat is displayed, never thresholded.** Show it as a secondary line. It is the
   only main-thread-specific signal, so fresh recency + stale heartbeat is a readable,
   useful statement: *subagents are working, the main thread is waiting*. Fusing them
   destroys that distinction — the most operationally interesting thing this data can say,
   and more valuable now that P3/P4 mean nothing else reveals subagent activity.
5. **120s and 900s are chosen, not measured.** 120s = the 80s observed worst-case log gap
   `[CONTEXT]` + the 10s export interval + margin. 900s is a judgement about when an
   operator stops caring. §6.3 measures both error directions so they can be tuned from
   evidence; the rationale is left visible so a future reader can see which numbers are
   assumptions.

### 4.3 What it can and cannot tell the operator

**Can**: "something happened in this session N seconds ago"; "the main thread last beat N
seconds ago"; "those two disagree, so subagents are probably running".

**Cannot**, and the UI must not imply otherwise:
- **Whether a session finished.** No end event exists `[CONTEXT]`. DORMANT means "silent
  for 15 minutes" and covers finished, crashed, and waiting-on-a-human identically.
- **Whether a session is blocked on user input.** r1 held this open on the grounds that a
  `claude_code.tool.blocked_on_user` span might answer it. **P2 closes that**: the string
  is in the binary but no claude-code span will arrive, so this is not answerable, full
  stop.
- **Which subagent is active.** No per-subagent liveness signal exists, and r1's
  "derivable from open spans" route is withdrawn with P2.

### 4.4 Implementation notes

- `sessionHeartbeats` (`sessions.go:257`) stays, as the secondary display value rather than
  the liveness gate.
- Liveness computation moves out of the Svelte component (`isActive`, `:204`) into
  `ListAgentSessions`, returned as `liveness: "active"|"idle"|"dormant"` plus
  `last_activity_ms` and `last_heartbeat_ms`. A threshold in a component is a threshold
  with no tests; the Go package has 27 `[CONTEXT]`.
- Keep `POLL_MS = 15_000` (`:104`). The 10s log export interval means faster polling cannot
  surface fresher data — that comment is correct.
- `metric.attributes.session@id.keyword` is required for the heartbeat aggregation and
  **some older daily indices lack the subfield and fail it** (7 shards) `[CONTEXT]`. Set
  `unmapped_type: "keyword"` and route the result through `shardReport.describe()`.

---

## 5. Dashboards inventory

Sized for ~10 sessions/day (P7). One dashboard, six saved objects, plus two built-in
screens that cover router spans only.

### Built-in, zero code — router spans only

| View | Index | Question | Covers |
|---|---|---|---|
| **Trace Analytics → Traces** | `otel-v1-apm-span-*` | "What happened in this completion, including every fallback attempt?" | `phantom-router-llm` after §3.3. **Not Claude Code** (P2). |
| **Trace Analytics → Services** (service map) | `otel-v1-apm-service-map` | "Which harnesses and backends are in play?" | Same. Incomplete by construction — label it (§3.4). |

### Saved objects to add to `dashboards-provision.py`

| id | Type / aggregation | Index | Question |
|---|---|---|---|
| `ag-cost-by-source` | pie, `sum(value)` by `metric.attributes.query_source.keyword`, filter `name: claude_code.cost.usage` | `metrics-otel-*` | "Main thread vs subagents vs auxiliary overhead?" Answers P1's gap: measured `main` $11,168.74 / `subagent` $940.70 / `auxiliary` $916.45 `[VERIFIED]`. |
| `ag-cost-trend` | date histogram 1d, `sum(value)`, split by `metric.attributes.workspace.keyword` | `metrics-otel-*` | "Is spend trending, in which profile?" Seeds the §3.6 threshold. |
| `ag-tokens-split` | horizontal bar, `sum` of `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, filter `event@name: api_request` | `logs-otel-*` | "Why is cost what it is?" **Four series, deliberately** — `input_tokens` excludes cache reads and cache reads dominate (186M vs 7.8k on one session `[CONTEXT]`). One merged number reads as a contradiction. |
| `ag-tool-mix` | horizontal bar, `count` by `tool_name.keyword`, filter **`event@name: tool_result`** | `logs-otel-*` | "What does the work consist of?" The filter is load-bearing: two docs per tool call, both with `tool_name` (7d: 6,024 `tool_decision` + 5,960 `tool_result` `[VERIFIED]`). Counting on `tool_name` alone doubles everything. |
| `ag-tool-failures` | data table, `count` by `tool_name.keyword` split by `success.keyword`, filter `event@name: tool_result` | `logs-otel-*` | "What is failing?" `success` is tri-state `[CONTEXT]`, so absent gets its own column rather than folding into failure. Works pre-§3.2 because `.keyword` exists on the `text` mapping. |
| `ag-spawn-coverage` | date histogram 1d, two series: `count` of `tool_result` where `tool_name: "Agent"`, and `count` of `event@name: subagent_completed` | `logs-otel-*` | **"How much subagent work is invisible?"** The gap between the lines is the P4 blind spot, measured at 55 vs 10 over 7 days `[VERIFIED]`. This is the one new view r2 adds, and it exists so the instrumentation's own incompleteness is on a chart rather than in a footnote. |

Deliberately **not** built: a latency-percentile panel (Trace Analytics does this better
for spans; `avg(duration_ms)` in the panel covers Claude Code), a per-model cost breakdown
(`cc-model-cost` already exists in the provisioner), and any per-subagent tool view —
P3/N4, it cannot be computed.

---

## 6. Verification plan

Ordered. §6.0 is a standing check that P2 still holds; §6.1 is the main build gate.

### 6.0 Re-confirm P2 before and after any work here

```bash
curl -s "https://opensearch.neverprepared.com/otel-v1-apm-span-*/_search" \
  -H 'Content-Type: application/json' \
  -d '{"size":0,"aggs":{"s":{"terms":{"field":"serviceName","size":50}}}}'
```

Expected today: buckets `compose`, `OpenTofu CLI`, `buildx` and nothing else
`[VERIFIED 2026-10-17]`. If a `claude-code` bucket **ever** appears, P2 has changed, N5 is
closed, and §2.1's split should be revisited — that is §7-R2. Re-run after any Claude Code
upgrade. Also:

```bash
curl -s "https://opensearch.neverprepared.com/otel-v1-apm-span-*/_search" \
  -H 'Content-Type: application/json' -d '{"size":0,"query":{"prefix":{"name":"claude_code"}}}'
```

Expected: `hits.total.value == 0`.

### 6.1 Derived session tree (gates §3.5 and §5)

Pick a session with known subagent fan-out, then:

```bash
S=<session-prefix>
curl -s "https://opensearch.neverprepared.com/logs-otel-*/_search" \
  -H 'Content-Type: application/json' -d "{
  \"size\":0,
  \"query\":{\"bool\":{
    \"filter\":[{\"prefix\":{\"log.attributes.session@id.keyword\":\"$S\"}}],
    \"must_not\":[{\"terms\":{\"log.attributes.event@name.keyword\":
      [\"hook_execution_start\",\"hook_execution_complete\"]}}]}},
  \"aggs\":{\"turns\":{\"terms\":{
      \"field\":\"log.attributes.prompt@id.keyword\",\"size\":50,
      \"order\":{\"seq\":\"asc\"}},
    \"aggs\":{
      \"seq\":{\"min\":{\"field\":\"log.attributes.event@sequence\"}},
      \"tools\":{\"filter\":{\"term\":{\"log.attributes.event@name.keyword\":\"tool_result\"}},
        \"aggs\":{\"byname\":{\"terms\":{\"field\":\"log.attributes.tool_name.keyword\",\"size\":20}}}},
      \"spawns\":{\"filter\":{\"bool\":{\"filter\":[
        {\"term\":{\"log.attributes.event@name.keyword\":\"tool_result\"}},
        {\"term\":{\"log.attributes.tool_name.keyword\":\"Agent\"}}]}}},
      \"reported\":{\"filter\":{\"term\":{\"log.attributes.event@name.keyword\":\"subagent_completed\"}}},
      \"sources\":{\"terms\":{\"field\":\"log.attributes.query_source.keyword\",\"size\":10}}}}}}"
```

Pass, all four:
- `_shards.failed == 0` (the §2.3 sort/mapping trap).
- Turn buckets are in execution order and their count matches the number of user prompts in
  the session.
- `tools.doc_count` per turn equals the `tool_decision` count for that turn (they pair) —
  confirms rule 1 is not double-counting.
- `spawns.doc_count >= reported.doc_count` in every turn. If `reported` exceeds `spawns`
  anywhere, the §3.1 discrepancy notice has its sign wrong.

Reproduce the P3 negative result, so a future reader does not retry the join:

```bash
curl -s "https://opensearch.neverprepared.com/logs-otel-*/_search" \
  -H 'Content-Type: application/json' -d "{\"size\":0,
  \"query\":{\"bool\":{\"filter\":[
    {\"prefix\":{\"log.attributes.session@id.keyword\":\"$S\"}},
    {\"term\":{\"log.attributes.event@name.keyword\":\"api_request\"}}]}},
  \"aggs\":{\"p\":{\"terms\":{\"field\":\"log.attributes.prompt@id.keyword\",\"size\":8},
    \"aggs\":{\"qs\":{\"terms\":{\"field\":\"log.attributes.query_source.keyword\",\"size\":10}}}}}}"
```

Expected, and the reason P3 is unsolvable: a single `prompt@id` bucket contains **both**
`repl_main_thread` and `agent:custom` — measured 148 / 102 on one turn
`[VERIFIED 2026-10-17]`.

### 6.2 Index template fix

```bash
curl -s "https://opensearch.neverprepared.com/logs-otel-$(date +%Y.%m.%d)/_mapping/field/\
log.attributes.success,log.attributes.prompt_length,log.attributes.tool_result_size_bytes" \
  | python3 -m json.tool | grep '"type"'
```

Pass (on an index created after the change): `prompt_length` and `tool_result_size_bytes`
`long`; `success` `keyword`.

```bash
curl -s "https://opensearch.neverprepared.com/logs-otel-*/_search" \
  -H 'Content-Type: application/json' -d '{"size":0,
  "query":{"term":{"log.attributes.event@name.keyword":"tool_result"}},
  "aggs":{"s":{"sum":{"field":"log.attributes.tool_result_size_bytes"}}}}'
```

Pass: `_shards.failed == 0` **and** `aggregations.s.value > 0`. Check both — a 0 with 0
failed shards is the silent-wrong-answer case from P8.

Then `cd app && go test ./opensearch/... -race`. The 27 tests encode the old mixed-mapping
behaviour; the fix must not break `floatOf`/`numOf`/`boolOf`, which still serve 65 legacy
indices.

### 6.3 Liveness

Against one known-live session and one known-finished (>1h):

```bash
curl -s "https://opensearch.neverprepared.com/logs-otel-*/_search" \
  -H 'Content-Type: application/json' -d "{\"size\":0,
  \"query\":{\"bool\":{
    \"filter\":[{\"prefix\":{\"log.attributes.session@id.keyword\":\"$S\"}}],
    \"must_not\":[{\"terms\":{\"log.attributes.event@name.keyword\":
      [\"hook_execution_start\",\"hook_execution_complete\"]}}]}},
  \"aggs\":{\"last\":{\"max\":{\"field\":\"time\"}}}}"
```

Pass: live < 120s, finished > 900s. Then the real test — sample every 30s for 20 minutes
across a session containing a multi-subagent fan-out, and count:

- **false DORMANT on a live session** (the bug being fixed). Target 0. The current
  heartbeat design fails this 9–10% of intervals `[VERIFIED + CONTEXT]`.
- **false ACTIVE on a finished session.** Target 0. This is the risk the inversion
  introduces (§7-R4) and the number that decides whether recency-primary is right.
- intervals with fresh recency and stale heartbeat. Expected non-zero during fan-out — the
  §4.2 rule-4 signal working, not a failure.

Reproduce the current breakage for the record:

```bash
curl -s "https://opensearch.neverprepared.com/metrics-otel-*/_search" \
  -H 'Content-Type: application/json' -d "{\"size\":500,
  \"query\":{\"bool\":{\"filter\":[
    {\"term\":{\"name\":\"claude_code.active_time.total\"}},
    {\"prefix\":{\"metric.attributes.session@id.keyword\":\"$S\"}}]}},
  \"sort\":[{\"time\":\"asc\"}],\"_source\":[\"time\"]}" \
 | python3 -c 'import json,sys,datetime,statistics
d=json.load(sys.stdin); ts=[h["_source"]["time"] for h in d["hits"]["hits"]]
p=lambda s: datetime.datetime.fromisoformat(s.replace("Z","+00:00"))
g=sorted((p(b)-p(a)).total_seconds() for a,b in zip(ts,ts[1:])); n=len(g)
print("beats",len(ts),"median",statistics.median(g),"p90",g[int(n*.9)],
      "pct>150s",round(100*sum(1 for x in g if x>150)/n,1))'
```

Expected, matching P5: `pct>150s` ≈ 9–10.

### 6.4 Router spans

```bash
cd phantom-router && uv run pytest    # baseline 1702 passing [CONTEXT]
```

With `CL_OTEL__ROUTER_TRACES=true`, drive one `complete()` that forces a fallback
(unreachable primary, working secondary):

```bash
curl -s "https://opensearch.neverprepared.com/otel-v1-apm-span-*/_search" \
  -H 'Content-Type: application/json' -d '{"size":10,
  "query":{"term":{"serviceName":"phantom-router-llm"}},
  "_source":["traceId","spanId","parentSpanId","name","status.code",
             "resource.attributes.workspace"]}' | python3 -m json.tool
```

Pass: one `llm_request` parent; two `llm_attempt` children carrying its `spanId` as
`parentSpanId`; the first with an error status; `resource.attributes.workspace` = the
profile. Then assert the inert case — flag off, same drive, **zero**
`phantom-router-llm` spans.

Then confirm the free win: the Trace Analytics UI at `https://logs.neverprepared.com`
renders that trace as a waterfall, and `otel-v1-apm-service-map` gains a
`phantom-router-llm` node (it has 20 docs today `[VERIFIED]`).

### 6.5 Dashboards and alerts

```bash
# on m3: export PATH=$HOME/.orbstack/bin:$PATH
docker compose up dashboards-init
curl -s "https://logs.neverprepared.com/api/saved_objects/_find?type=visualization&per_page=100" \
  | python3 -c 'import json,sys;print([o["id"] for o in json.load(sys.stdin)["saved_objects"]])'
```

Pass: all six `ag-*` ids present. Re-run: no duplicates, no non-409 status in the script's
per-object output.

Derive the alert threshold rather than choosing it:

```bash
curl -s "https://opensearch.neverprepared.com/metrics-otel-*/_search" \
  -H 'Content-Type: application/json' -d '{"size":0,
  "query":{"bool":{"filter":[{"term":{"name":"claude_code.cost.usage"}},
                             {"range":{"time":{"gte":"now-30d"}}}]}},
  "aggs":{"d":{"date_histogram":{"field":"time","calendar_interval":"1d"},
               "aggs":{"c":{"sum":{"field":"value"}}}}}}'
```

Set the threshold at 3x the observed p95 daily sum. Then verify each monitor fires at all
by temporarily setting its threshold to 0 and confirming an alert appears — an untested
monitor is indistinguishable from a broken one, which is §3.6's failure mode.

---

## 7. Risks and rollback

| id | Risk | Evidence it is real | Mitigation | Rollback |
|---|---|---|---|---|
| R1 | The derived tree is mistaken for a complete audit trail | P3: a turn's tools cannot be split between main thread and subagents. P4: ~18% spawn-report coverage, and one session with 1,164 subagent model calls and zero reports `[VERIFIED]` | §3.1's honesty rules are requirements, not polish: label the tool list as combined, show both spawn and report counts, and put the gap on a chart (`ag-spawn-coverage`) | N/A — this is a labelling discipline, not a deployable. The failure mode is a reviewer dropping the labels as clutter |
| R2 | P2 changes under us — Claude Code gains public trace support, or the beta path changes shape | `BETA_TRACING_ENDPOINT`, `ENABLE_BETA_TRACING_DETAILED` and `ANT_OTEL_TRACES_EXPORTER` all exist in 2.1.267 `[VERIFIED]`, so a public path could land in any release | §6.0 is a standing check, cheap enough to run after every Claude Code upgrade. §2.1's table is the single place the split is encoded | Nothing to roll back; this is upside. Derived trees keep working whether or not spans arrive |
| R3 | §3.2 breaks the 27 `app/opensearch` tests, or a panel query spanning the mapping boundary | Mixed mappings are handled today by `floatOf`/`numOf`/`boolOf`; the change creates a *second* era, not one clean era | Keep all three helpers. Every new agg sets `unmapped_type`. §6.2 runs the Go suite | Revert the `template_content` block; indices already created keep the new mapping and the helpers read both |
| R4 | Recency-primary trades false-dead for false-alive | 120s/900s are judgement calls (§4.2 rule 5). Recency includes events the heartbeat excludes, so a session that stopped working but emitted a trailing event could read ACTIVE | §6.3 counts **both** directions over a real 20-minute fan-out before merge. Logic lives in Go where it is testable | Two constants in `sessions.go`. Tri-state collapses to two by folding IDLE into DORMANT |
| R5 | Router spans break completions | The seam is on the hot path for every ollama/claude/omni call | Inherit `tracing.py`'s invariant — a completion must never fail because tracing failed — and wrap every OTel call. `CL_OTEL__ROUTER_TRACES` defaults **off**, so merging is inert | Set the flag false. Restart nothing but the router |
| R6 | Span volume from §3.3 bloats an unbounded store (N1) | The index already holds 180,646 `compose` spans with no delete policy `[VERIFIED]` | Router completions are far rarer than Claude Code turns, so the increment is small — but it is unmeasured (§8.2). §6.4 records the delta | Flag off. `OTEL_TRACES_SAMPLER` is available on the OTel side if ever needed |
| R7 | A dashboards re-provision wipes a hand-made saved object | `provision.py:65-73` is a deliberate delete-then-recreate | Only `ag-*` and `cc-*` ids are in the delete list. Document that hand-made objects outside those prefixes survive and inside them do not | Re-run the service to recreate; a hand-made object inside the prefix is not recoverable, which is the reason for the convention |
| R8 | Adding data to a store with no access control (N3) widens exposure | `DISABLE_SECURITY_PLUGIN=true`; an unauthenticated DELETE was processed `[CONTEXT]` | Router spans are metadata-only; §3.3's third invariant forbids content. Exposure grows in volume, not in kind | None — accepted risk, stated so it is not silently inherited |

**Rollback of the whole spec**: every piece is independently revertible and none migrates
data. §3.1 is read-only query code; §3.2 is one YAML block, forward-only; §3.3 is behind a
default-off flag; §3.5 is constants plus a field; §3.4 is idempotent saved objects; §3.6 is
two deletable monitors. No step writes to or reindexes an existing index, and nothing
deletes telemetry.

---

## 8. Stated uncertainties

1. **Why Claude Code emits no spans.** P2 establishes *that* it does not, with the config
   proven present and the pipeline proven working. The `BETA_TRACING_ENDPOINT` /
   `ANT_OTEL_TRACES_EXPORTER` / `ENABLE_BETA_TRACING_DETAILED` triple is strong
   circumstantial evidence of an internal beta path, but I did not decompile the binary to
   confirm the public exporter is gated off, and I have **not** tried setting those
   variables. I would not recommend it blind: they are undocumented, their payload shape is
   unknown, and `BETA_TRACING_ENDPOINT` pointing somewhere unintended is a data-egress
   question, not a config question. If anyone pursues it, §6.0 is the test and it should be
   one session on one profile.
2. **Router span volume.** Not measured — no `phantom-router-llm` span exists. Asserted
   small by comparison with Claude Code turn counts (R6). §6.4 records the real delta.
3. **Liveness thresholds 120s / 900s.** Chosen, not measured (§4.2 rule 5).
4. **Whether recency-primary is right.** The supporting measurement (40s vs 264s during
   fan-out) is from `[CONTEXT]` and I did not independently reproduce it. It also
   contradicts a deliberate code comment whose stated objection — live sessions have 80s
   log gaps — is real. §6.3's false-ACTIVE count is the decider; if it is non-zero, the
   right answer may be "display both, threshold neither".
5. **The daily cost alert threshold.** Deliberately unset; the all-time `query_source` sums
   are cumulative counter sums, not daily spend. §6.5 derives it. A number invented here
   would be worse than none.
6. **Whether `prompt@id` is stable across a compaction.** The derived tree's turn grouping
   depends on it. `compaction` is a real event (4 docs in 7d `[VERIFIED]`) and
   `query_source: compact` appears inside an ordinary turn's bucket `[VERIFIED]`, which
   suggests compaction does not start a new turn — but I did not test a session that
   compacted mid-turn. If turn grouping looks wrong after a compaction, this is the first
   thing to check.

---

## 9. Build record — what was implemented, and where this spec was wrong

Added during implementation. Everything below was measured or exercised against
the live systems, not reasoned about. Five of this spec's own claims were wrong,
and they are stated here rather than quietly fixed, because the wrong ones were
wrong in the same direction: implementing them as written would have broken
behaviour that works today.

### 9.1 Corrections to this spec

**§3.2's string rows would have broken the panel at the next index rollover.**
The table mapped `session@id`, `prompt@id`, `tool_name`, `event@name` and
`success` as `keyword`. Dynamic mapping currently gives those fields `text`
*plus* a `.keyword` subfield, and six live aggregations in
`app/opensearch/sessions.go` read exactly those subfields. Pinning them as
`keyword` deletes the subfield, so every one of those aggregations would return
zero buckets on each newly created daily index — a worse defect than the
mis-typing being fixed, and a silent one. `success.keyword` also aggregates
today (63,533 `true` / 1,514 `false` `[VERIFIED]`), so it needed no pinning
either. **Implemented: the ten numeric fields only; no string field touched.**

**`success` does not exist on `api_request`, so §3.6's API-failure monitor is
not implementable.** Measured over 7 days: present on 6,077 of 6,077
`tool_result` docs, and on zero of 6,962 `api_request`, zero of 3,719
`assistant_response`, zero of 10,654 `hook_execution_*` `[VERIFIED]`. Tool
failure is the only failure signal this index carries. What shipped is the
`cc-tool-failures` panel; no API-failure monitor was built.

**§3.4 and §5 assumed Dashboards was unprovisioned. It was not.**
`phantom-platform/config/opensearch/dashboards-provision.py` already created
seven visualisations and the `cc-dashboard` dashboard, all live. Several
proposed saved objects were strictly worse duplicates of `cc-token-usage` and
`cc-cost-over-time`. **Implemented: the existing provisioner extended by three
panels; nothing parallel created.**

**§3.5's two trims were rejected.**

- Dropping the timeline cap from 1000 events to 200 contradicts the stated goal
  of auditing a complete session tree: a session with 1,786 tool calls would
  render 11% of itself. P7's low-volume argument justifies listing fewer
  *sessions*, not showing fewer *events* per session. Cap unchanged.
- Deleting the prompt-capture notice treats content capture as a settled "off".
  It is not — it is wanted, and that block is the only in-product pointer to
  the env vars that enable it. Kept.

**§4.2's "load-bearing hook exclusion" was already implemented.** `LastSeen` is
`max(time)` over a query already filtered to `activityEvents`, so
`hook_execution_*` was never included in it. Liveness needed deriving from
`LastSeen`, not a new query.

### 9.2 Discovered during the build

**Data Prepper's trace source is gRPC-only as configured.** Probed directly:
`POST /opentelemetry.proto.collector.trace.v1.TraceService/Export` returns
**415** — the path exists, the framing is refused — and `POST /v1/traces`
returns **404** `[VERIFIED]`. Only the OTLP/HTTP exporter is installed in the
router. Resolved by setting `unframed_requests: true` on `otel_trace_source`
rather than adding `grpcio` and rebuilding the image for it.

**Two config files are triplicated byte-identically, and nothing enforces it.**
`pipelines.yaml` and `dashboards-provision.py` each exist in
`phantom-platform/config/opensearch/`, `phantom-ink/docker/opensearch/config/`
and `phantom-ink/app/compose/opensearch/config/`. Every change in this build
was applied to all three and md5-verified afterwards. This remains a standing
hazard, not a solved problem.

**`save()` in the provisioner cannot update an existing object.** It POSTs, and
an ignored 409 means "already exists", so an id missing from the delete pass is
created once and then frozen. All three new ids were added to that list.

### 9.3 Verified numbers behind the new dashboard panels

| Panel | Measurement |
|---|---|
| `cc-cost-by-source` | 30d: main $5,154.69 · subagent $503.27 · auxiliary $434.52 — **15.4%** of spend is invisible to a transcript-based capture |
| `cc-cost-by-workspace` | 30d: personal $4,457.50 · lakeview $1,609.62 · gsa $25.36 |
| `cc-tool-failures` | 30d: 764 failures — Bash 523, mcp_tool 214, Read 20 |

Provisioned against the live instance: 10 visualisations and the dashboard all
returned `200`, the dashboard carries 10 panels with 10 resolving references,
and the compound tool-failure kuery returns data in the analysed form
Dashboards actually runs `[VERIFIED]`.

### 9.4 The content-stripping invariant

The router emits one span set to two sinks with deliberately different
payloads. Langfuse keeps prompt and completion text; the OTLP exporter into
OpenSearch must never receive it, because that store runs with
`DISABLE_SECURITY_PLUGIN=true` and no retention policy — an unauthenticated
DELETE against it was *processed*. The dual-sink spike confirmed the hazard is
real: `span.attributes.langfuse@observation@input` and `@output` landed in
OpenSearch carrying full message content.

The guard is an **allowlist**, not a denylist: a denylist must anticipate every
attribute name a future SDK version coins, and being wrong there discloses
conversations. The allowlist is followed by a content-token gate that can veto
an otherwise-allowed prefix, because some revisions of the GenAI conventions
put message text under `gen_ai.request.` and `gen_ai.response.`.

That gate initially rejected `gen_ai.usage.input_tokens` and
`output_tokens` — the basis of every cost panel — because the counts are named
for the direction of the content they measure. Caught by its own test. Resolved
with a short, explicitly reviewed exception set, each entry asserted by test to
end in `_tokens`, rather than by widening the gate.

### 9.5 Outstanding

- **`unframed_requests` is staged but not applied.** It needs a `data-prepper`
  restart on m3, and ssh to that host is refusing the key — publickey offered
  and probed, signature rejected, then falling back to a password prompt. Until
  that restart, router spans cannot reach the pipeline.
- **Span export currently requires Langfuse credentials.** The instrumentation
  goes through the Langfuse SDK, which is inert without a key pair, so the OTLP
  sink rides along with it. The `TracerProvider` is deliberately built
  independently of those credentials, so decoupling is a small change — but it
  is not done.
- Retention and access control are unchanged from §1's "Explicitly NOT solved":
  both stores retain indefinitely, and OpenSearch runs with the security plugin
  disabled.

---

## 10. P2 is wrong — Claude Code traces ARE reachable

Added after §9, and it overturns this spec's central premise. P2 states that
Claude Code does not emit OTLP spans and that "this is not a configuration
problem". It is a configuration problem. There are **two** gates, and every
version of this spec found only one.

### 10.1 The second gate

```
OTEL_TRACES_EXPORTER=otlp                  # already set  [VERIFIED]
CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1      # was NOT set  <- the missing gate
```

Verified two independent ways in the 2.1.267 binary:

1. `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA` appears **6 times**, alongside
   `ENABLE_ENHANCED_TELEMETRY_BETA` (4), `BETA_TRACING_ENDPOINT` (7) and
   `ENABLE_BETA_TRACING_DETAILED` (6) `[VERIFIED]`.
2. The binary's own admin settings help text says it outright `[VERIFIED]`:

   > "Enables Claude Code's session tracing
   > (`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` + `OTEL_TRACES_EXPORTER=otlp`)
   > … Each user interaction exports a trace whose spans and events carry
   > `trace_id`/`span_id` … metrics do not carry trace context; correlate
   > those via `session.id`."

That is product documentation, not inference from strings — and it is the
sentence that explains every measurement in this spec: traces configured, the
pipeline provably working, and zero `claude-code` spans ever.

This is **not** the Anthropic-internal path. `BETA_TRACING_ENDPOINT` is
internal and was deliberately not pursued (§8.1). This flag points at the
endpoint already configured in the workspace env — the operator's own Data
Prepper behind their own Traefik — so enabling it is a configuration change,
not a data-egress decision.

**Applied:** `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` appended to the workspace
env file. It takes effect on the next session, because the environment is read
at startup — so it is unverified by construction until then. §6.0 is the test.

### 10.2 What this changes

P2 is the load-bearing premise under §2.1's "not usable" verdicts, §3.1's
derived tree, the two-level span convention, N4, N5, §4.3's closures, and the
decision to move audit into the app panel. **None of those are retracted
here** — a derived tree and an honest liveness indicator are worth having
regardless, and the spec's own §6.0 exists precisely so this premise can be
re-checked rather than assumed. But the sequencing changes: once spans arrive,
Trace Analytics and the service map become usable for Claude Code, and the
`agentBreakdown`/`query_source` workaround stops being "the only subagent view
that will ever exist".

Re-run §6.0 after the next session starts. If `serviceName: claude-code`
appears in `otel-v1-apm-span-*`, revisit §2.1, §3.1 and N4 against real spans
rather than against this spec's assumption.

### 10.3 Reported but NOT verified by me

The refinement pass reported, from decompiling the `claude_code.tracing`
module, that under the flag five spans emit (`claude_code.interaction`,
`llm_request`, `tool`, `tool.execution`, `tool.blocked_on_user`); that
`subagent.spawn`, `bash.subprocess`, `mcp.rpc` and `compaction` are hard-
disabled behind a function that returns constant false; and that `llm_request`
and `tool` spans carry `agent_id` and `parent_agent_id`, which would make
subagent attribution an attribute join rather than the `parentSpanId` walk the
earlier draft proposed — and would therefore partly reopen P3/N4.

I did not verify any of that, and it should not be built against until
observed in landed spans. The flag itself is verified; this span inventory is
not. Treat §10.2's re-check as the way to establish it.

### 10.4 Correction to §9.2

§9.2 reported Data Prepper's trace source as gRPC-only and resolved it with
`unframed_requests: true`. The first half is right; the conclusion was wrong,
and it came from a wrong choice of mine — I used the OTLP/HTTP exporter because
it was the one already installed, then changed the server to accept it.

The fleet is already configured for gRPC: `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`
against `https://otel-traces.<domain>`, a Traefik route terminating TLS and
forwarding h2c `[VERIFIED]`. That path works. **The server needed no change;
`unframed_requests` is reverted**, and the router now selects its transport
from the standard protocol variables, defaulting to gRPC.

An HTTP exporter here would have failed **silently** — a span exporter's errors
never reach the caller, so the sink would have been empty with nothing in the
logs to explain it. That is the more useful lesson than the port detail.

### 10.5 §9.5's blocker is resolved

The m3 ssh problem is no longer load-bearing: nothing needs restarting, because
the gRPC path was already live. Verified end to end against the live pipeline,
with the real provider, `BatchSpanProcessor`, `ContentStrippingExporter` and
gRPC exporter — one span carrying a unique canary in seven content-shaped
attributes:

- Both spans landed in `otel-v1-apm-span-*`, `serviceName: phantom-router`,
  with parent/child nesting and `resource.attributes.workspace: personal`
  `[VERIFIED]`
- All five metadata attributes survived, both token counts included
  `[VERIFIED]`
- The canary appears in **zero** documents across `otel-v1-apm-span-*`,
  `logs-otel-*` and `metrics-otel-*` `[VERIFIED]`

The content-stripping invariant therefore holds through the real encoder and
the real transport, which is the part no unit test could establish.

### 10.6 Deployed and verified on m3

The router span exporter is live, not merely proven in a probe.

**What changed on the host** (m3 = `100.89.35.33`; note `m3-64.neverprepared.com`
and that address both resolve to a machine reporting the same tailscale
hostname as the workstation, which is why an earlier attempt looked like it was
talking to localhost):

- `phantom-router` checkout moved from `feat/llm-langfuse-tracing` @ `81f700d`
  to `feat/llm-otel-spans` @ `31b391e` (pushed to origin; the two branches had
  diverged histories, so a new branch was used rather than clobbering the
  existing one).
- `docker-compose.override.yml` — the host-local, gitignored delta file already
  in the compose invocation — gained three keys on `services.router.environment`:
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: http://data-prepper:21890`,
  `OTEL_EXPORTER_OTLP_PROTOCOL: grpc`, `OTEL_SERVICE_NAME: phantom-router`.
  The **internal** compose address, so no TLS and no public Traefik hop.
  Timestamped backups sit beside it.
- Router image rebuilt (the previous image had no `opentelemetry` at all) and
  the container recreated.

**Verified** with one real completion through `POST /api/llm/complete`
(backend ollama, model `qwen3:8b`, reply `OK`):

| Check | Result |
|---|---|
| Spans landed | 2, correctly nested — `generate-completion` (root) → `ollama` |
| Provenance | `llm@caller=api`, `llm@profile=personal`, `llm@quality=cheap`, `llm@seam_trace_id` |
| Model | `gen_ai@response@model=qwen3:8b` |
| Profile scoping | `resource.attributes.workspace=personal` |
| Content canary | the probe marker was embedded in the PROMPT; it appears in **0** docs across `otel-v1-apm-span-*`, `logs-otel-*`, `metrics-otel-*` |

### 10.7 Two gaps this deploy exposed

- **Ollama reports no token counts or cost.** The real call produced
  `gen_ai@usage@input_tokens = 0` and `llm@cost_usd = 0.0` `[VERIFIED]`; the
  non-zero values in the index are from a synthetic probe. So the span index
  can show ollama *latency and fallback shape* but not its spend. Whether that
  is the seam passing zeros or the ollama backend not surfacing usage was not
  determined.
- **`session.id` is absent on spans from the stateless endpoint.**
  `tracing.session()` is wrapped by `conversation_runtime` and
  `conversation_orchestrator`, not by `POST /api/llm/complete`, so
  endpoint-driven completions group by trace but not by session. Expected, and
  worth closing if that endpoint ever needs session grouping.

### 10.8 Still not applied on m3

`phantom-platform` on m3 remains on `main`. The index-template fix (§3.2) and
the provisioner extension (§9.3) are NOT on that checkout — the dashboards were
provisioned directly against the live instance, and the template fix needs the
branch plus a `data-prepper` restart before it affects newly created indices.
The tracked `docker-compose.yml` wiring for the router (committed) is likewise
not what m3 uses; m3 uses the override above.

---

## 11. Claude Code spans ARRIVED — measured, not predicted

`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` worked. `serviceName: claude-code` is
in `otel-v1-apm-span-*`: **210 spans across 2 traces**, 208 of them carrying a
`parentSpanId` `[VERIFIED]`. P2 is now empirically dead, not merely suspected
wrong. Everything below is measured against those spans.

### 11.1 The span inventory — §10.3's claims adjudicated

Two of the three unverified claims hold; the most consequential one does not.

**CONFIRMED — the five emitting spans, exactly as reported:**

| Span | Count | p50 latency | p90 |
|---|---|---|---|
| `claude_code.interaction` | 2 | 739 s | 1,477 s |
| `claude_code.llm_request` | 54 | 12.3 s | 22.1 s |
| `claude_code.tool` | 52 | 336 ms | 36.8 s |
| `claude_code.tool.execution` | 51 | 333 ms | 36.9 s |
| `claude_code.tool.blocked_on_user` | 52 | 5 ms | 10 ms |

`tool.blocked_on_user` is NOT rare — it fires on essentially every tool call,
carrying `decision` (`accept`) and `source` (`config`). It records the
permission *decision*, including an auto-accept, rather than a human wait. At a
5 ms p50 it also quantifies permission overhead as negligible.

**CONFIRMED — the hard-disabled set.** `claude_code.subagent.spawn`,
`bash.subprocess`, `mcp.rpc`, `compaction` and `hook`: **zero spans each**
`[VERIFIED]`. The `T3(){return!1}` gate holds.

**REFUTED — `agent_id` / `parent_agent_id`.** §10.3 reported that `llm_request`
and `tool` spans carry these, which would make subagent attribution an
attribute join and partly reopen P3/N4. They are present on **0 of 210 spans**,
and so is `query_source` `[VERIFIED]`. **P3 and N4 stand unchanged: a tool call
still cannot be attributed to the agent that ran it.** With `subagent.spawn`
disabled there is no subtree root for delegated work either, so the span tree
does not rescue attribution. The scepticism in §10.3 was warranted — this was
the one claim to not build against.

### 11.2 What spans give that the logs never did

Per-span attributes, from a real `llm_request`: `gen_ai@request@model`
(`claude-opus-5[1m]`), `gen_ai@response@id`, `gen_ai@response@finish_reasons`,
`gen_ai@system` (`anthropic`), `input_tokens`, `output_tokens`,
**`cache_read_tokens`** (365,626 on one request), `cache_creation_tokens`,
**`ttft_ms`** (1,179), `stop_reason`, `attempt`, `speed`, `success`,
`client_request_id`. A `tool` span carries `tool_name`, `tool_use_id` /
`gen_ai@tool@call@id`, and `duration_ms`.

Three of these are genuinely new:

- **`ttft_ms`** — time to first token. Nothing in `logs-otel-*` carried it.
- **A real tree.** 208/210 spans are parented, so Trace Analytics waterfalls
  and the service map work for Claude Code — which §2.1 marked "not usable".
- **`tool_use_id` on tool spans**, which joins a tool span to the model call
  that requested it.

### 11.3 Narrowing §9.1: the API-failure monitor IS implementable — on spans

§9.1 states that `success` exists only on `tool_result` and that an
API-failure monitor is therefore unimplementable. That is correct **for
`logs-otel-*`** and must be read that way. On the span index,
`claude_code.llm_request` carries `success` — 54 of 54, all `true`
`[VERIFIED]`. So a model-call failure rate is computable from spans even though
it is not from logs. The §3.6 monitor is revived, against a different index.

### 11.4 A NEW trap: every span attribute is a keyword

Data Prepper's built-in `trace-analytics-raw` template maps **all** of
`span.attributes.*` as `keyword` `[VERIFIED from the live mapping]`. So the
numeric ones are not aggregatable: `sum`/`percentiles` over
`span.attributes.ttft_ms`, `input_tokens`, `cache_read_tokens` or
`duration_ms` all return null, even though the values are plainly there on the
documents.

This is the same class of defect §3.2 fixed for `logs-otel-*`, and the §3.2 fix
does **not** cover it — that patch edits the `otel-logs` pipeline, while traces
go through `otel-traces-raw` with `index_type: trace-analytics-raw`, whose
template is Data Prepper's own.

**The workaround is sufficient for latency and needs no change:** the
top-level `durationInNanos` is a `long`, and every latency figure in §11.1 was
computed from it, including per-tool percentiles (Bash p50 397 ms / p90 36.9 s;
Write 28 ms; Read 9 ms). **Token and ttft aggregation is blocked** until the
span template pins those fields. Not attempted here: overriding a built-in
Data Prepper template is a larger change than this work, and the per-document
values remain readable in a drill-down.

### 11.5 Correction: the span PII is not new

The spans carry `user@email`, `user@account_id`, `user@account_uuid`,
`user@id`, `organization@id` and `terminal@type` on every document. I was about
to report that as a new exposure introduced by enabling the flag. It is not:
the same email already appears in **390,208** `logs-otel-*` documents and
**169,133** `metrics-otel-*` documents, against **211** in the span index
`[VERIFIED]`. The identity attributes have been pervasive in these stores all
along.

This changes nothing about the standing concern and sharpens it: both stores
hold operator PII, `DISABLE_SECURITY_PLUGIN=true`, and there is no retention
policy — see §1's "Explicitly NOT solved". Enabling traces added ~0.05% to an
existing exposure; it did not create one.

---

## 12. Decision reversed: Langfuse owns session reading

The operator's judgement, and it is correct: *"I'm not sure this is as easy to
read as langfuse is, even if it's not reporting 100% of the data."*

Langfuse is a purpose-built trace reader; OpenSearch Dashboards is an analytics
tool. And the specific thing asked for first — *"prompt & completion capture…
click to get more details"* — is precisely what OpenSearch does not have:
content is stripped from spans by design (§10.4) and the log attributes read
`<REDACTED>` (N2). The drill-down was always going to be a flat event list with
no conversation in it. That is structural, not a polish gap.

### 12.1 What made this cheap

Two facts, both measured today:

1. **Nothing on the OpenSearch side reads spans.** All ten dashboard panels
   resolve to `logs-otel` or `metrics-otel`, and the app's Go read layer has 19
   index references — 10 `logs-otel-*`, 9 `metrics-otel-*`, **zero** to
   `otel-v1-apm-span-*` `[VERIFIED]`. So moving traces costs the dashboards,
   the cost figures, the failure view and the Agent Work panel nothing.
2. **Native OTLP removes the objection that disqualified Langfuse.** The
   46–82% subagent miss (the plugin's own issue #100: 18 of 220) was the
   transcript HOOK's failure, not Langfuse's. Spans come from the binary, so
   delegated work is not skipped. Langfuse 4.63.0 ingests OTLP at
   `/api/public/otel/v1/traces` `[VERIFIED]` — and classifies
   `claude_code.llm_request` as a **GENERATION** from its `gen_ai.*`
   attributes, so it renders as a real generation rather than an opaque span
   `[VERIFIED]`.

### 12.2 The split, as configured

| Signal | Destination | Protocol |
|---|---|---|
| **traces** | Langfuse `/api/public/otel/v1/traces` | http/protobuf |
| logs | Data Prepper → `logs-otel-*` | grpc |
| metrics | Data Prepper → `metrics-otel-*` | grpc |

Per-signal endpoints and a per-signal protocol override, so OpenSearch keeps
everything it was good at and Langfuse gets the one job it is better at.

Configured in `.envrc` rather than `.env` because the `Authorization` header is
**derived** from `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` at shell load — a
dotenv file cannot do command substitution, and writing the base64 literal
would put a second copy of the secret on disk. Rotation is therefore a
one-place change. Fail-safe: with the keys absent the block is skipped and the
Data Prepper trace endpoint stays in force, so a missing credential degrades to
the previous behaviour instead of breaking export.

**Verified** with an export driven by the ambient environment alone — nothing
configured in the probe — so a malformed or line-wrapped derived header would
have 401'd. It did not; the trace appeared in Langfuse `[VERIFIED]`.

### 12.3 The content gap is upstream of Langfuse

Worth stating precisely, because it decides where a contribution would go:

| Path | Coverage | Content |
|---|---|---|
| Native OTLP spans | complete (from the binary) | **none** — spans carry metadata only |
| Transcript hook / plugin | ~18% of subagent work | full prompt + completion |

So the two are complementary and both are left enabled: they feed the same
Langfuse project and coexist (observed: native `claude_code.*` names sitting
alongside hook-produced `Claude Generation 15` / `Tool: Bash`).

Closing the gap properly is **not a Langfuse bug**. Spans carry no message
text because Claude Code does not put it there — the `OTEL_LOG_*` content flags
apply to LOGS, not spans. A useful upstream contribution therefore splits:

- **To Langfuse** — subagent walking in `claude-observability-plugin` (#100),
  which is where the 18-of-220 number comes from.
- **To Claude Code** — content on spans, or a documented way to correlate the
  content-bearing log records to a span by `trace_id`/`span_id`. The binary's
  own help text says spans and events carry `trace_id`/`span_id` and that
  metrics do not, so the correlation key may already exist on the log side;
  that is the first thing to check.

### 12.4 Rotation is now blocking

The Langfuse secret key — leaked earlier in this session and still unrotated —
is now on the live authentication path for every session's trace export. It was
previously only used by the hook. Rotate it in the Langfuse UI (project
settings → API keys), update the single value in `$WORKSPACE_HOME/.env`, and
run `direnv reload`; the header is derived, so nothing else needs touching.

### 12.5 The OTLP path does not carry the environment

A fresh session did not appear in the panel, and its observations were visible
in Langfuse under environment **`default`** rather than `personal`. The
operator spotted this from the Langfuse UI; it is a real defect in §12.2 as
shipped.

**Cause.** `LANGFUSE_TRACING_ENVIRONMENT` is read by the Langfuse *SDK*, so the
transcript hook files its observations under it. The OTLP path knows nothing
about that variable. Langfuse takes the environment from a **resource
attribute**, and nothing was setting one — so every native span landed in the
default bucket and was invisible to a panel scoped to `personal`.

**Which attribute.** Probed against the live deployment rather than assumed —
all three work `[VERIFIED]`:

- `langfuse.environment`
- `deployment.environment`
- `deployment.environment.name`

`deployment.environment` is the OTel semantic convention, so it is the portable
choice and is what was configured.

**Fix.** `.envrc` appends it to the existing resource attributes, derived from
the same stamp so the two cannot drift:

```
OTEL_RESOURCE_ATTRIBUTES = workspace=personal,deployment.environment=personal
```

Appended, never assigned: `.env` already sets `workspace=personal` and an
assignment would drop it. Verified by emitting a span with **only** the ambient
environment — it filed under `personal` `[VERIFIED]`.

**Two consequences worth stating.**

- The environment is read at process start, so a session already running keeps
  writing to `default` until it is restarted. One more restart is needed after
  this change.
- Observations already filed under `default` do not move. At the time of
  writing: `personal` 2,208, `default` 115 `[VERIFIED]`.

**The general hazard.** Any harness that exports OTLP to Langfuse without this
resource attribute silently lands in `default` and disappears from every
environment-scoped view. That is a quiet failure with no error anywhere, so it
belongs in the setup checklist for each new harness rather than being
rediscovered. The router is unaffected — it exports to Data Prepper, not
Langfuse — but anything pointed at Langfuse directly needs it.
