# ADR-006: Drive inbox queues as the capture front door

- **Status**: Accepted
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

Three queues — `reminders`, `todo`, `ideas` — under
`phantom-ink/<profile>/<queue>/`, each with the same five lifecycle folders
(`inbox`, `processing`, `done/<YYYY-MM>`, `quarantine`, `rejected`). One file is
one item, and the lifecycle is the filesystem.

A three-stage pipeline runs as **two** collect jobs, with a local spool between
them:

```
Drive inbox/ ─▶ [A drain] ─▶ spool ─▶ [B extract] ─▶ verdict ─▶ [C apply]
                mechanical           local model only        holds the tokens
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
- A `reminders` item with no parseable date is quarantined rather than filed
  dateless, because a reminder with no time is not a reminder.

### The folder is the category

The original framing was "read it, categorise, store it". But the category is
declared by the human when they choose the folder. Re-deriving it adds a failure
mode and buys nothing, so Stage B does **field extraction** — prose to
title/due/priority — not classification. Smaller job, smaller blast radius.

Frontmatter the human wrote is trusted and wins field by field, and an item
whose frontmatter is complete never reaches the model at all.

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

**Good.** Capture works from any device with Drive. No new credentials, no new
service, no Go or Svelte changes — both stages are `target_type: "shell"` collect
jobs that the existing CollectorsPanel already manages, and drained items appear
on the app timeline through the normal entries contract. Prompt injection is
closed structurally rather than by hoping a model behaves.

**Costs and sharp edges.**

- Two scheduled jobs per profile rather than one, because collect jobs are
  killed at 60 seconds (`app/collect.go:793`) and no run may span both stages.
  Per-run caps (25 drain, 10 apply) and a 35s model timeout keep each inside it.
- Stage C must append to the ledger **before** moving the Drive file. A kill
  between the two leaves an applied file in `processing/`, which
  `--requeue-stale` archives by consulting the ledger. The reverse order would
  double-apply.
- `reminders_add` does **not** error on an unknown list — it silently falls back
  to the default list (`EventKitManager.swift:145`). The per-profile list is
  therefore ensured up front and the list name in the confirmation is verified;
  a reactive "create on error" approach never fires and silently breaks profile
  isolation while reporting success.
- Local extraction quality is the main ongoing risk. Relative-date parsing is
  the hard part, and the quarantine path makes failures visible rather than
  wrong. If the rate is high the fix is frontmatter-first habits, not
  `allow_paid`.
- EventKit's TCC grant must be primed once from a terminal; as a subprocess of
  the Wails app the prompt may not be attributable.

## Alternatives rejected

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
