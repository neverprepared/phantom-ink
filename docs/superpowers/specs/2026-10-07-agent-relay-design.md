# Agent Relay — Design Spec

**Date:** 2026-10-07
**Status:** Draft (design shape) — pending approval. Implementation phased across 5 sequential increments with explicit gates.
**Supersedes:** nothing. Deliberately does *not* replace the conversation engine (see §10).

## 1. Motivation

Session agents are ephemeral containers. Every one starts from zero. Nothing carries fast
working knowledge from one agent generation to the next, so the fleet never compounds —
agent #40 re-walks the dead ends agent #7 already mapped.

The two existing stores both decline the job, correctly:

- **phantom-brain `memory`** is a gate-and-distill posterity archive. The synth path
  **rewrites bodies**, which is exactly wrong for "agent B needs agent A's exact finding."
- **The conversation engine** is concurrency-shaped chat: turn-taking, cooldowns, relevance
  gates, personas. It assumes participants coexist. The relay's participants never do.

The missing primitive is a **durable, verbatim, append-only relay for agents that never
coexist** — plus the read path that makes it worth writing to.

**Prior art.** The 2026 OpenAI evaluation incident is the clearest demonstration of the
value and the failure mode. Short-lived agents bootstrapped a shared blob store into a
message board and passed working exploits **between runs that never overlapped in time**;
entries were signed, labour was divided, and anti-clobber conventions emerged bottom-up.
It also shows the failure mode: hundreds of thousands of entries that nobody could use.
Capture is easy; **retrieval under volume is the whole problem.** Setting the misuse aside,
the substrate lesson is that append + enumerate + read is sufficient to bootstrap
coordination — so if any shared writable surface is reachable, a relay will emerge anyway.
Better to provide a good one.

## 2. Goals / decisions (locked)

1. **Content vs state split — this is the architectural resolution.** The recorded rule is
   *brain = content, phantom-events = state, "no state folder."* Therefore:
   - `"I tried X, it failed because Y"` is **content** → phantom-brain, verbatim vault.
   - `"I'm working on this / alive / blocked"` is **state** → phantom-events.

   The relay is content. Liveness is state. They are different systems and stay that way.
2. **Verbatim, never distilled.** Entries are stored byte-for-byte. The gate's rewrite is
   the enemy here.
3. **Negative results are first-class.** A failed approach is kept, labelled, and
   *findable*. `recall --reliability low` is a feature, not a consolation.
4. **Amend, never delete.** Outcome is knowable only *after* the write. `supersede` /
   `record_links` carries the later verdict without destroying the original claim.
5. **Two axes, not one.** `reliability` (how much do I trust this *source*) is orthogonal
   to `outcome` (did it *pan out*). A reliable source can lead to a failed approach; a
   low-confidence hunch can be right.
6. **One substrate, two readers.** Agents query it; the human reads it as narrative. There
   is no second store and no separate "supervisor console."
7. **Read path must not depend on agent discipline.** The primary read is *injected*, not
   requested. This is what makes the design serve "define and stand back."
8. **Assemble, don't build.** A verbatim vault already provides exact storage, hybrid
   retrieval, reliability filtering, supersede versioning, profile scoping, and p2p mesh
   sync. No new service.

### Tunable defaults (config, easy to change)
- Briefing slice injected at session start: **top 10 entries**, scoped to repo + role.
- Stall threshold (Phase 0): **30 min** no task-status change *and* no branch-head move.
- Briefing excludes entries whose `outcome` is `superseded-correct` (already fixed).

## 3. Architecture

