# Agent Relay — Design Spec

**Date:** 2026-10-07
**Status:** **NOT APPROVED — DO NOT BUILD AS SPECCED.** Retained as the record of a 5-lens adversarial review that falsified four of its load-bearing mechanisms. The approved replacement is the four-item probe in §0. Everything from §1 onward is the original draft, preserved unedited except where a §0 finding is cross-referenced.
**Revised:** 2026-10-07 — merged the under-posting and volume risks into one trade-off and
locked the dial (§9.1); demoted volume, dedup and staleness to **open problems** rather
than mitigated rows (§9.2); recorded briefing as an accepted context-injection channel
(§9.3); replaced the weak "readability at 20 entries" gate with a top-5 retrieval-precision
test; made repo-config provisioning a Phase 1 requirement.
**Supersedes:** nothing. Deliberately does *not* replace the conversation engine (see §10).

## 0. Review outcome (2026-10-07) — read this first

A five-lens adversarial review (YAGNI, failure modes, security, operability, retrieval),
each lens verifying claims against code rather than against this document, returned:

| Lens | Verdict |
|---|---|
| YAGNI | **RESPEC** — "the findings don't trim the spec, they falsify its locked goals" |
| Failure modes | **DESIGN DEFECT** — rewrite §4, Phase 1, Phase 2 before build |
| Security | **MITIGATE BEFORE BUILD** — Phase 2 blocked on five findings |
| Retrieval | **VIABLE WITH CHANGES** |
| Operability | **OPERABLE WITH CHANGES** — scorecard + `session_id` are binding conditions |

### 0.1 Why this is not an amendment

**The null hypothesis holds: the pipeline this spec proposes already exists.**

- `task update --finding` already captures per-finding during work, with importance and
  memory-type.
- `task complete` already promotes to a **recallable** store — `internal/mcp/task.go`
  ingests to `curated` with `KindOverride: "task_summary"`,
  `MemoryTypeOverride: "episodic"`, `SourceOverride: ["task:<id>"]`.
- `session_summary.py` already emits narrative + machine-extracted facts + an evidence
  handle.
- The p2p mesh already replicates it.

The **only** genuine gap is that `recall` prefers the synthesised body
(`internal/pgrecall/recall.go` `bodyExpr`). So the real delta is a read-path option plus a
vault binding — not a five-phase programme.

Worse, this spec's §2.2 "verbatim, never distilled" **introduced** a defect the existing
path does not have: `reliability` and `topic` are omitted from p2p `syncCols` because they
are recomputed locally, which holds for **synth** kinds. `task_summary` is a synth kind.
Choosing verbatim is precisely what breaks cross-node ranking.

### 0.2 Confirmed substrate traps (verified against code — reusable beyond this spec)

1. **`supersede` hides the original from `recall`.** The integration test's own header:
   "supersede writes a link, recall + list hide the superseded record." `head=false` and
   `history` still reach it. So §2.4's "amend, never delete" is true of storage and false
   of the read path — and Phase 4 would have removed exactly the refuted dead ends it
   existed to preserve. A briefing would then silently reduce to unreviewed claims only,
   presented as vetted. If an annotation is ever needed, widen
   `record_links_rel_chk` (today `CHECK (rel IN ('supersedes'))`) — the hiding keys
   explicitly on `rel='supersedes'`, so a new rel annotates without hiding.
2. **The router skips `configure()` for runner-dispatched sessions.** `lifecycle.py`:
   `if ctx.runner_name and ctx.runner_name != "local": return ctx`. A briefing injected
   there reaches local sessions only — never the fleet, which was the entire motivation.
   `configure()` is also not fail-open, so a hung dependency inside it fails session create.
3. **Vault tokens are injected into the container, not kept server-side.**
   `_brain_cred_keys()` enumerates `CL_<VAULT>_API_TOKEN` per entry in `CL_BRAIN__VAULTS`.
   Adding a vault hands every session a full-vault bearer with no scope and no ceiling,
   **bypassing the MCP gateway entirely** — so ADR-002 residency filtering and ADR-005
   capability scoping have no reach over brain vaults. This spec's claim that the token
   "stays server-side" was simply wrong.
