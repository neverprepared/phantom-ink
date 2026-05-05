---
description: List available Reflex agents
allowed-tools: Bash(ls:*)
---

# Reflex Agents

List all available agents in the Reflex plugin.

## Command

```bash
!ls -1 plugins/reflex/agents/*.md 2>/dev/null | xargs -I {} basename {} .md | while read a; do echo "- **$a**"; done
```

## Available Agents

| Agent | Purpose | Key Skills |
|-------|---------|------------|
| rag-proxy | RAG wrapper for any agent — enriches with second-brain (sqlite-vec + FTS5) context before delegation | obsidian-research |

## Note

Most agent functionality is now provided by:
- **Official plugins**: testing-suite, security-pro, documentation-generator, developer-essentials
- **Skills**: analysis-patterns, obsidian-research, task-decomposition, etc.

Use the Task tool directly with official plugin agents, or invoke skills for domain-specific guidance.
