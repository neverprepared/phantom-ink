# ADR-001: Agent Orchestration Direction — A2A Façade + Per-Step Model Router; Defer LangGraph

- **Status:** Accepted
- **Date:** 2026-06-28
- **Deciders:** Platform owner (mindmorass)
- **Related:** [`ORCHESTRATION.md`](./ORCHESTRATION.md), [`../codex-integration.md`](../codex-integration.md)

---

## Context

The fixed goal for phantom-ink is **autonomous agent-to-agent (A2A) work**, with the ability to **route a task between Claude, Ollama, Codex, and other models/CLIs** over time. A new enabler is now in place: a centralized, shareable memory store via the **phantom-brain MCP** (a shared HTTP daemon addressed by `CL_BRAIN_API`, backed by OpenSearch + MinIO), which gives every agent a common long-term memory.

The owner asked for competing designs, with an explicit lean toward "A2A tooling with **LangGraph** to help orchestration instead of our custom chains." Three architectures were drafted independently and then checked against the current code. **Grounding them against the codebase changed the picture decisively: phantom-ink is already ~70% of the way to the goal, and its orchestration engine is not "custom chains" — it is a real durable workflow engine.**

### Verified ground truth

- **brainbox already has a durable workflow engine.** `brainbox/src/brainbox/router.py:283-368` (`suspend_task` / `resume_task`) plus `brainbox/src/brainbox/models.py:127-151` implement suspension kinds **HUMAN / JOIN / SCHEDULE / CHILD** with scheduler auto-resume, `resume_payload` merge, and the property that *suspended tasks do not consume queue slots*. That is fan-out/fan-in, wall-clock timers, and human-in-the-loop — the exact primitives a graph framework markets — already built.
- **A loops consolidation is already in flight.** On branch `feat/loops-markdown-cutover`, `app/sequences.go:28-33` demotes the host-side type to "the authoring surface in the desktop app and the host-side runtime for the trivial 1-iteration case," while "the rich convergence / iteration primitives live on the brainbox-side SequenceSpec." `app/sequences.go:73-82` reserves an `Executor` field (`"host"` wired today; `"session"` / `"queue"` reserved). `app/app_loops_runtime.go:10-13` calls the brainbox loop runner the "proper convergence/iteration" engine versus "legacy local-SQLite … bookkeeping."
- **Multi-model already works per session.** `SessionContext.llm_provider: Literal["claude","ollama","codex"]` with `llm_model` / `llm_effort` / `ollama_host` / `codex_api_key` overrides (`models.py:86-90`); per-agent defaults on `AgentDefinition` (`claude_model`, `claude_effort`, `codex_model`, `ollama_model`, `models.py:31-34`); provider-specific env injection in `lifecycle.py:1027-1044`. Claude runs under **OAuth, no API keys** (see CLAUDE.md "No API Keys for Agents"). aider/gemini/opencode exist only in the host CLI catalog (`app/agents.go`), not yet in brainbox.
- **A2A is the one genuinely-missing standard piece.** No `/api/a2a/*` routes exist (confirmed). Cross-agent coordination today runs over `/api/hub/messages`, channels, and the task lifecycle.

---

## Decision

1. **Engine fork → defer LangGraph; mature the engine we own.** The router/loop engine already covers what LangGraph markets. Adopting LangGraph now would *duplicate the suspension engine* mid-cutover, add Python dependency churn, and entrench a language boundary — for capability we already have. Adopt LangGraph only when **true multi-node branching DAGs** force it (consistent with the existing `project_loop_multinode_todo` decision to defer the Node+Edge graph until PR-review-and-fix forces it).
2. **Model routing → first-class now, for claude/ollama/codex.** Lift the scattered per-session selection into one per-step `ModelTarget`, with an extensible provider enum + env-injection branch so future providers (gemini/aider/opencode) slot in without a new subsystem.
3. **Build the two framework-independent enablers first:** the **A2A façade** (interop + autonomous cross-agent work) and the **per-step model router**. Both are valuable regardless of which orchestration engine wins later, so they are low-regret.
4. **Finish the loops consolidation** so brainbox is the single orchestration engine and the host app is the authoring surface + trigger source.

---

