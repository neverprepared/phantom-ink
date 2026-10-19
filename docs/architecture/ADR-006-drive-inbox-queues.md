# ADR-006: A Drive inbox as the capture front door

- **Status**: Accepted (revised 2026-10-19, same day — see *Revision*)
- **Date**: 2026-10-19
- **Supersedes / relates to**: ADR-001 (orchestration), ADR-005 (capability scoping)

## Context

There was no low-friction way to get a thought from a phone into the platform.
An item could enter only through the GitHub webhook
(`brainbox/src/brainbox/github_webhook.py`), the agent event bus, or the
conversation-promote menu (`brainbox/src/brainbox/conversation_promote.py`) —
all of which require being at a machine, inside the app. Capture that needs a
laptop is capture that does not happen.

Google Drive is already on every device the capture would come from, and two
accounts are already OAuth'd on this host with full `drive` scope
(`~/.google_workspace_mcp/credentials/`), so a folder is reachable with no new
auth flow and no new client in the platform.

Two facts constrain the design.

**The platform has no Drive client.** There is no Google code in the Go app or in
brainbox; the only reference is an MCP command override
(`brainbox/src/brainbox/backends/configure.py:41`). Drive is reachable from a
host-side script, not from the daemon.

**A Drive folder is an untrusted input channel.** Anything shared into it, or
pasted from the web, becomes text that is read and acted on — with vault-write
and calendar-write authority downstream. Capture is also the most private text
in the system: unfiltered thinking, written without an audience in mind.

## Decision

**One flat inbox** per profile at `phantom-ink/<profile>/inbox/`. One file is
one item. What an item IS — reminder, todo, or idea — is a property of the item,
settled during extraction or declared by the author, never encoded in a folder
path.

Alongside it, two folders that are never deleted from: `quarantine/` and
`rejected/`. A filed item is moved to Drive **trash**; there is no archive.

A three-stage pipeline runs as **two** collect jobs, with a local spool between
them:

```
inbox/ ─▶ [A drain] ─▶ spool ─▶ [B extract] ─▶ verdict ─▶ [C apply] ─▶ trash
          mechanical           local model only          holds the tokens
          gates only          no tokens, no tools
```

### The trust boundary is the architecture

**The component that reads untrusted text holds no credentials and has no
tools.** Stage B is a pure text-to-JSON function; its output is validated field
by field before anything acts on it, and Stage C consumes only that validated
struct, never the original prose.

This is deliberately structural rather than a prompt instruction. The prompt
does say the content is data, but that is defence in depth: the control is that
there is nothing in Stage B to subvert. An injected instruction can at worst
produce a wrong-but-well-formed title.

Three properties fall out of it:

- A verdict whose `item_id` is not in the spool is discarded, so the model
  cannot act on an item it invented.
- Every field is range-checked — title length, tag charset, priority enum, a
  due date that must be in the future and inside a two-year horizon. A
  hallucinated year cannot create a reminder nobody will ever see.
- `kind` is validated against the closed set, so the model cannot route an
  item to a target that does not exist.

### The kind is a field, with a deterministic override

Reminder-versus-todo is nearly mechanical (is there a time?), but
**todo-versus-idea is commitment**, and that is not derivable from any field —
an idea can be perfectly actionable; that is exactly the pointer case below. So
the model returns `kind` alongside the other fields, validated against the
closed set like everything else.

Two deterministic overrides exist for when it guesses wrong, both skipping the
model entirely: frontmatter `type:`, and a filename prefix (`r:`, `t:`, `i:`, or
the full word). The filename route exists because frontmatter is awkward from a
phone share-sheet, where the filename is often the only thing you get to type.
Its separator is restricted to a colon or a space-padded hyphen, because a bare
hyphen turns the ordinary filename `ideas-are-great.md` into kind=idea with the
title "are-great".

A **dateless reminder is demoted to a todo** rather than quarantined — the
capture is good work, and losing it over a date the author never wrote is the
worse failure. If the author *declared* it a reminder, that is a contradiction
worth surfacing, and it quarantines.

### Extraction is local-only, verifiably

Stage B calls `POST /api/llm/complete` with `quality="cheap"`,
`allow_paid=false`, `allow_scraped_free=false`, which resolves to an
**ollama-only** chain (`phantom-router/.../llm/strategy.py:47`). "Capture
content never leaves the machine" is therefore a property of the resolved
backend set rather than a promise — and the response's `backend` is asserted
before the text is used. An Ollama outage quarantines rather than silently
falling back to a remote model.

### Ideas are notes, not tasks

An idea you intend to "act on eventually" is not a task. Ideas become
**memory-vault notes**; only when an item is marked `actionable` is a single
thin **todo pointer** created, referencing the note by sha. The note holds the
content, the todo is the hook. Duplicating the body would give one item two
sources of truth, and filling the todo list with un-actionable ideas is how a
todo list dies.

### Reminders go to Apple Reminders

The todo vault carries only `title/body/tags/sha` (`app/app_agent_vault.go:21`)
— no due date, no priority, no completion state. A reminder's entire point is
its time, so it goes to EventKit via `macos-mcp`, which has all three.

## Consequences

