---
description: Query and summarize the local session memory log (recent/search/summary/stats)
allowed-tools: Bash(*), AskUserQuestion(*)
argument-hint: [recent|search|summary|stats] [args...]
---

# Memory

Query the local Claude Code session memory log. Events are captured automatically via the PostToolUse hook whenever WebSearch or WebFetch tools are used.

## Paths

```bash
DB_PATH="${REFLEX_HOME:-$HOME/.config/reflex}/memory.db"
MEMORY_PY="${CLAUDE_PLUGIN_ROOT}/scripts/memory.py"
```

---

## Subcommands

### `/reflex:memory` or `/reflex:memory recent`

Show activity from the last 24 hours.

**Instructions:**

1. Run:
   ```bash
   python3 "$MEMORY_PY" recent --hours 24
   ```
2. Parse the JSON lines output.
3. Display grouped by date, then by session:
   ```
   Memory — Last 24 hours
   ──────────────────────────────────────────────
   Today  (14 events)

     Searches
       • how to use sqlite fts5 content tables
       • reciprocal rank fusion python implementation
       • ollama nomic-embed-text dimensions

     Sites visited
       • sqlite.org — FTS5 documentation
       • github.com — sqlite-vec releases
       • ollama.ai — model library
   ```
4. If no events: "No activity logged in the last 24 hours. Memory captures WebSearch and WebFetch events automatically."

---

### `/reflex:memory recent --hours N`

Same as above with a custom look-back window.

```bash
python3 "$MEMORY_PY" recent --hours 72
```

---

### `/reflex:memory search <query>`

Full-text search across all recorded queries, titles, and notes.

**Instructions:**

1. Run:
   ```bash
   python3 "$MEMORY_PY" search "$QUERY"
   ```
2. Display results:
   ```
   Search: "sqlite fts5"  (8 matches)
   ──────────────────────────────────────────────
   2026-03-31 14:22  web_search   how to use sqlite fts5 content tables
   2026-03-31 14:23  web_fetch    sqlite.org — FTS5 documentation
   2026-03-30 09:11  web_search   sqlite fts5 vs fts4 differences
   ```
3. If no results: "No matches found for '{query}'."

---

### `/reflex:memory summary [YYYY-MM-DD]`

Generate or retrieve a daily summary. Defaults to today.

**Instructions:**

1. Run:
   ```bash
   python3 "$MEMORY_PY" summarize --day "$DAY"
   ```
2. Display:
   ```
   Summary — 2026-03-31
   ──────────────────────────────────────────────
   The session focused on designing a local-first memory system for Claude Code,
   researching SQLite FTS5 internals, hybrid vector retrieval with Reciprocal Rank
   Fusion, and tuning sqlite-vec for local embedding storage.

   Key topics (9): sqlite fts5, reciprocal rank fusion, sqlite-vec, ollama embeddings,
                   WAL mode, langfuse hook pattern, vector index design, nomic-embed-text,
                   reflex skill architecture

   Events logged: 23
   ```
3. If ollama is unavailable, the plain aggregate is shown instead — note this at the bottom:
   `(summary generated from event log — ollama unavailable for LLM assist)`

---

### `/reflex:memory stats`

Show event volume grouped by type and day.

**Instructions:**

1. Run:
   ```bash
   python3 "$MEMORY_PY" stats
   ```
2. Display as a simple table:
   ```
   Activity Stats
   ──────────────────────────────────────────────
   Day          Type          Count
   2026-03-31   web_search    17
   2026-03-31   web_fetch     6
   2026-03-30   web_search    12
   2026-03-30   web_fetch     4
   ```

---

### No argument match

```
Usage: /reflex:memory [subcommand] [args...]

Subcommands:
  recent [--hours N]          Recent activity (default: 24h)
  search <query>              Full-text search across all events
  summary [YYYY-MM-DD]        Daily summary (default: today)
  stats                       Event counts by type and day

The memory log is at: ${REFLEX_HOME:-$HOME/.config/reflex}/memory.db
Events are captured automatically for WebSearch and WebFetch tool calls.
```
