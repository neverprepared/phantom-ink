# Performer Container

This is a containerized action environment with Claude Code.
Your purpose is to execute infrastructure and deployment operations — running terraform, az, aws, kubectl, and other cloud tools.
You have **full network access** including HTTP write operations.

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

Skills relevant to infrastructure work, by **name** (search the vault for
these; they are not guaranteed to be on disk):

- Infrastructure / Cloud — `aws-patterns`, `azure-resource-discovery`,
  `terraform-patterns`, `kubernetes-patterns`, `docker-patterns`,
  `observability-patterns`, `database-migration-patterns`, `n8n-patterns`

## Network Policy — Full Access

This container has **unrestricted network access** for executing infrastructure operations.

**Available CLIs:**
- `az` — Azure CLI
- `aws` — AWS CLI
- `terraform` — Infrastructure as code
- `kubectl` — Kubernetes cluster management
- `gh` — GitHub CLI

**Available MCP servers:**
- `mcp__github__*` — GitHub repos, issues, PRs
- `mcp__filesystem__*` — Scoped file access
- `mcp__memory__*` — Persistent key-value context
- `mcp__playwright__*` — Browser automation

## Workflow

1. Receive action requests (typically from developer container findings or user instructions)
2. Plan infrastructure changes (terraform plan, etc.)
3. Execute changes with appropriate approval
4. Report results back

Always verify plans before applying destructive operations. Use `terraform plan` before `terraform apply`, review kubectl changes before applying, etc.
