---
name: workspace-builder
description: Set up and configure development workspaces
---


# Workspace Builder Skill

> Master specification for building the agentic workflow system.
> This skill is **reference documentation** - use component-specific skills for building.

## Overview

This workspace provides a reusable, multi-project automation system with:
- Semantic routing for intelligent resource selection
- RAG (vector search) with project isolation
- Modular agents, skills, and commands
- Template-based architecture for cloning to new projects

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER QUERY                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SEMANTIC ROUTER                               │
│  Tier 1: Category (command | agent | skill | workflow)          │
│  Tier 2: Specific resource (e.g., "researcher" agent)           │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌─────────┐     ┌─────────┐     ┌─────────┐
        │Commands │     │ Agents  │     │Workflows│
        └─────────┘     └─────────┘     └─────────┘
                              │
                              ▼
                    ┌──────────────────────────┐
                    │   Second Brain MCP       │
                    │   (sqlite-vec + FTS5)    │
                    └──────────────────────────┘
```

## Component Build Order

Build in this sequence for incremental testing:

### Phase 1: Foundation
1. **Directory structure** ✅
2. **CLAUDE.md** ✅
3. **Config files** (base.yaml, .env.template)
4. **Setup scripts** (setup.sh, init-project.sh)

### Phase 2: Core Services
5. **RAG Server** → See `skills/rag-builder/SKILL.md`
6. **Router** → See `skills/router-builder/SKILL.md`

### Phase 3: Interface Layer
7. **Slash Commands** (research, code-review, daily-standup)
8. **MCP Config** (wire up servers)

### Phase 4: Agents
9. **Sub-agents** → See `skills/agent-builder/SKILL.md`
10. **Orchestrator** (ties everything together)

### Phase 5: Automation
11. **Workflows** (YAML definitions + executor)
12. **Service management** (start/stop scripts)

## Key Technical Decisions

### Vector + Keyword Storage: obsidian-second-brain MCP

The second-brain MCP owns the vault's storage layer. One SQLite file at `{vault}/_index/vectors.db` holds both a sqlite-vec KNN index and an FTS5 BM25 index, sharing the same connection. Hybrid search (vector + keyword, fused by rank) is the default when both indexes are populated.

Why this instead of a standalone vector service:
- Single file, no daemon to run alongside the workspace
- Hybrid search out of the box (vectors miss exact identifiers; FTS5 misses paraphrase)
- PARA-aware storage with TTL/freshness/confidence built into the schema

### Embeddings: nomic-embed-text via Ollama

The MCP embeds memory content via local Ollama (`OLLAMA_BASE_URL`, default `http://localhost:11434`) using `nomic-embed-text` (768 dims). No API key, no remote round-trip.

### Routing: Semantic Router
```python
# Why Semantic Router:
# - ~10ms decisions (not LLM calls)
# - Scales to 1000s of resources
# - Same embeddings as RAG

from semantic_router import Route, RouteLayer
```

## Configuration Strategy

### Layered Config
```
config/base.yaml      # Defaults (version controlled)
config/local.yaml     # Overrides (git-ignored)
.env                  # Secrets (git-ignored)
```

### Multi-Project Pattern
```bash
# Clone template
git clone <repo> project-alpha
cd project-alpha

# Initialize project
./scripts/init-project.sh project-alpha

# Creates:
# - .env.project-alpha (credentials)
# - config/profiles/project-alpha.yaml
# - Isolated RAG collections
```

## File Templates

### Slash Command Template
```markdown

# Command Name

You are executing the **command-name** command.

## Instructions

1. First step
2. Second step
3. Output format

## Output

Describe expected output format.
```

### Agent Prompt Template
```markdown
# Agent Name

You are a specialized **Agent Name** focused on [domain].

## Core Capabilities

1. Capability one
2. Capability two

## Tools Available

- `tool_name`: Description

## Operating Principles

- Principle one
- Principle two

## Output Standards

- Standard one
- Standard two
```

### Route Definition Template
```yaml
routes:
  - name: resource-name
    utterances:
      - "example phrase one"
      - "example phrase two"
      - "variation three"
      - "variation four"
      - "at least 5-10 examples"
    metadata:
      file: "path/to/resource"
      description: "What this resource does"
```

## Testing Strategy

### Incremental Testing
```bash
# Test RAG server
python -c "from rag.server import RAGServer; print('RAG OK')"

# Test router
python -c "from routing.router import route; print(route('test query'))"

# Test full flow
python -c "
from routing.router import route
result = route('research quantum computing')
print(f'Routed to: {result.category}/{result.resource_name}')
"
```

### Integration Test
```bash
# Start all services
./scripts/start-services.sh

# Test via MCP
# (use Claude Code to interact)
```

## Dependencies

```
# requirements.txt
pyyaml>=6.0
python-dotenv>=1.0.0
mcp>=1.0.0
semantic-router>=0.1.0
aiofiles>=23.0.0
httpx>=0.25.0
# Vector + FTS storage for the vault is provided by the obsidian-second-brain
# MCP (Node, not Python). Install separately: npm i -g @neverprepared/mcp-obsidian-second-brain
```

