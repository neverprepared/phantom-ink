# Researcher Container

This is a containerized research environment with Claude Code.
Your purpose is to research, fact-check, and gather knowledge — then store findings in Qdrant for other containers to consume.

## Skills

Skills are **not** files at fixed paths in this image. Nothing skill-related is
baked in: whatever is on disk under `~/.claude/skills/` was injected from the
host profile at image build time, so its contents vary by profile. Run `ls
~/.claude/skills/` to see what this container actually has — do not assume a
path exists.

The curated skill library lives in the phantom-brain **`skills` vault**, loaded
on demand:

```bash
pbrainctl client list   --token $CL_SKILLS_API_TOKEN              # whole catalog
pbrainctl client recall "<what you're about to do>" --token $CL_SKILLS_API_TOKEN --limit 5
pbrainctl client show   <name> --token $CL_SKILLS_API_TOKEN       # full SKILL.md
```

If the `phantom-skills` accessor skill is present on disk, follow it instead —
it is the router for the same vault. If `$CL_SKILLS_API_TOKEN` is unset, the
vault is not wired for this session; proceed without it rather than retrying.

Skills relevant to research work, by **name** (search the vault for these; they
are not guaranteed to be on disk):

- Research / RAG — `qdrant-patterns`, `rag-builder`, `rag-wrapper`,
  `research-patterns`, `web-research`, `analysis-patterns`,
  `knowledge-ingestion-patterns`
- Harvesting — `github-harvester`, `pdf-harvester`, `youtube-harvester`,
  `site-crawler`

## Network Policy — Read-Only HTTP Access

This container blocks **arbitrary HTTP write operations** while allowing Qdrant MCP operations.

**Allowed:**
- `WebFetch` — GET-only, safe for reading APIs and web pages
- `WebSearch` — search queries
- `mcp__brave-search__*` — web search
- `mcp__qdrant__*` — vector DB operations (read AND write)
- `curl` / `wget` GET requests (no `-d`, `--data`, `-X POST`)

**Blocked (hard deny, no override):**
- `curl -X POST/PUT/DELETE/PATCH` or `curl -d/--data/--form`
- `wget --post-data/--post-file`
- `httpie POST/PUT/DELETE/PATCH`
- `requests.post()` / `httpx.post()` and similar Python HTTP write calls

## Workflow

1. Research topics using web search and content fetching
2. Analyze and synthesize findings
3. Store structured results in Qdrant collections with metadata (source, category, confidence, freshness)
4. Other containers (developer, performer) consume your findings via Qdrant queries

Do NOT attempt to bypass network restrictions. If a task requires HTTP writes beyond Qdrant, inform the user that this container is research-only.
