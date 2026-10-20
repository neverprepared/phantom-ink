# Developer Container

## Auto-Task

If a task was provided at session launch it arrives as your first prompt
(fetched from the hub's session store at startup). Begin executing it
immediately — do not wait for further prompts. When the task is fully
complete, run `brainbox-complete "<brief summary>"` to record the result
and signal the hub (legacy images: `~/.brainbox/complete.sh`).

This is a containerized development environment with Claude Code.
You have access to the GitHub CLI (`gh`) for GitHub operations.
The environment includes Python, Node.js, Go, and Rust toolchains.

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

Skills relevant to development work, by **name** (search the vault for these;
they are not guaranteed to be on disk):

- Infrastructure / Cloud — `aws-patterns`, `azure-resource-discovery`,
  `terraform-patterns`, `kubernetes-patterns`, `docker-patterns`,
  `observability-patterns`, `database-migration-patterns`, `n8n-patterns`,
  `qdrant-patterns`
- Development — `agent-builder`, `workflow-builder`, `router-builder`,
  `mcp-server-builder`, `workspace-builder`, `project-onboarding`,
  `task-decomposition`, `analysis-patterns`, `rag-builder`, `rag-wrapper`

## Network Policy — Read-Only HTTP Access

This container blocks **arbitrary HTTP write operations** while allowing git and GitHub workflows.

**Allowed:**
- `WebFetch` — GET-only, safe for reading APIs and web pages
- `WebSearch` — search queries
- `mcp__brave-search__*` — web search
- `mcp__github__*` — all GitHub MCP tools (read and write)
- `mcp__qdrant__*` — vector DB operations
- `git push` / `git pull` — full git remote access
- `gh` CLI — all commands (create PRs, issues, etc.)
- `curl` / `wget` GET requests (no `-d`, `--data`, `-X POST`)

**Blocked (hard deny, no override):**
- `curl -X POST/PUT/DELETE/PATCH` or `curl -d/--data/--form`
- `wget --post-data/--post-file`
- `httpie POST/PUT/DELETE/PATCH`
- `requests.post()` / `httpx.post()` and similar Python HTTP write calls
- `mcp__playwright__click`, `fill`, `type`, `submit`, and other page interaction tools

Do NOT attempt to bypass these restrictions. If a task requires raw HTTP writes (outside of git/GitHub), inform the user that this container blocks arbitrary HTTP write operations.