## Options Considered

### Option A — Standardize the wire, minimal new runtime
Build an A2A façade over the existing hub/task/session primitives; expose provider as a request parameter; write essentially no new orchestration runtime; rely on the existing queue + scheduler + automation + loops.
- **Strength:** smallest surface; honors "standard tools, minimal custom code"; the A2A `input-required` state maps directly onto `SuspensionKind.HUMAN`.
- **Weakness:** no first-class branching graph; provider routing is "dumb" (caller decides, no cost/latency awareness); the façade translation layer accretes edge-case glue over time.

### Option B — LangGraph as the single brainbox runtime
Adopt LangGraph OSS (self-hosted; **not** LangSmith/Platform) in brainbox; collapse Sequences + Loops + Playbooks into graphs; graph nodes dispatch to OAuth sessions (no API keys); a checkpointer holds control state while phantom-brain holds knowledge; map suspension kinds onto LangGraph interrupt/checkpoint/fan-out.
- **Strength:** one orchestration brain; native conditional/cyclic graphs; off-the-shelf graph ergonomics; nodes can be A2A clients and a graph can be exposed as an A2A agent.
- **Weakness:** **duplicates** the router suspension engine that already exists; Python dependency churn; Go↔Python boundary for debugging; highest migration risk to retire two engines mid-cutover; overkill for the linear flows that dominate today.

### Option C — Mature the core you own (**chosen**)
Consolidate onto loops, wire the reserved `Executor` so one orchestrator runs host *or* container steps, lift provider selection into a `ModelTarget`, and add a thin A2A skin for interop.
- **Strength:** builds on a verified, capable engine; the expensive part (durable suspension/resume) is already done; the missing parts are orthogonal to the engine choice; deferring LangGraph is cheap and reversible.
- **Weakness:** we own the suspension/graph semantics forever (e.g. resume-payload merge and circular-suspension deadlock are ours to debug); risk of incrementally reinventing a worse, undocumented LangGraph; no off-the-shelf graph observability.

### Why C won
The verified router suspension model plus the in-flight loops cutover mean the costly engine work is already built. The two genuinely-missing capabilities — A2A interop and per-step model selection — are independent of the engine choice and are useful under **either** future. Deferring LangGraph is reversible and low-cost; committing to it now is disruptive and partly redundant. The honest conditions under which B beats C are recorded in Phase 4.

---

## Target Architecture

- **brainbox = the orchestration engine** — loops + router suspension/resume + scheduler + runners (`select_runner`) + Ollama pool.
- **Host Wails app = authoring surface + trigger source** — `queue.go` / `scheduler.go` / `automation.go` enqueue and trigger work that brainbox executes; the host stops being a parallel orchestration runtime (matches the cutover trajectory).
- **A2A = the boundary protocol** — a thin skin over hub/task/session for external interop and cross-agent calls.
- **phantom-brain = shared cross-agent knowledge** — durable handoff payloads stored by reference; the A2A task/wire carries only a key, not the blob. Distinct from control-flow state, which stays in the router/loop engine.
- **ModelTarget = per-step provider/model selector** — resolved into `SessionContext` before the session is spawned.

```
external A2A client ─┐
                     ├─ /api/a2a/* (façade) ─ router.submit_task ─ loop/router engine
agent ⇄ agent (A2A) ─┘                                   │
                                          ModelTarget → SessionContext → session (OAuth, no keys)
                                                         │
                              phantom-brain (CL_BRAIN_API) ── shared knowledge, by reference
```

---

## Phased Roadmap

Each phase is a future build plan, not built by this ADR.

### Phase 1 — A2A façade *(low-regret; unblocks autonomous A2A + interop)*
New `brainbox/src/brainbox/a2a.py`, mounted in `api.py` — thin adapters over existing primitives:
- `GET /.well-known/agent.json` per agent ← generated from `AgentDefinition` (`brainbox/agents/*.json`); capabilities → A2A skills.
- `POST /a2a/tasks/send` ← `router.submit_task()`; the A2A Task maps onto the brainbox `Task` (states already align).
- `GET /a2a/tasks/{id}` and stream ← `router.get_task` + the existing SSE bus (`/api/events`, `router.on_event`).
- A2A `input-required` ↔ `SuspensionKind.HUMAN` via `suspend_task` / `resume_task`.
- A2A messages ↔ `messages.route()` / channels.
- Auth bridge: map A2A Bearer onto the existing hub `Token` / `require_api_key`.
- **Open questions to settle at build time:** which agents ship in v1 (all four — supervisor/worker/assistant/reviewer — or just supervisor + worker); which A2A spec version to pin.