4. **`reliability` is author-settable** (`applyMemoryFields` copies it verbatim; validation
   only checks enum membership) **and was this spec's top ranking key** — so a poisoned
   entry self-promotes. As a ranking input it also ratchets upward until every entry is
   `high` and the feature carries zero bits.
5. **`supersededSHAs` full-scans every supersedes link on every recall**, with `fetchSize`
   clamped to `recallMaxLimit=50`; past ~40 superseded entries pages silently under-fill.
6. **"Scoped by repo + role" is not implementable today.** `RecordsMapping` has no
   `repo`/`role` field and `buildFilters` has no `tags` clause despite `tags` being mapped
   as a keyword. Separately, `topic` is an **unanalyzed keyword term filter**, which
   directly contradicts §4's "no taxonomy imposed" — free-text topics never match.
7. **No `author`/`node` field exists.** §4 asserted a provenance property the substrate
   lacks, while citing prior art that depended on exactly that property.

### 0.3 Where the security acceptance in §9.3 was wrong

§9.3 accepted briefing as a context-injection channel because "every writer is our own
agent." **Authorship is not the trust boundary.** Our agents read issues, changelogs,
vendored READMEs and web pages, then write findings — authentically ours, content
attacker-chosen. §9.3's own revisit trigger was therefore met on day one. It is also worse
than ordinary prompt injection because it **persists**: one bad entry gets a ranked,
auto-injected delivery channel into every future session. Additionally, briefing must never
ride `appendSystemPromptFiles`, which would make web-derived text literally system prompt.

### 0.4 The approved replacement — a four-item probe

Reuse what exists; prove the content is worth something before automating delivery.

1. Add `sessions` to `CL_BRAIN__VAULTS` **through `phantom-platform` repo config** — never
   by hand; hand-provisioning is how the `agents` vault came to exist on the live daemon
   but not in config.
2. POST the existing `session_summary.py` envelope to that vault.
3. Add a **raw read** option to `recall`, so the exact body is retrievable.
4. **Read the corpus for a week**, then decide whether any delivery machinery is justified.

No migration, no new kind, no injection, no new token — and because nothing is injected,
the entire §9.3 risk class is out of scope for the probe.

**Two independent fixes, not relay work:**

- `reap.go` publish-before-unlink. A real bug, but only for **local** Claude Code sessions,
  whose `wm-<PID>.sqlite` actually gets reaped. Containers use a CLI task store at pid 0
  that `reap.go` is documented never to match, so this spec's "crash-safe publish"
  component addressed a bug that does not exist on the session path — and is ill-defined
  for `SIGKILL` regardless.
- `session_summary.py` sets `description = narrative[:4000]` **unredacted**, while evidence
  upload is opt-in *because* transcripts carry secrets. Needs a redaction pass, and a
  delete escape hatch that **overrides** §2.4 — a secret spill must not be permanent and
  mesh-replicated.

### 0.5 Decisions worth keeping if this is ever revisited

- **Staleness:** admission-time TTL in the briefing composer (not briefable past ~30 days,
  still recallable), with an optional `repo`+`commit` anchor. Zero brain change. Decay
  cannot work — it is only *relative* demotion, so in a thin scope a six-month-old wrong
  entry still ranks first.
- **Outcome:** a denormalized keyword **on the record**, never a read-time link join. Under
  p2p a link references two SHAs, so merge can deliver link-before-record either way;
  verdicts arrive orphaned or records silently rank `unknown`, through an invisible window.
- **Ranking:** relevance is the sort key; reliability and recency are bounded (±15%)
  nudges. **Filter** on reliability; **rank** on outcome, because a different actor writes
  it.
- **Provenance as a tripwire, not a trust claim:** router-stamped from observed tool use
  (did this session call web/browser/issue-tracker tools?), never agent-declared.
  Externally-tainted entries brief as quoted evidence only and are barred from
  `reliability=high`.
- **Any agent-initiated read:** a gateway tool, scope-gated and ceiling-filtered,
  read-only — never a vault bearer.
- **The gate, made executable:** freeze the corpus; have a *different* agent write each
  query from the originating problem statement only (never the entry text); plant
  distractors that must not be admitted (past-TTL, refuted, superseded-correct); run
  through the real composer; pass on top-10 hit rate **and zero distractors admitted**.
  Then inflate to ~2,000 plausible neighbours and re-run — that answers the volume
  question in an afternoon rather than waiting for a later phase.
