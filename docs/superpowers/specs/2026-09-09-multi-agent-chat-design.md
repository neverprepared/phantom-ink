# Multi-Agent Chat — Design Spec

**Date:** 2026-09-09
**Status:** Approved (design shape). Implementation phased across 4 sequential PRs.
**Supersedes:** the existing `Conversations` feature (in-memory hub channels + Ollama watcher + per-participant Docker bootstrap).

## 1. Motivation

The current `Conversations` feature is a real capability wearing the wrong clothes: it was built as a heavy agent-orchestration tool (Docker-session participants, per-participant tmux bootstrap, in-memory/JSON hub state) and presented as a chat surface. Concretely it suffers from:

- **Not real-time** — the UI polls every 5s; a per-conversation SSE stream exists but is never subscribed. 5s latency floor makes chat feel dead.
- **Heavy/flaky participants** — session participants need a Docker container + tmux prompt injection; if the container is not booted, bootstrap silently fails and the UI shows the agent "joined" when it never received the prompt.
- **No turn-taking** — every Ollama participant replies to every message; multiple agents pile on. `addressed_to` exists in the data model but nothing enforces or encourages it.
- **Side-silo** — channels live in their own in-memory/JSON hub, disconnected from the local-first store, the event bus, and the brain.

## 2. Goals / decisions (locked)

1. **Flexible room** — one room supports a human + N agents freely. The human can drive, or kick it off and let agents converse, stepping in at will.
2. **Lightweight personas, promotable** — a participant is by default a lightweight LLM persona (`{model_target, role prompt, cooldown, tools?}`) running via the `complete()` LLM seam (Ollama or Claude, swappable). When real work is needed, a persona is **promoted** to a full session that executes and reports back.
3. **Self-gated turn-taking** — each eligible persona independently judges whether it has something worth adding (a cheap relevance gate), with a concurrency cap, a cooldown, and a quiet-detector to stop runaway agent-to-agent loops.
4. **Platform-native** — conversations persist in the local-first store (profile-scoped, P2P-syncable); messages emit bus envelopes (visible in Stream/Timeline); a message can be promoted to brain (memory), a todo, or a task.
5. **Strategy: rebuild the engine, reuse the shell** — replace the in-memory hub / Ollama-watcher / per-participant Docker bootstrap; keep the Svelte `ConversationsPanel` shell and the `complete()`/session/job infra.

### Tunable defaults (config, easy to change)
- Concurrency cap: **1–2** responders per round.
- Quiet after **4** consecutive agent-only turns (or when no persona passes the gate).
- MVP personas are **text-only**; tool work happens via promotion.

## 3. Architecture

```
ConversationsPanel (Svelte, reused shell)
   │  per-conversation SSE (message.created / delta / done / thinking / participants)
   ▼
Wails/Go bridge (per-conversation stream — the wiring missing today)
   ▼
Conversation API (REST CRUD + SSE)  ── brainbox
   ├─ ConversationStore   (local-first store: Conversation + Message records)
   ├─ TurnOrchestrator    (event-driven relevance gate + cooldown + cap + quiet)
   ├─ Persona runner      (complete() seam — Ollama / Claude, streaming)
   └─ SessionPromotion    (existing session/job infra; results stream back as messages)
   │
   ├─ Event bus           (message envelopes → Stream/Timeline)
   └─ Promote actions     (brain / todo / task — reuse existing endpoints)
```

### Components (each independently testable)
- **ConversationStore** — CRUD over `Conversation` and `Message` records in the local-first store. Owns profile scoping and P2P fields (ULID id, node_id, tombstone). Replaces the in-memory dicts + JSON flush.
- **TurnOrchestrator** — pure-ish decision engine: given a conversation's messages + participants + a `complete()` handle, decides who speaks next and drives their streamed reply. Unit-testable with a fake `complete()`.
- **Persona runner** — turns a persona + conversation history into a streamed `complete()` call; also runs the cheap relevance gate.
- **SessionPromotion** — wraps existing session/job machinery; seeds a session with conversation context + task, relays its output back as `kind=session` messages.
- **Conversation API** — REST + per-conversation SSE; enforces profile scoping on every read/write.

## 4. Data model (local-first, profile-scoped)