### Phase 2 — Per-step `ModelTarget` *(the "pass between models" goal)*
- Introduce `ModelTarget{provider, model, effort}` resolved per step and written into `SessionContext` before spawn — reuse the env injection at `lifecycle.py:1027-1044` and the create→query→stop pattern in `playbooks.py::_run_task`.
- Widen the provider enum + add env/auth branches as new providers arrive (gemini/aider/opencode later; the host catalog in `app/agents.go` already carries their invocation specs).
- Expose `provider` / `model` as A2A skill inputs, so "Claude drafts → Ollama reviews" is just a second A2A task with a different `ModelTarget`.
- Preserve the OAuth / no-API-keys invariant throughout.

### Phase 3 — Finish loops consolidation
- Wire the reserved `Executor` (`app/sequences.go:73-82`): `"host"` (today) | `"session"` (brainbox session dispatch — also resolves the `// TODO: sandbox` at `sequences.go` by routing into a containerized session) | `"queue"` (enqueue + CHILD/JOIN suspend, scheduler-resumed).
- Demote host Sequences to authoring + the trivial 1-iteration case; retire the host "legacy SQLite loop" bookkeeping once `Executor` routing lands.
- Fold Playbooks into loop steps. Keep `queue.go` / `scheduler.go` / `automation.go` as trigger sources.

### Phase 4 — Deferred LangGraph re-evaluation *(trigger-gated)*
Re-open **only** when multi-node branching graphs are actually needed (`project_loop_multinode_todo`). If adopted, slot LangGraph OSS (self-hosted; not LangSmith/Platform) in as a node-executor **behind the same A2A skin and `ModelTarget`**, so Phases 1–2 are not wasted. **Decision-flip conditions (must both hold):** (a) dense conditional/cyclic graphs with per-edge state that outgrow markdown loops, **and** (b) a deliberate Python-standardization choice that makes the language boundary a feature rather than a cost. Secondary pull factors: wanting an off-the-shelf graph-replay/observability ecosystem, or hiring against a known framework.

---

## Keep / Retire / Mature

| | Items |
|---|---|
| **Keep** | `router.py` suspension/resume; scheduler; runners `select_runner`; Ollama pool; `app/queue.go` / `app/scheduler.go` / `app/automation.go` / outbox; phantom-brain memory; session OAuth provider injection (`lifecycle.py:1027-1044`). |
| **Retire** | Host Sequences as a distinct orchestration engine (→ authoring only); Playbooks as a separate user concept (→ loop steps); host "legacy SQLite loop" bookkeeping once `Executor` routing lands. |
| **Mature (custom code)** | The A2A façade; the `ModelTarget` selector; the `Executor` dispatch. |

---

## Consequences

- **Positive:** lowest-regret next steps; preserves a verified durable engine; the no-API-keys/OAuth invariant is untouched; A2A unlocks ecosystem interop; phantom-brain becomes the shared-state backbone for autonomous groups; the LangGraph door stays open without a premature bet.
- **Negative / risks:** we continue to own subtle orchestration semantics (resume-payload merge, circular suspension); no off-the-shelf graph observability until/unless Phase 4; the A2A façade translation layer needs discipline to stay thin; retiring two engines mid-cutover (Phase 3) needs careful sequencing so triggers never point at a half-migrated path.

---

## Cross-links

- [`docs/architecture/ORCHESTRATION.md`](./ORCHESTRATION.md) — current host→container orchestration flow.
- [`docs/codex-integration.md`](../codex-integration.md) — multi-provider integration background.
- Vault notes: `project_a2a_integration_todo`, `project_autonomous_agent_workflows`, `project_loop_multinode_todo`, `project_agent_chain_sandbox_todo`, `project_workflow_roles_redesign_todo`.