```
agent (in session)                      router                      phantom-brain
  │                                       │                              │
  ├─ task update --finding ──────────────────────────────────────────▶ findings vault
  │   (verbatim, reliability, why)         │                         (kind=finding)
  │                                        │                              │
  └─ (dies / docker stop) ────────────▶ session_summary.py ──────────▶ findings vault
                                           │  narrative + facts          (kind=finding,
                                           │  + evidence handle           references live
                                           │                              entries)
                                           │
      session create ◀── briefing ─────────┤ configure phase
                                           │  (recall slice → context)
                                           │
                                  phantom-events ◀── liveness/claims (state, NOT brain)
                                           │
                                      rules engine ──▶ notify (ntfy)
```

### Components (each independently testable)

| Component | Owner repo | Responsibility |
|---|---|---|
| `findings` verbatim vault | phantom-brain | Exact storage + retrieval + supersede |
| Crash-safe publish | phantom-brain | Publish a dying process's findings before the reaper |
| Relay publish from summary | phantom-router | `session.summary` → relay entry referencing live entries |
| Briefing injection | phantom-router | `configure` phase pulls a recall slice into session context |
| Stall detector | phantom-router | Task status + `git ls-remote` poll → stall event |
| Outcome verdict | phantom-router (reviewer role) | `supersede` the entry with what actually happened |

## 4. Data model

A relay entry is a brain record with `kind=finding` in vault `findings`, stored verbatim.

| Field | Source | Notes |
|---|---|---|
| `title` | agent | One line. The claim. |
| `body` | agent | Verbatim. What was tried, what happened, **why**. |
| `reliability` | agent, on write | `high \| medium \| low \| contested`. Already settable on the verbatim path; defaults to `medium`. |
| `topic` | agent | Preserved verbatim when supplied. **No taxonomy imposed** (see §9). |
| author / node | daemon | Provenance. Signed entries mattered in the prior art. |
| `outcome` | reviewer, later, via `supersede` | See below. |
| evidence handle | router | Transcript/diff as an artifact reference, not inline. |

**`outcome` vocabulary** (deliberately small): `confirmed` · `refuted` · `partial` ·
`superseded-correct` (the problem is fixed, do not re-brief) · `unknown` (default).

**Why `outcome` lives on an amendment, not the original:** the writing agent cannot know
it. Only a later actor can. Amendment preserves the original claim *and* the correction,
which is the "keep bad information, categorised as such" requirement.

## 5. The read path (the hard half)

Writing is an MCP call. Useful reading is retrieval under volume, and it is where the
prior art broke down.

Three shapes were considered (§11 records the rejected two). **Decision: injection at
start, as the primary read.**

- The router's `configure` phase already injects per-session config; the briefing slice
  fits there. `phantom-agents` / `phantom-skills` already demonstrate on-demand vault
  loading, so the pattern is established.
- Requires **nothing** from the agent — no discipline, no prompt compliance, no tool call.
- Scoped by repo + role; ordered by reliability then recency; `superseded-correct` excluded.
- Hard cap on slice size. A briefing that blows the context budget is a regression.

Agent-initiated pull (`recall` over the vault) is added in Phase 3 as a *supplement*, never
the primary.

## 6. Liveness (state — separate system, Phase 0)

Not part of the relay, but sequenced first because it is the actual prerequisite for
standing back. The relay makes agents smarter; liveness is what stops silent failure.

Recorded history: a supervisor idled ~8h on a hung worker and lost the work. The recorded
fix is a bounded stall timer polling **both** task status **and** `git ls-remote` for the
branch head — an agent can look busy while producing nothing.

Stall → event on phantom-events → existing rules engine → existing `notify` action (ntfy).
No new infrastructure; wiring only.

## 7. Phasing, timelines and gates

Each phase ships independently useful. **A gate is evidence, not elapsed time** — the soak
durations are minimums for accumulating that evidence, not the gate itself.

Build estimates assume agent-assisted implementation by one operator.

### Phase 0 — Liveness detection
**Build: ~1–2 days.** Stall detector + event + rule + notify wiring.

**Test:** induce a stall deliberately — `docker kill` a worker mid-task — and confirm a
notification arrives within the threshold. Unit-test the dual-signal logic (status
unchanged *and* branch head unmoved) including the look-busy-produce-nothing case.

