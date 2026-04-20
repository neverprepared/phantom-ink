---
name: obsidian-research
description: Memory-first research using Obsidian second brain with web search fallback. Checks stored knowledge before searching online, stores new findings automatically.
---

# Obsidian Research Skill

## Overview

Memory-first research workflow that queries the Obsidian second brain before falling back to web search. New findings are stored automatically so future queries hit local memory instead of the web.

## When to Use

- Answering a question that might already be in the Obsidian knowledge base
- Researching a topic while building a durable, searchable knowledge trail
- Avoiding redundant web searches by checking stored findings first
- Storing a new research finding for reuse in future sessions

Research workflow that checks Obsidian memory first, falls back to web search only when needed, and stores new findings for future recall.

## Workflow

```
1. Search Obsidian memory  → mcp__obsidian-memory__memory_search
2. Evaluate coverage       → Sufficient & fresh? Use cached. Gaps or stale? Continue.
3. Recall full content     → mcp__obsidian-memory__memory_recall (for relevant hits)
4. Web search fallback     → WebSearch / WebFetch (only for gaps)
5. Store new findings      → mcp__obsidian-memory__memory_store
6. Update stale memories   → mcp__obsidian-memory__memory_update
7. Respond with merged     → Combine cached + new, cite sources
```

## Step 1: Search Obsidian Memory

Always search memory first with the user's topic:

```
Tool: mcp__obsidian-memory__memory_search
Arguments:
  query: "<user's question or topic keywords>"
  freshness: "fresh"
  limit: 10
```

Also check for stale results that may need refreshing:

```
Tool: mcp__obsidian-memory__memory_search
Arguments:
  query: "<same query>"
  freshness: "stale"
  limit: 5
```

Run both searches in parallel.

## Step 2: Evaluate Coverage

Use this matrix to decide the next action:

| Fresh Results | Stale Results | Action |
|---------------|---------------|--------|
| >= 3 with score >= 5 | Any | **Use cached** — recall full content, skip web search |
| 1-2 with score >= 5 | Any | **Partial** — recall cached, web search for gaps only |
| 0 | >= 1 | **Refresh** — web search, then update stale memories |
| 0 | 0 | **Full search** — web search everything, store all results |

## Step 3: Recall Full Content

For each relevant fresh result, get the full content:

```
Tool: mcp__obsidian-memory__memory_recall
Arguments:
  id: "<memory_id from search results>"
```

Read the freshness and source_urls fields. If the memory is marked STALE in the recall output, flag it for refresh even if it was returned as a match.

## Step 4: Web Search Fallback

Only search the web for information NOT covered by fresh memories:

```
Tool: WebSearch
Query: "<refined query targeting specific gaps>"
```

For detailed page content:

```
Tool: WebFetch
URL: "<specific documentation URL>"
Prompt: "<what to extract>"
```

When formulating web queries, exclude topics already covered by fresh cached results.

## Step 5: Store New Findings

Store synthesized web research as new memories:

```
Tool: mcp__obsidian-memory__memory_store
Arguments:
  title: "<Descriptive Title - Topic & Scope>"
  content: "<synthesized findings in markdown>"
  para: "resources"
  tags: ["<topic>", "<subtopic>", ...]
  source: "import"
  source_urls: ["https://...", "https://..."]
  ttl_days: <see TTL guidelines below>
  confidence: "medium" or "high"
```

## Step 6: Update Stale Memories

When stale memories exist on the same topic, update them instead of creating duplicates:

```
Tool: mcp__obsidian-memory__memory_update
Arguments:
  id: "<stale_memory_id>"
  content: "<refreshed content>"
  source_urls: ["<new sources>", "<plus original sources worth keeping>"]
```

The `updated` timestamp resets automatically, which resets the TTL clock.

## Step 7: Respond

Combine cached and new information. In your response:
- Lead with the answer, not the process
- Note if information came from cached memory vs fresh web search
- Include source URLs at the end

## TTL Guidelines

| Topic Type | ttl_days | Rationale |
|------------|----------|-----------|
| Cloud service docs (AWS, Azure) | 90 | APIs and features change quarterly |
| Architecture patterns | 180 | Relatively stable |
| Programming language features | 120 | Major releases ~yearly |
| Historical facts / demographics | 365 | Changes slowly |
| News / current events | 7 | Ephemeral |
| Security advisories | 30 | Critical to stay current |
| Personal notes / preferences | 365 | Stable |

## PARA Category Selection

| Content Type | PARA Category |
|-------------|---------------|
| General reference / knowledge | `resources` |
| Active project research | `resources` (link to project via `related`) |
| Ongoing responsibility docs | `areas` |
| Time-bound deliverable notes | `projects` |

Most research output goes to `resources`. Use tags and `related` links to connect to projects rather than storing research in `projects`.

## Best Practices

- **Don't duplicate**: Before storing, check if a memory with similar title/tags already exists. Update it instead.
- **Tag consistently**: Use lowercase, hyphenated tags matching the topic taxonomy (e.g., `aws`, `azure`, `containers`, `networking`).
- **Preserve source URLs**: When updating stale memories, merge new source_urls with existing ones rather than replacing.
- **Confidence levels**: Use `high` only for official documentation and well-sourced data. Use `medium` for synthesized research. Use `low` for unverified or single-source information.
- **Chunk large topics**: Rather than one massive memory, split into focused memories that link to each other via `related` slugs.

## Integration

- **Supersedes**: `web-research` skill for Obsidian-backed workflows
- **Complements**: `research-patterns`, `qdrant-patterns` for vector-backed search
- **Depends on**: `mcp__obsidian-memory__*` tools with freshness fields (last_accessed, source_urls, ttl_days)
