# Adversarial review — agent work visualisation

Premise-level review against the measured numbers in REVIEW-BRIEF.md. Numbers taken as given.

---

## 1. The design has silently merged two jobs, and the visualisation serves only the weaker one

"Operate" and "audit" are stated as two jobs and then built as one surface with a scope
picker. Check each against the data separately and they want opposite artefacts.

**Operate.** Live population: 0 sessions, 0 running hub-tasks, 2 heartbeats. When it is not
zero it is 1–6 rows (max fan-out as-recorded is 6). Two consequences:

- A surface whose steady state is empty is not opened. This is the same failure the author
  already reasoned about from the other side: a check that fires every run trains the
  operator to ignore the panel; a panel that is blank every time trains them not to open
  it. Both end at "operator stops looking". The author applied that standard to cancelling
  two checks and has not applied it to this surface.
- At 6 rows, no layout beats a table. Falsifiable: in the radial view angular position
  encodes sibling order, which is start time, which a table states exactly and sortably.
  For n≤6 the wheel carries strictly less information than six rows of text, because the
  text can also carry duration, runner, attempts and session in the same glance.

The honest artefact for "operate" at this liveness is **notification**, not visualisation:
something started, something failed, something has been running longer than its peers.
0 running / 16 failed out of 500 says the operator's question is almost never "what is
running" and almost always "did anything break".

**Audit** is where a tree has a population (500 rows, 403 roots) — and audit is the job the
brief itself describes as "it would be nice to see", i.e. the one with no evidence of
demand. So the view that justifies the d3 work serves the job with the weakest warrant,
and the job with the real warrant needs a list and a toast.

**Survives:** profile scoping, and the decision to measure before building. The brief is
the most valuable thing produced so far.

---

## 2. The audit view cannot answer the only question audit is for, so it will be opened once

Enumerate what a post-mortem actually asks: *why did this fail*, *what did it change*,
*how long did it take*, *which runner*. Now check the envelope. Filled fields are
title/subtitle/type/status/source/start_at/end_at/tags/metadata. `description` is **0%
filled across all 500 rows**. There is no error field named anywhere in the brief.

So the audit tree can state *that* a node failed and for how long. It cannot state why, and
it cannot state what the work produced. The operator opens it, reads `failed`, and goes to
logs — permanently. Duration and runner they could already get from a sorted list.

This makes one prototype limitation load-bearing rather than cosmetic: *"the session column
is a labelled chip, not a working Connect button."* That chip is not polish. **The exit into
evidence is the entire value of the audit view**; the tree is the index, not the answer. A
tree that cannot be exited into the error text, the PR/commit, or the session log is a
picture of an incident, not an incident review.

Second scope error: the audit view is being designed against 482 `done` rows when the
interesting population is **16 failed (3.2%)**. The default scope should be failed nodes and
their ancestry, not everything.

**Verdict:** the audit job is real *conditionally*. It becomes load-bearing with
failed-first default + inline failure reason + one click to logs/artifact. Without all
three it is a dead end, and the three are producer-side and UI work, not layout work.

---

## 3. Depth-3 is structure for its own sake, and the headline statistic measures the wrong thing

`session → hub-task → runner-work` is an implementation hierarchy. Test it the only way that
matters: name an operator question whose answer *is* that edge. There is exactly one good
candidate — "a fan-out job dispatched 11 units of runner-work and 2 failed; which, and on
which runner". That is a real question, so the edge is not worthless. But note what it
needs: grouping runner-work under its task. Not depth.

The statistic "depth-2 nodes 1→307" is being read as if node depth were a goal. 307 children
under 71 roots, max fan-out 11, max depth 3, is a **wide, shallow, two-level grouping** —
which is a nested disclosure list. Falsifiable: at max depth 3 and max fan-out 11, a tidy
tree layout conveys nothing a two-level accordion does not, and costs SVG layout, zoom/pan,
hit-testing, and a third code path. "403 roots → 71" reads as progress because 403 flat rows
look bad; but 403 flat rows sorted by time is also exactly what the list view is, and it
works today.

