---
name: phantom-brain
description: Operational reference for phantom-brain, the long-term memory MCP (brain_* and task_* tools). Use when storing to or searching long-term memory, choosing between brain_learn, brain_perceive, or brain_attach, running the task lifecycle (task_start/update/complete), or debugging recall freshness, snapshots, or daemon connectivity.
---

# phantom-brain: long-term memory

> "Long-term memory" always means the phantom-brain MCP server (the `brain_*` and `task_*` tools). This skill is the operational reference: which tool to use, the recall protocol, and the gotchas.

## What it is

phantom-brain is a two-process system: a per-agent MCP client (`pbrainctl client mcp`) talking to a shared HTTP daemon backed by OpenSearch + MinIO. Writes go to the daemon; synthesis (the Gate plus an LLM distill pass) runs automatically daemon-side; recall reads a local snapshot of the daemon's view.

There is **no** `brain_synthesize` (synthesis is automatic) and **no** `brain_reflect` yet (maintenance/forget is planned, neverprepared/phantom-brain#72). Do not call either.

## Access only through these tools (hard rule)

Reach memory ONLY through the `brain_*` tools below. OpenSearch + MinIO are the daemon's implementation detail, not an access path:

- Do NOT curl/search OpenSearch, MinIO, the local snapshot, or vault files to find or verify a memory - even when the user frames it as "is it in OpenSearch / the index / the store / at this id." Route to `brain_recall`.
- A `localhost:9200` cluster may exist for TELEMETRY (otel logs/metrics/APM). That is NOT the brain corpus; a hit or miss there says nothing about memory. The brain daemon is typically remote (`CL_BRAIN_API`), and recall serves from a local snapshot even when `brain_status` shows `connectivity: offline`.
- Memory ids are `profile:vault:contentSHA` (e.g. `lakeview:memory:<sha>`); the `<sha>` equals the SHA in `brain_recall` results - a brain handle, not an OpenSearch `_id`.

## Tools

| Tool | Use for | Writes to |
|---|---|---|
| `brain_recall` | Search long-term memory (hybrid BM25 + vector). Call this FIRST, before web search or codebase exploration. | reads only |
| `brain_learn` | Ingest a curated note you trust. Skips the LLM gate (curation is the quality signal). | long-term |
| `brain_perceive` | Ingest gathered web content, articles, sources. | long-term |
| `brain_attach` | Ingest a binary (PDF, Word, image). Bytes to MinIO, metadata to OpenSearch. | long-term + MinIO |
| `brain_trace` | Read the synthesis audit log. | reads only |
| `brain_status` | Report brain state: connectivity (online/degraded/offline), queued writes, snapshot age, heartbeat. First stop for debugging. | reads only |
| `task_start` | Begin a working-memory task; auto-seeds from a recall against the goal. | active (local) |
| `task_update` | Log a finding, artifact, or open question. | active |
| `task_complete` | Promote important findings to long-term as a `task_summary` note. | active to long-term |
| `task_get` | Read current task state. | reads only |

## Choosing the write tool

- You wrote or curated it and trust it: `brain_learn`
- You gathered it from the web: `brain_perceive`
- It is a binary file: `brain_attach`
- It is a session's work product: let `task_complete` promote it

All writes are content-addressed by SHA256, so re-ingesting identical content dedups automatically and retries are always safe. Batch multiple sources in one call via `items[]` on `brain_learn` / `brain_perceive`.

## Recall protocol

1. Call `brain_recall` BEFORE any web search or codebase dig.
2. Optionally scope with a `topic` filter: `agents | memory | governance | tools | training | infrastructure | knowledge | multiagent | general`.
3. After any web search, ingest the results: `brain_perceive` for gathered content, `brain_learn` for content you curated. No exceptions.

## Gotchas

- **Snapshot lag: your own writes are not immediately recall-able.** Recall reads a birth-time snapshot. Content you write this session lands in the daemon but will not surface in `brain_recall` until it synthesizes and a new snapshot publishes (typically next agent birth). Do not assume a just-written note is searchable mid-session.
- **No manual synthesize.** `brain_synthesize` does not exist. Synthesis is automatic.
- **Offline never fails a write.** If the daemon is unreachable, writes queue locally and drain on reconnect; the tool result says "Queued". Queued writes are not recall-able until they sync. Inspect with `pbrainctl client queue list`.
- **Updating a value creates a new doc, not an in-place edit.** Content-addressing means a changed body is a new SHA, so it lands as a new entry beside the old one, which can still surface in recall. There is no supersede or forget yet (#72). To retire a stale value today you must delete the old doc out of band.

## Troubleshooting

- Recall feels stale: check `brain_status` for snapshot age.
- A write is "not showing up": expected (snapshot lag). Confirm it synced via `brain_status` queued-writes or `pbrainctl client queue list`.
- Tools missing: the MCP server reconnects per session; reload via `/mcp`.