**Gate to Phase 1 (≥1 week soak):**
- [ ] One *deliberately induced* stall caught and notified.
- [ ] At least one *organic* stall caught, or a week with zero stalls and no false positives.
- [ ] Zero false positives on healthy long-running tasks. A noisy detector gets muted, and a muted detector is worse than none.

**Kill criterion:** if false positives cannot be driven to zero, stop. Do not proceed with a detector you will learn to ignore.

### Phase 1 — The relay vault, fed by what exists
**Build: ~3–4 days.** The fiddly part is phantom-brain: adding a verbatim kind touches
exactly three sites — `internal/osearch/docs.go` (the `Kind` const plus `IsValid` /
`IsVerbatim`), `verbatimKindForVault()` in `internal/server/handlers_write.go`, and a
`records_kind_chk` migration. Miss one and writes fail a check constraint at runtime
rather than at compile time. Then crash-safe publish, then `session.summary` → relay.

No new capture. `task update --finding` and `session.summary` already produce the content.

**Includes a bug fix:** the reaper currently deletes a crashed process's `wm-<PID>.sqlite`
shard without publishing it. That destroys findings precisely when handoff matters most.
Publish-then-reap.

**Test:**
- Round-trip a verbatim entry: write → `recall` → `fetch`, assert the body is byte-identical.
- Assert `reliability` survives the verbatim path and filters correctly at recall.
- Assert `supersede` preserves the original and links the amendment.
- **Kill a session mid-task and assert its findings reached the vault** (this is the
  regression test for the reaper fix).
- **End-to-end in a real container** — not mocked, not REST-only. The existing board MCP
  tools were never verified in a live container; do not repeat that.

**Gate to Phase 2 (≥2 week soak):**
- [ ] ≥20 entries accumulated organically.
- [ ] Crash-publish verified on a real killed container.
- [ ] **The readability test:** answer a real question from the relay that you could not
      have answered before, using only `recall`. If the relay is already unreadable at
      20 entries, retrieval is the problem and Phase 2 will not fix it.

**Kill criterion:** if the entries are uniformly low-value — epitaphs with no reusable
reasoning — stop and reconsider the grain before building the read path.

### Phase 2 — Briefing injection
**Build: ~2–3 days.** Recall slice in the `configure` phase, scoped and capped.

**Test:**
- Assert the slice respects the cap and never exceeds the context budget.
- Assert profile scoping: a `personal` session never receives a `gsa` entry. Cross-profile
  leakage is a bug, not a degradation.
- Assert `superseded-correct` entries are excluded.

**Gate to Phase 3 (≥2 week soak):**
- [ ] **≥1 demonstrated case of an agent not repeating a documented dead end** — the
      single most important piece of evidence in this spec. Everything else is plumbing.
- [ ] Briefing adds no measurable session-start latency regression.
- [ ] Subjective but required: briefed sessions feel like they start further along.

**Kill criterion:** if no avoided-dead-end case appears in two weeks, the relay's content
is not actionable. **Stop here.** Phases 0–2 are still a coherent, useful system. Do not
escalate to asking agents for more writes when the existing writes aren't paying.

### Phase 3 — Live posting and agent-initiated reads
**Build: ~3–5 days.** Role-prompt updates (the real fix for under-posting — currently *no*
role prompt mentions any of this), `hub_messaging`-equivalent capability gating, and a pull
tool over the vault.

**Test:** post rate per session; read rate per session; assert posting failure never fails
the task (fail-open, as `session.summary` already is).

**Gate to Phase 4 (≥2 week soak):**
- [ ] Median ≥1 agent-authored entry per session, from role text alone.
- [ ] Agents demonstrably *read* mid-task, not just write. A relay that is written and
      never read is a log.
- [ ] No runaway volume: entry rate stays within retrieval's ability to rank.

