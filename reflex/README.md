# Reflex Plugin Marketplace

[![Release](https://img.shields.io/github/v/release/mindmorass/reflex)](https://github.com/mindmorass/reflex/releases)

A Claude Code plugin for application development, infrastructure, and data engineering workflows.

## Installation

```
/plugin marketplace add mindmorass/reflex
/plugin install reflex
```

## Features

| Component | Count | Description |
|-----------|-------|-------------|
| Skills | 36 | Development patterns, harvesting, infrastructure, knowledge management |
| Commands | 19 | `/reflex:agents`, `/reflex:skills`, `/reflex:handoff`, etc. |
| Agents | 2 | `rag-proxy`, `workflow-orchestrator` |

## Docker Services

The `docker/` directory at the **monorepo root** contains Docker Compose configurations for supporting services:

| Service | Purpose | Port |
|---------|---------|------|
| [LangFuse](../docker/langfuse) | LLM observability | 3000 |

### Quick Start

```bash
# LangFuse (optional — for observability)
cd ../docker/langfuse
cp .env.example .env
# Edit .env and generate secrets
docker compose up -d
```

> Vector + keyword storage for the second-brain vault is provided by the [`obsidian-second-brain`](https://github.com/neverprepared/mcp-obsidian-second-brain) MCP, which uses sqlite-vec + FTS5 in a single SQLite file at `{vault}/_index/vectors.db`. No separate service to run.

## Structure

```
reflex/
└── plugins/reflex/        # Main plugin
    ├── agents/            # Sub-agents
    ├── skills/            # 42 skill definitions
    ├── commands/          # Slash commands
    ├── hooks/             # Session hooks
    └── scripts/           # Helper scripts

../docker/                 # Docker services (monorepo root)
    └── langfuse/          # LLM observability
```

## License

MIT