Separately, this is **not** the "one-field fix" the brief frames it as. The session edge is
explicitly derivable client-side with no backend change. The runner-work edge is not —
`{kind, runner, work_id}` has no session and no task, so a `parent_id` must be stamped by
the producer. Two changes of different cost and different risk are being quoted under one
headline. If anyone proposes closing the gap client-side by joining on `runner` plus an
overlapping time window: that is a join on a non-key, and it mis-attributes precisely under
concurrency — a fan-out job on one runner — which is the only case the edge was wanted for.

---

## 4. The derived session edge fails silently, and inheritance turns that into a profile bug

This is the sharpest finding. Three problems, escalating.

**(a) n is 38, not 500.** "100% filled" describes the 38 hub-task rows. 37/38 resolve
*inside the window*. The one that didn't is not a defect — it is the generic failure mode:
the parent session fell outside the limit/retention window. At `limit=500` you get 97%; at
`limit=50`, far less. The rate is a function of window size, so a figure measured at 500
cannot be quoted as a property of the field.

**(b) The failure is silent and inverts a stated principle.** An unresolved `session_name`
renders the node as a root — visually identical to a genuine root. The project's own rule is
that a surface must never report what it did not observe. A node flattened to root *beside
correctly nested siblings* asserts "this work had no session", which is a false claim about
the world. This fails **worse than no edge**: a wholly flat view is honest about being flat,
and the reader applies no inference to it. Mitigation is cheap and must be non-negotiable:
when `session_name` is present but unresolved, attach to an explicit placeholder marked
*session not in window*. Never to root.

**(c) `session_name` is a name, not an id.** Names are reusable; ids are not. If session
names are not unique for all time, the join is many-to-many and children attach to the wrong
parent — and the view will look perfectly healthy while doing it. **Confirm uniqueness in the
producer before building this edge.** This is exactly the class of defect behind the author's
nine planning defects: an API property assumed from a sample rather than read from the
source. A 500-row sample cannot prove a uniqueness constraint.

**(d) The consequence nobody costed.** Profile attribution 35% → 94% is achieved *through
these edges*. So a wrong edge does not merely mis-draw a line — it **mis-labels a profile**,
in a system whose standing rule is that cross-profile leakage is a bug. Inheritance converts
a cosmetic tree error into a correctness failure in the one dimension declared
non-negotiable, and it does so invisibly.

Inheritance is also the wrong mechanism on its own terms. 295 runner-work rows lack
`workspace` while running *with that profile's credentials* — the producer knows the answer
and does not write it down. That is a producer bug. Client-side inheritance is a workaround
that launders an unknown into a confidently-rendered label. 94% attribution that is 94%
*inferred* is not the same artefact as 94% observed, and the operator cannot tell them apart.

**Rule:** inherit profile only across edges the producer asserted (`parent_id`). Never across
the client-side `session_name` join. Better: fix the producer and delete inheritance.

---

## 5. The "this session" scope is indefensible and is compensating for finding #1

38 nodes assembled from conversation history, with a middle layer the brief admits is "a
task grouping invented for readability, not a session boundary", sitting in the same scope
picker as `platform live` with the same visual language.

- **Unreproducible.** Nobody, including the author, can re-derive it next week.
- **It fabricates the one thing it exists to show.** The scope's purpose is to demonstrate
  depth; its middle level is invented. The demo's load-bearing layer is the fiction.
- **Placement asserts provenance.** A dropdown of peers claims the entries are peers. A
  viewer three weeks out has no way to know which parts were observed. The prototype's own
  per-node "what this row cannot tell you" line does not save it, because the scope picker
  is read before any node is clicked.

And the motive is the problem. This scope exists because the real data is empty. It makes
the surface *look* populated, which conceals the finding in #1 rather than surfacing it.
The emptiness is the result, not an obstacle to be dressed.

The genuine fact here — *28 in-session subagents produced zero platform records; nothing
models "an agent spawned another agent"* — is a one-sentence gap to file against the
producer. It is worth more written down than mocked up.

---

## 6. Three views: the redundant one is not the one you'd guess

"I like looking at data in different forms" is a real preference, but it does not establish
that three *layouts* are three forms.

- **List** — flat, chronological, sortable, carries every field as a column. Does the real
  work.