**Conversation**: `id (ULID), profile, title, status (active|archived), participants[], created_at, node_id, tombstone`

**Participant**: `name, kind (human|persona|session), model_target?, role_prompt?, cooldown_s?, joined_at`

**Message**: `id (ULID), conversation_id, author, kind (message|system|join|tool|session), content, addressed_to?, in_reply_to?, created_at, node_id, tombstone`

Records live in the local-first store → P2P sync and profile scoping come for free. Streaming state (partial content) is transient (SSE deltas); the persisted record is the finalized message.

## 5. Turn orchestration (the hard part)

- **Event-driven**, not a fixed poll. A new message enqueues one **evaluation round** for its conversation.
- **Eligibility** per persona: not the message author, off cooldown, and not excluded by an `@address` on the triggering message.
- **Relevance gate**: each eligible persona runs a cheap `should_respond` check (a small/fast `complete()` call returning a boolean + confidence, or a cheaper model). This is the self-gate.
- **Concurrency cap**: only the top *N* (default 1–2) by confidence actually reply this round; the rest defer. Prevents pile-ons.
- **Cooldown**: each reply starts that persona's cooldown; a persona on cooldown is ineligible next round.
- **Chaining**: each posted reply is a new message → triggers the next round, so agents build on each other.
- **Quiet-detector**: after *K* (default 4) consecutive agent-only turns, or when no persona passes the gate, the room goes **quiet** awaiting the human. Kills runaway loops.
- **`@address`**: an addressed persona bypasses the gate (always answers); non-addressed personas stay silent for that message.

## 6. Personas & promotion

- Personas defined per-conversation (a reusable persona library is out of scope — YAGNI).
- `model_target` selects an Ollama model or Claude via the `complete()` seam.
- MVP personas are text-only; an optional small read-only toolset may come later. **Real tool/file work is the promotion path.**
- **Promotion** is explicit and user-triggered (not per-participant auto-bootstrap → this is what fixes today's silent-bootstrap-failure bug). It spins a session (existing infra) seeded with conversation context + the task; the session streams progress/results back as `kind=session` messages and can be dismissed.

## 7. Real-time delivery

Per-conversation SSE carrying: `message.created`, `message.delta` (tokens), `message.done`, `thinking` (persona is composing), and participant changes. The frontend subscribes on conversation open; the 5s polling loop is removed. The Wails/Go layer bridges the per-conversation stream (today it only bridges the main `/api/events` stream).

## 8. Platform integration

- **Bus**: each completed message emits a bus envelope keyed `conversation:<id>` → visible in Stream/Timeline; the conversation itself is a timeline entity.
- **Promote a message** → brain (`learn`), → todo (phantom-todo), → task/job (`submit_task`), reusing existing endpoints.
- **Profile scoping** is enforced server-side on every conversation read/write (fixes today's trust-based isolation, where the backend only filtered on list).

## 9. Retirement / migration

- Delete the in-memory hub channels engine, the Ollama-watcher, the per-participant Docker bootstrap, and the JSON flush.
- **Keep the `channel_*` MCP tool names** (`channel_read/send/complete/join`) but repoint them at the new store, so a promoted session still participates via the same tools.

## 10. Testing

- **Orchestrator**: relevance gate, cooldown, concurrency cap, quiet-detector, max-consecutive, `@address` — unit tests with a fake `complete()`.
- **Store**: CRUD + P2P merge/tombstone + profile scoping.
- **Streaming**: SSE contract (created/delta/done ordering).
- **Profile isolation**: cross-profile read/write rejected.
- **Promotion**: persona → session → results-back round trip.

## 11. Phasing (sequential PRs — each independently useful)

- **PR1** — ConversationStore (local-first) + REST CRUD + per-conversation SSE streaming + human↔single-persona via `complete()`. Rewire the panel to stream; drop polling. *Proves streaming, kills the latency floor.*
- **PR2** — Multi-persona + self-gated TurnOrchestrator (relevance gate, cooldown, cap, quiet-detector, `@address`). Persona-management UI.
- **PR3** — Bus envelopes + server-side profile enforcement + promote-message-to-brain/todo/task.
- **PR4** — Promote-to-session (persona → real work → results back); retire the old channels engine; migrate the `channel_*` MCP tools.

Each PR depends on the previous one; they must land in order.
