# Operator-experience review — agent work visualisation

Judged only against the two moments: **something is running and I want to see/steer it**,
and **something finished and I want to know what happened**.

---

## The headline

The prototype's most impressive view — "This session · 38", the one that loads by default —
is the one view that **cannot exist in the shipped product**. In-session subagents produce
zero platform records; that tree was assembled from conversation history. Everything an
operator will actually see is the other four scopes, where liveness is 0 sessions, 0 running
tasks, 2 heartbeats, and the deepest real tree is one node at depth 2.

So the design has been tuned against a dataset the product will never have. The default
frame on a real idle platform is **a near-empty tree with two unconnected dots**, and that
is the frame that needs the design work. Right now it has none.

A second consequence: the whole surface is currently arguing a feasibility case (five scopes,
one per join hypothesis; a verdict section; a per-row paragraph about `metadata.session_name`
fill rates). That argument is good and should live in the ADR. None of it belongs in the
panel.

---

## Prioritised changes

### 1. Make the idle state the primary design, not the fallback
**Why:** the fleet is idle most of the time (482/500 done, 2 active, 0 sessions). The
operator will open this surface and see nothing running far more often than not. If "nothing
running" renders as an empty tree canvas, the surface is useless in its most common state and
the operator stops opening it.

The idle state should be the surface's launcher, in this order:

```
Nothing running.                          ← plain, one line, not an illustration
Last failure   runner-work · m3-64 · 14m ago      [open]
Recent         3 trees finished in the last hour  [list]
Runners        m3-64 ✓ 8s   storage ✓ 11s          ← the 2 heartbeats, as health
```

Three facts, each a link into the thing it names. The near-empty state (1–2 running) is the
same layout with the running rows pinned above it. There is no separate empty-state design to
build — it is the list with zero rows in its first section.

### 2. Lead with "what is stuck", not with chronology
**Why:** the operator scanning running work is looking for the one that is wedged. The tree
sorts siblings by start time, which is right for audit and wrong for operate.

Default order in operate mode: **active, sorted by age of last event descending** (most
silent first), then **failed, newest first**, then done. The lead column is live-ticking
**elapsed**, not `took` — and for active rows a second number that matters more: **age of the
last event for that task**. A task 40 minutes in with an event 3 seconds ago is healthy; one
12 minutes in with an event 11 minutes ago is the one to open. `took` is the audit column;
these are not the same column wearing different hats and should not be merged into one
labelled "took".

Also surface `attempts / max_attempts` from `hub-task` metadata as a column. "3/3" is the
cheapest stuck signal in the dataset and it is already 100% filled.

### 3. Put Connect on the row. Count the clicks.
**Why:** "quick and easy to navigate" was the stated requirement, and the current path is:
open (lands on tree) → switch to list (1) → scan/scroll → click row (2) → read detail → the
session chip is a `<span class="conn">` with `cursor:default` and a tooltip saying a real
panel would do this (∞).

Target is **one click from open to terminal**. That means: list is the landing view, running
rows are pinned at the top, and the row itself carries a Connect button. The detail panel must
not be on the path to attaching — detail is for "what happened", not for "let me in".

Add keyboard, because this is a desktop operator tool opened dozens of times a day: `/` to
filter, `j`/`k` to move, `Enter` to connect, `Esc` to clear. That is the difference between a
surface you navigate and a surface you drive.

### 4. Cut radial. Keep list + tree.
**Why:** three views over one dataset is indecision, and radial is the one that answers no
operator question.

- It encodes **depth as radius** when real depth is ≤ 3 even fully parented. It is spending
  the most expressive channel on the least variable dimension.
- Labels only render for parent nodes under 60 visible rows, and radially-placed text is
  harder to read than a left-aligned column at every node count.
- The drag-to-rotate interaction exists to relieve occlusion that the layout itself created.
  An interaction whose purpose is to undo the view's own defect is not a feature.