**Good.** Capture works from any device with Drive, into a single folder with
no taxonomy to maintain. No new credentials, no new service, no Go or Svelte
changes — both stages are `target_type: "shell"` collect jobs that the existing
CollectorsPanel already manages, and drained items appear on the app timeline
through the normal entries contract. Prompt injection is closed structurally
rather than by hoping a model behaves.

**Costs and sharp edges.**

- Two scheduled jobs per profile rather than one, because collect jobs are
  killed at 60 seconds (`app/collect.go:793`) and no run may span both stages.
  Per-run caps (25 drain, 10 apply) and a 35s model timeout keep each inside it.
- Stage C must append to the ledger **before** disposing of the Drive file. A
  kill between the two leaves an applied file in `processing/`, which
  `--requeue-stale` disposes of by consulting the ledger. The reverse order
  would double-apply.
- `reminders_add` does **not** error on an unknown list — it silently falls back
  to the default list (`EventKitManager.swift:145`). The per-profile list is
  therefore ensured up front and the list name in the confirmation is verified;
  a reactive "create on error" approach never fires and silently breaks profile
  isolation while reporting success.
- Local extraction quality is the main ongoing risk, and it now covers the
  kind as well as the fields. Relative-date parsing is the hard part. A
  mis-typed item is cheap to fix and the overrides exist for the cases that
  recur; if the rate is high the fix is marker-first habits, not `allow_paid`.
- **The schedule lives in the app, and that is accepted rather than solved.**
  The collect scheduler is a goroutine started from `app.go`, so capture stops
  whenever the app is closed or restarting; both jobs were observed ~10 minutes
  overdue after a `wails dev` rebuild. A per-profile LaunchAgent owning the
  schedule (the pattern `pbrainctl mart` already uses) with the app reduced to a
  status viewer was considered and rejected: it would take the jobs out of the
  Collectors panel and with them the per-job play button. The inbox is
  therefore not a background service.
- A successful collect job that emits no items is invisible — stdout is parsed
  and discarded, stderr only reaches `last_error` on a non-zero exit. Each
  stage therefore emits one `inbox_run` status row with a stable `entry_id`,
  which upserts (`PRIMARY KEY (job_id, entry_id)`, `app/collect.go:504`) into a
  single always-current row rather than 96 a day. Without it, "ran, nothing to
  do" and "never ran" are indistinguishable — a confusion that produced two
  misdiagnoses while this was being built.
- Removing `done/` removed the visible proof that an item was processed. The
  local ledger is now the only such record, which is why disposal is trash
  rather than delete, and why the content-hash replay guard matters more.
- Files that are inbox *furniture* — a README, an `index.md`, anything dotted or
  underscored — must be skipped by name, or they are re-ingested as ideas on
  every run. `CLAUDE.md` and `AGENTS.md` are on that list too: Claude Code reads
  those from local filesystem paths, so a Drive copy is inert, but Drive is a
  multi-writer surface and such a file would be untrusted content if it ever
  did reach an agent.
- EventKit's TCC grant must be primed once from a terminal; as a subprocess of
  the Wails app the prompt may not be attributable.

## Revision (same day)

The first accepted version of this ADR used three per-kind folders
(`reminders/`, `todo/`, `ideas/`) and archived filed items into
`done/<YYYY-MM>/`. It argued, correctly for that design, that "the folder IS the
category — re-deriving it adds a failure mode and buys nothing."

That was revised on the author's feedback: the folders were serving the
*author's* cognition, not the system's, and maintaining a taxonomy at capture
time is friction at exactly the moment friction is most expensive. Drive is a
temporary queue, not a filing cabinet.

So the premise changed, not the reasoning: with no folder to read the category
from, the category must come from somewhere, and that is what moved `kind` into
the extracted-and-validated field set. The trust boundary, the local-only
routing, and the field validation are all unchanged — `kind` simply joined the
set of things the model proposes and the validator polices.

## Alternatives rejected

- **Per-kind folders** (the original design). Zero ambiguity and no model needed
  for routing, but it makes the author maintain a taxonomy at capture time and
  requires picking a folder on a phone.
- **Deriving the kind purely from fields** (a date makes it a reminder, else
  actionable makes it a todo, else an idea). Fully checkable with no new model
  output, but it cannot distinguish a committed todo from an idea, so ideas
  collapse into todos.
- **Requiring an explicit marker on every item**, quarantining anything
  unmarked. Deterministic, but it is the per-kind-folder problem again in a
  different costume.
- **Drive as a mirror of the vaults** (regenerated each run). Read-only for the
  human, which defeats capture.
- **Bidirectional sync.** Materially more work and nasty conflict semantics for
  a channel whose job is one-way intake.
- **One running Doc per queue** instead of one file per item. Lower capture
  friction, but the job must then diff the doc and track consumed lines; the
  filesystem gives the same lifecycle atomically and for free.
- **A dispatch stage** turning items into router tasks. Deferred: unattended
  agent spawn from a folder is its own decision, and it belongs after the
  capture path has proven itself.
- **No model at all** (frontmatter only). Maximum safety, but it forces you to
  write `due: 2026-10-23` instead of "friday", which is most of the friction the
  feature exists to remove. The frontmatter path remains available per item.