- **Radial** — the user's own word was "wheel", and they asked for drag-to-rotate. This is
  the *requested* artefact. Cutting it overrides a stated want.
- **Tree** — this is the redundant one. It is the radial in Cartesian coordinates, and at
  max depth 3 / max fan-out 11 (see #3) its structural payload is a two-level grouping the
  list can express with indentation, which the list already does ("task indented by depth").

So the defensible cut is the tidy tree, not the wheel. That is the opposite of the usual
instinct and it follows from the measured shape of the data plus the one explicit request.

Maintenance test, falsifiable: if adding a field to the detail panel requires edits in three
places, the three views are three surfaces and the indulgence is real. If selection, filter
and detail are shared and only layout differs, the cost is small and this objection is
minor. Answer that before defending the count.

---

## 7. Memory provenance: kill it outright

"Any data it touches" is unachievable in a form that does not mislead.

- Reads are **not logged at all** — and reads are most of "touched".
- Records carry no session, task or agent id. `/links`, `/audit`, `/record_links`,
  `/provenance` all 404.
- The only attribution is a naming convention (`task_summary` title = task goal) covering
  88/400 ≈ 22%.

22% is the worst possible number. High enough that the panel appears to work; low enough
that an empty result means nothing at all. The operator cannot distinguish "this task
touched no memory" from "this task's writes were not of the shape our convention covers".
A provenance view built on it is a machine for producing false negatives about data
lineage — and the one thing provenance must never do is under-report.

The only honest version is producer-side: stamp `session_id`/`task_id` on brain writes, and
log reads. That is a backend project with its own justification, not a feature of this
visualisation. Do not half-build it. Do not render a provenance section that is blank for
78% of records.

---

## 8. A live correctness bug outranks every view here

Already known and already logged: workspace-less envelopes **pass** the profile filter in
`applyAgentEvent`/StreamPanel, so the 295 runner-work rows appear under *every* profile
simultaneously. That is cross-profile leakage, in production, in the dimension the user has
declared foundational — and any new surface built on the same event path inherits it.

Also: the planned-but-unbuilt StreamPanel `active` tab has the same data source and the same
purpose as this design's "operate" half. Shipping a second surface for running tasks before
that tab exists creates two places to look for one answer.

One useful by-product of the measurement: that note's open decision — "frontend SSE
aggregation or a backend `current_activity` field" — **is now settled.** `description` is 0%
filled and no `current_activity` exists anywhere, so there is nothing to aggregate. The
per-agent "what is happening" line is a backend change or it does not exist. Any plan that
shows an activity line without a producer change is planning against a remembered API again.

---

## What survives attack, plainly

Spend here; these are sound.

1. **Profile scoping as a first-class filter.** Correct, required, non-negotiable.
2. **The per-node "what this row cannot tell you" line.** The best idea in the prototype and
   the right response to a 0%-filled `description`. Extend it, don't bury it.
3. **The audit job itself** — conditionally, as a failure post-mortem that terminates in
   evidence. Real work, wrong current shape.
4. **Grouping runner-work under its dispatching task.** The fan-out question is genuine.
   Buy it from the producer, not from a time-window join.
5. **The list view.** Carries every field, needs no interaction vocabulary, works at n=6 and
   n=500.
6. **Measuring first.** The brief's numbers are what made this review possible, and they
   killed two of the author's own premises. That is the process working.

---

## If allowed one change

**Stamp `workspace` and `parent_id` on `runner-work` at the producer.** One producer-side
change, 295 rows (59% of the data), and it simultaneously:

- gives real grouping under the dispatching task, *observed* rather than joined;
- takes profile attribution to ~94% as recorded fact, not inference;
- removes the need for profile inheritance, and with it the #4(d) leakage risk;
- removes the need for the `session_name` join to carry any correctness load;
- fixes the live every-profile leak at the source rather than patching the filter.

Everything the design currently fakes, it fakes because this field is missing. Buy the field
before drawing anything.

## If allowed one deletion

**The "this session" scope.** Unreproducible, its load-bearing layer is invented, and its
placement beside real telemetry asserts a provenance it does not have. It exists to hide the
fact that the platform is idle — which is the finding, not the problem. Replace it with one
filed sentence: *in-session subagents are invisible to the platform.*