- The user asked for a rotatable wheel because *"I like looking at data in different forms"*.
  That is a genuine preference, and the tree already satisfies it — it is a second form. It is
  not a requirement that the second form be a wheel.

If the wheel survives, it belongs on the **dashboard as a scale/health widget** ("here is the
whole fleet's shape at a glance"), not in the operate path. Argue that trade with the user
explicitly rather than keeping it as a third tab to avoid the conversation.

Keep the tree, but earn it: it is the **audit** view, and it should be reachable by selecting
a root and saying "show the tree for this", not as a global toggle. One finished session's
tree is a shape worth seeing. Five hundred rows of mixed roots is not.

### 5. Delete the five scopes
**Why:** "As recorded / Session-isolated / Fully parented" are three competing engineering
hypotheses offered to the operator as a choice. No operator wants to pick a join strategy, and
picking wrong makes the data look broken. "As recorded" with its 403 roots is a view of the
bug, not a view of the work.

Ship the best available join — session-isolated today, fully-parented once `runner-work`
carries a `parent_id` — as the only shape. Keep one honest toggle if any: **completed on/off**,
which is what the user actually asked for ("active/live agents and their subagents as a
default, maybe a toggleable completed jobs").

### 6. Profile must be structural, not a filter chip
**Why:** the user's standing rule is that every feature is profile-scoped and cross-profile
leakage is a bug. The chip model breaks that twice:

- It **defaults to "All profiles"** — cross-profile by default, which inverts the rule.
- The known `applyAgentEvent` bug lets workspace-less envelopes pass every profile filter, so
  the 295 `runner-work` rows (59% of everything) appear under *every* profile at once. The chip
  does not just fail to isolate; it reports a false isolation.

The right model: the surface inherits the app's current profile, same as every other panel —
one profile, no chip. A deliberate **fleet mode** ("all profiles") is an explicitly different
mode: a profile column appears, the header changes, so the operator can never be unsure which
they are looking at.

Then fix the data under it. The prototype's parent-inheritance pass is the correct idea and
should move into the app's filter, not stay in the mock — it takes attribution from 35% to 94%
with one rule. The remaining 6% goes into an explicit **unattributed** bucket that is *never*
included in a specific profile's view. Leaking into one profile is worse than being
unattributed in a visible bucket.

### 7. Fix the activity line, or admit there isn't one
**Why:** the user asked for "a brief summary of what the task is doing". `description` is 0%
filled across all 500 rows and there is no `current_activity`. The prototype fills the gap by
assembling a summary from title + metadata — which will read as a confident lie on exactly the
tasks where it is wrong, and those are the ones the operator opened the panel for.

Cheapest honest version, and better than prose anyway: **the last event, typed, with its age**
— `tool_use · bash · 14s ago`. That is true, it is derivable from the bus today (the
StreamPanel active-tab note already proposed frontend SSE aggregation), and for spotting a
wedged agent it beats any summary sentence. If a backend `current_activity` lands later, it
drops into the same slot.

Do not ship the assembled summary. A fabricated activity line is worse than a blank one.

### 8. The surface must be live, and say when it isn't
**Why:** a static snapshot of running work has no value — the operator's question is "is it
moving". Wire SSE, tick elapsed clocks locally between events, and show a staleness marker when
the stream drops. An operate surface that silently stops updating will be trusted once and then
never again.

### 9. Rewrite the detail panel around the next action
**Why:** it currently dumps every field the row holds, plus a paragraph explaining the
platform's metadata limitations. That paragraph is an author explaining himself inside a
product. The operator did not ask how `metadata.session_name` is joined and cannot act on it.

Lead with the three things that drive the next action:

1. what it is doing (the activity line above)
2. elapsed / last event age / attempts
3. where to attach — **and the actions**: Connect, Stop, Retry

Then, for a failed row, **the error** — the last error text or the tail of its output. Right
now clicking a failed task tells you it failed, which you already knew from the colour. That is
the single most common audit question and the panel currently cannot answer it.

Raw metadata goes behind a disclosure for the rare moment someone needs `work_id`.

Keep exactly one piece of the honesty: mark absence **where the value would be**, in the
field's own slot, in a few words — `activity: not recorded`. That is useful. The paragraph is
not.

### 10. Add actions, not just views
**Why:** within a minute of real use the operator will try to stop something. The MCP surface
already exposes `cancel_task`; the app does not expose it here. A surface that lists running
work and cannot stop it sends the operator to a terminal, which is where they were trying not
to go. Minimum: **Stop** on a running row, **Retry** on a failed one.

### 11. Parent `runner-work` so you can hide it
**Why:** endorse the one-field change — 403 roots → 71 is the difference between a readable
tree and a wall. But be clear about the real payoff: `runner-work` is 59% of all rows and is
mostly machine chatter. The value of giving it a parent is not that you can *draw* 295 more
nodes; it is that you can finally **collapse it under its hub-task and show a count**
(`+ 7 runner steps`). Parenting is what makes correct hiding possible. If the plan is to render
all 307 depth-2 nodes, the field buys a denser wall.

### 12. Reframe "data it touches" as "what it produced"
**Why:** the user asked to see the data a task touched. It does not exist: reads are not logged
at all, and writes are attributable only by the `task_summary` title convention (~22% of
records). There is no honest "touched" view, and a 22%-coverage one will be read as complete
and be wrong three times in four.

What is real today: the `task_summary` a task produced, and the commit/PR in its outcome
metadata. Ship **outputs** — the artifact, the PR, the summary record — which is also what the
audit moment actually wants. Call it what it is, and leave "touched" for when read logging
exists.

---

## Dead ends

- **Radial / the wheel in the operate path.** Depth ≤ 3 does not need a radial encoding, and
  drag-to-rotate is remediation for self-inflicted occlusion. Legitimate as a dashboard
  scale widget; not as one of three peer views.
- **The scope switcher.** Operators do not choose join hypotheses. This is ADR content.
- **The "what this row cannot tell you" paragraph.** Authorship inside the product. Reduce to
  an inline `not recorded` marker in the absent field's own slot.
- **A synthesised activity summary.** Confidently wrong exactly when it matters. Use last
  event + age instead.
- **A "memory touched" view built on the title convention.** 22% coverage presented as
  complete. Ship outputs instead.
- **Building this as a new panel.** It duplicates the already-planned StreamPanel `active` tab,
  whose data source is the same `hubTasks` store, and it depends on *not* having the workspace
  filter bug that StreamPanel has. Shipping a second surface leaves two implementations of the
  same list and one of them leaks profiles. Sequence it as: fix `applyAgentEvent`'s
  workspace-less pass-through → lift the ttyd slide-out out of `SessionsPanel` into a shared
  component (its second consumer is this) → build the active list as a component inside the
  Stream/Automations surface → add the per-root tree as the audit view there.

---

## Missing, in order of how fast an operator hits it

| Within | They reach for | Status |
|---|---|---|
| 10s | Stop this | absent |
| 20s | Why did that fail — the error text | absent |
| 30s | Which runner, and is it alive | heartbeats exist, unjoined |
| 30s | Is this moving (last event age) | derivable, absent |
| 40s | Attempt 3 of 3 | 100% filled, unsurfaced |
| 1m | Retry | absent |
| 1m | Text search / filter | absent |
| 1m | What did it produce — PR, commit, summary | in metadata, unsurfaced |
| 1m | Open this in logs, filtered to this task | absent |

---

## What I would actually ship first

One list, profile-scoped structurally, live over SSE. Sections: **running** (elapsed, last
event age, attempts, Connect, Stop), **failed recently** (error on click, Retry), **runners**
(heartbeat health). Completed is a toggle. Selecting a root reveals its tree as the audit view.
No scopes, no radial, no explanatory prose.

That is smaller than what exists, serves both moments, and is one click from open to terminal.