- **Dedup:** query-time suppression on the slice only (over-fetch, drop near-duplicates,
  report the suppressed count — which *is* the frequency signal). Write-time dedup is
  actively wrong: five agents hitting one wall is signal. Byte-identical entries already
  collapse via `records_uniq` and content-addressing, so only *fuzzy* dedup was ever open.
- **Operability:** any phase gate whose metric is hand-counted is fiction. A
  `relay report` scorecard pushed over the notify spine is the binding condition, and
  `session_id` on the entry is required before two named mechanisms can even be computed.

---

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
9. **No write mandate; ruthless read selection.** See §9.1 — this is the single most
   consequential decision in the spec. Posting is never gated or enforced. The relay is
   allowed to be noisy, and *admission to the briefing* is where quality is enforced.

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
- Scoped by repo + role. **Admission criteria are the quality control for the whole
  system — see §9.1**, because there is deliberately no write-side gate.
- Hard cap on slice size. A briefing that blows the context budget is a regression.
- Briefed content is **agent-authored text entering another agent's context**. See §9.3.

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

**Provisioning requirement — not optional.** The vault must be created **through
`phantom-platform` repo config**, not by hand on the daemon. Precedent: the `agents` vault
is live and populated on m3 but absent from
`phantom-platform/config/phantom-brain/profiles/*/vaults/`, so a fresh bootstrap would not
recreate it. Doing this by hand reproduces that drift exactly. The router half is
declarative (add `findings` to `CL_BRAIN__VAULTS`; `lifecycle.py` derives
`CL_FINDINGS_API_TOKEN` and keeps it server-side) — it is the brain-side binding that
drifts.

**Gate to Phase 2 (≥2 week soak):**
- [ ] ≥20 entries accumulated organically.
- [ ] Crash-publish verified on a real killed container.
- [ ] Vault present after a **clean bootstrap from repo config** on a scratch target —
      not just working on the live daemon.
- [ ] **The retrieval test** (replaces an earlier, weaker "can you read it" check):
      construct 5 realistic queries a *future* agent would ask, run them through `recall`,
      and score whether the top-5 hits are the entries you would have hand-picked.
      Precision in the top 5 is the metric; total corpus readability is not, because 20
      entries is readable no matter how bad ranking is.

**Kill criterion:** if the entries are uniformly low-value — epitaphs with no reusable
reasoning — stop and reconsider the grain before building the read path. If ranking is
already poor at 20 entries it will not survive 2,000; fix retrieval before Phase 2.

### Phase 2 — Briefing injection
**Build: ~2–3 days.** Recall slice in the `configure` phase, scoped and capped.

**Test:**
- Assert the slice respects the cap and never exceeds the context budget.
- Assert profile scoping: a `personal` session never receives a `gsa` entry. Cross-profile
  leakage is a bug, not a degradation.
- Assert `superseded-correct` entries are excluded.

**Blocking prerequisite:** the **staleness** decision in §9.2 must be made before this
phase ships. Briefing is the mechanism that turns a stale entry into a confidently wrong
instruction, so shipping injection without an aging story is shipping the hazard.

**Gate to Phase 3 (≥2 week soak):**
- [ ] **≥1 demonstrated case of an agent not repeating a documented dead end** — the
      single most important piece of evidence in this spec. Everything else is plumbing.
- [ ] Briefing adds no measurable session-start latency regression.
- [ ] **Zero cases of an agent misled by a stale entry.** One such case is a stop, not a
      papercut — it is strictly worse than having no relay.
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

### 9.1 The signal/noise dial (the dominant risk — one trade-off, not two)

Under-posting and volume are **not independent risks**. They are opposite ends of one dial,
and an earlier draft of this spec wrongly listed them as separate table rows with separate
mitigations. Getting this wrong in either direction kills the relay.

**Why agents under-post — it is structural, not motivational.** An agent's completion signal
is *its own task*. Posting costs tokens, context and time, and the beneficiary is a
different agent that does not exist yet. That is a free-rider structure. No amount of
prompt insistence fixes an incentive, and instructions that do not serve the immediate goal
are the first thing dropped under context pressure.