**Kill criterion:** if median posting is <1/session, **revert to Phase 2 behaviour** rather
than escalating prompt pressure. Under-posting is an affordance problem; if the affordance
isn't working, more insistent prompting is not the fix.

### Phase 4 — The outcome axis
**Build: ~2–3 days.** No new actor: the **reviewer role already verifies by executing** and
already gates merges (no PR merges on its author's word). Have it `supersede` the relay
entry with the verdict.

**Test:** assert a verdict links to the original; assert briefing honours `outcome`
(a `refuted` entry briefs as a warning, a `superseded-correct` one is dropped).

**Gate to steady state (≥3 week soak):**
- [ ] Verdicts land on ≥50% of entries that had a reviewer pass.
- [ ] ≥1 case of a confident entry downgraded, or a low-confidence entry confirmed —
      evidence the axis carries real information rather than rubber-stamping.

**Total: ~2–3 weeks of build across ~10–11 weeks calendar**, the difference being soak.
The soaks are the point: every risk in §9 is a "looks healthy, isn't" risk, and only
elapsed real use exposes those.

## 8. Testing strategy

- **Unit** — verbatim round-trip byte-equality; reliability filter; supersede linkage;
  stall dual-signal logic; briefing cap and scoping.
- **Integration** — real container, real session, real kill. The recorded failure here is
  proving MCP tools at the REST layer only and calling it done.
- **Soak** — each gate above. Non-negotiable; the failure modes are all slow.
- **Negative** — cross-profile leakage attempt must fail closed; oversized briefing must be
  truncated not dropped; a brain outage must not fail a session (fail-open throughout).

## 9. Risks

| Risk | Mitigation |
|---|---|
| **Agents under-post; relay degenerates into session-end epitaphs while looking healthy** (dominant risk) | Phase 2 before Phase 3 — prove reading pays before asking for more writing. Explicit kill criterion. |
| **Volume kills retrieval** (the prior-art failure mode) | Verbatim + no distillation makes this worse, not better. Gate on readability at 20 entries; cap the briefing; revisit grain if ranking degrades. |
| Briefing eats the context budget | Hard cap; measure session-start latency. |
| Premature taxonomy | Impose none. The prior art shows conventions emerge from use; harvest them later if they do. |
| Relay drifts into duplicating brain `memory` | Vault boundary + verbatim-vs-synth is the line. The relay is in-flight working knowledge; promotion to `memory` stays explicit. |
| Outcome verdicts become rubber stamps | Gate requires evidence of an actual downgrade/upgrade, not just coverage. |

## 10. Explicitly not doing

- **Not reusing the conversation engine.** Concurrency-shaped chat (turn-taking, cooldowns,
  personas) is wrong for a relay whose participants never coexist. It remains the
  human/persona chat surface.
- **Not building a `phantom-board` service.** Nothing here requires one.
- **Not putting findings on the durable event bus.** The recorded decision that
  `channel.message` is not a durable event stream holds; the relay is content, not state.
- **Not building the narrative UI yet.** Read via `recall` first. If it won't be read in a
  terminal, a panel will not save it.
- **Not designing a topic taxonomy upfront.**
- **Not adding a supervisor console.** Each phase is sequenced to *reduce* operator
  attention, not add a surface to watch.

## 11. Rejected alternatives

- **Push by topic** (agent declares what it's working on, gets matching entries) — requires
  a taxonomy up front, which §9 rejects, and still depends on the agent declaring.
- **Pull-only** (agent asks when it chooses) — zero new infrastructure, but depends entirely
  on agent discipline, which is the dominant risk. Kept as a Phase 3 supplement, never the
  primary read.
- **Per-session grain only** — nearly free (`session.summary` already does it) but it is an
  epitaph: it can never change the outcome of work in flight.
- **A new standalone service** — violates compose-don't-re-own; the vault gives storage,
  retrieval, versioning, scoping and mesh sync already.
