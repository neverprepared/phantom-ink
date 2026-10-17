# Review record — agent work visibility

Date: 2026-10-17
Spec this informed: `docs/superpowers/specs/2026-10-17-agent-observability-design.md`

Four reviewers were given one shared brief containing measured facts rather than a
narrative, so each could disagree with the author's conclusions from the same evidence.
Their reviews are kept verbatim; every load-bearing claim below was independently verified
against source before being acted on.

| File | Lens |
|---|---|
| `REVIEW-BRIEF.md` | The shared input: the goal in the user's words, measured platform facts, overlapping app surfaces, the prototype's state |
| `review-ux.md` | Operator experience — does the surface serve "navigate what is running" |
| `review-data.md` | Data model and emission points |
| `review-skeptic.md` | Adversarial — find the reason not to build this as conceived |
| `review-plan.md` | Ordered task graph (`task-planner` role) |

## What the reviews changed

**The design had been tuned against a dataset the product cannot produce.** The
prototype's default view was a 38-node tree assembled from conversation history;
in-session subagents emit no platform records at all. Meanwhile the frame an operator
actually sees — 0 sessions, 0 running tasks, 2 runner heartbeats — had no design. The idle
state is the product.

**Three of the brief's own load-bearing claims were wrong**, and the author had propagated
one stale figure into the brief itself (`StreamPanel.svelte` given as 1430 lines; it is
1017 — a reviewer read the file). Corrections are listed in the spec's *Corrections*
section.

**One free win had been missed entirely**: `Task.spawned_by` already records real
agent→subagent parentage and is discarded at the envelope boundary in favour of `job_id`.

**One blocker nobody had seen**: `phantom-events/store.py:252` replaces `parent_id` rather
than coalescing it, so any parentage one producer writes is erased by the next envelope
that omits it. Nothing multi-producer works until that one line changes.

## Where the reviewers disagreed

`review-ux.md` argues the radial view should go and the tidy tree stay.
`review-skeptic.md` argues the reverse — at depth ≤3 a tidy tree only reproduces what an
indented list already shows, while the radial at least conveys scale. **Unresolved**; it
is a presentation question, not a correctness one, and the spec does not depend on it.

## On the one claim that was overstated

`review-skeptic.md` holds that `session_name` uniqueness "was assumed from a sample, not
read from the producer". Uniqueness *is* producer-enforced — `brainbox/store.py:135`
declares `session_name TEXT PRIMARY KEY`. But a primary key constrains live rows only:
names free on deletion (16 `session.deleted` rows in the sampled window) and a
caller-supplied name bypasses the uuid suffix. No reuse was observed. The severity ranking
survives even though the premise was imprecise, and it produced the rule the spec adopts:
**a derived edge may group things visually but must never confer identity.**