**Why the obvious fix is worse.** Mandating posts — gating `task complete` on having
findings, for which there is precedent in the existing Stop-hook enforcement — produces
**compliance slop**: entries written to satisfy a gate rather than to inform anyone. That
converts a signal problem into a noise problem, which is the other end of the same dial.

**Decision (locked, §2.9): no write mandate; ruthless read selection.** Let the relay be
noisy. Enforce quality at *admission to the briefing*, not at write time:

- Rank by `reliability`, then `outcome`, then recency.
- Prefer entries a later agent actually referenced (citation as a quality signal) once
  Phase 3 makes referencing observable.
- Exclude `superseded-correct`; brief `refuted` only as an explicit warning.
- Hard cap the slice.

Read-side selection costs nothing in agent compliance and **cannot be gamed by an agent
trying to pass a gate**, which is precisely why it is preferred to enforcement.

### 9.2 Open problems (unsolved — do not pretend otherwise)

These have no mitigation in this spec. They are recorded as open so Phase 2+ does not
proceed believing them handled.

**Volume is substantially unmitigated.** Capping the briefing bounds what an agent *reads*;
it does nothing about corpus growth or ranking degradation. The prior art broke down at
hundreds of thousands of entries. The Phase 1 retrieval test measures top-5 precision at
small N, which is necessary but nowhere near sufficient.

**Deduplication.** When five agents hit the same wall, the relay gets five near-identical
entries. `supersede` is for *amendment*, not dedup — nothing collapses them. A healthy
relay should **shrink** under repetition (five reports of one failure → one entry with a
count), but consolidation is exactly what the synth gate does, and §2.2 rejects that for
verbatim. The tension is real: **verbatim buys exactness and forfeits consolidation.** The
likely resolution is a derived digest layer for briefing with drill-through to verbatim —
a new component, and more complexity than this spec currently accounts for.

**Staleness — the most dangerous open problem.** A finding about a dependency since
upgraded is not merely useless, it is **confidently wrong**. `superseded-correct` covers the
case somebody noticed; stale-but-unmarked is the hazard. Nothing here ages entries.
**Briefing a stale entry is worse than briefing nothing**, because believing it costs the
new agent nothing. Candidate directions (none chosen): recency decay in ranking; a
revalidation requirement past some age; binding an entry to a commit/dependency version so
it can be invalidated mechanically. Needs a decision before Phase 2 ships.

### 9.3 Briefing is a context-injection channel (accepted with eyes open)

Briefing injection means **agent-authored content enters another agent's instruction context
at session start.** This is not hypothetical: it is the literal mechanism by which the
agents in the prior art propagated capability between runs. One wrong or poisoned entry is
then believed by every subsequent agent, and `reliability` offers no protection because the
entry's author sets its own confidence.

**Accepted** for a single-operator, single-tenant fleet where every writer is our own agent.
Recorded explicitly rather than left unnoticed, because the platform is otherwise
fail-closed about trust (ADR-000 P2, trust-zone ceilings) and this is a deliberate
exception. Revisit immediately if any of these become true:

- a relay entry can be authored by anything outside our own fleet;
- the relay is read across profiles (today it is profile-scoped and must stay so);
- briefed content starts driving tool calls or automation without a human in the loop.

Cheap partial measures, deferred but noted: brief entries as *quoted evidence* rather than
as instructions, and mark the briefing block as untrusted data in the prompt.

### 9.4 Remaining risks

| Risk | Mitigation |
|---|---|
| Briefing eats the context budget | Hard cap; measure session-start latency as a Phase 2 gate. |
| Premature taxonomy | Impose none. The prior art shows conventions emerge from use; harvest later if they do. |
| Relay drifts into duplicating brain `memory` | The vault boundary plus verbatim-vs-synth is the line. The relay is in-flight working knowledge; promotion to `memory` stays explicit. |
| Outcome verdicts become rubber stamps | The Phase 4 gate requires evidence of an actual downgrade or upgrade, not mere coverage. |
| New vault exists on the live daemon but not after a clean bootstrap | Phase 1 provisioning requirement; `agents`-vault drift is the precedent. |

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
