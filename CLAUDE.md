# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository.

## Overview

`phantom-ink` is a **monorepo** for a local-first AI-agent platform. Its pieces:

| Path | What it is | Language |
|---|---|---|
| `app/` | **phantom-ink** — the Wails 2 macOS desktop app (command center) | Go 1.25 + Svelte 5 / TS |
| `brainbox/` | **Brainbox** — FastAPI orchestration server, MCP server, MCP gateway, agent hub | Python 3.11+ |
| `shell-profiler/` | CLI for direnv-backed workspace profiles | Go 1.25 |
| `reflex/` | Claude Code plugin marketplace (skills, commands, agents, hooks) | Markdown + Python |
| `packages/brainbox-mcp/` | Guest-side MCP helper shipped **into** a session container | Python |
| `packages/brainbox/` | Homebrew/packaged brainbox launcher (`brainbox.sh`, compose) | Shell |
| `tier0/` | opencode + local ollama harness wired to the MCP gateway | Shell/JSON |
| `contracts/` | Collection-output JSON Schema (array framing over the timeline-entry contract) | JSON |
| `docker/` | Compose stacks for local services (langfuse, minio, opensearch, brainbox) | YAML |
| `Formula/` | Homebrew formulae | Ruby |
| `docs/` | Architecture docs + ADRs | Markdown |

Root `Justfile` is the task runner for every component. `just` with no args lists all recipes.

## Architecture

```
shell-profiler ── direnv profiles (git identity, creds, tool config)
        │
phantom-ink (Wails desktop app)
  ├─ Go backend (app/*.go)         bound wholesale to the frontend via Wails `Bind`
  │    ├─ app_*.go                 one file per feature surface (profiles, services,
  │    │                           brainbox, gateway, jobs, runners, tokens, mesh, …)
  │    ├─ db*.go                   local SQLite state (~/.phantom-ink.db) + migrations
  │    ├─ brainbox/                HTTP client + SSE listener for the Brainbox API
  │    └─ internal/contract        codegen'd timeline-entry bindings (pinned tag)
  └─ Svelte 5 frontend (app/frontend/src/lib/panels/*.svelte — 19 panels)

Brainbox (brainbox/src/brainbox)
  ├─ api.py            FastAPI app, ~142 routes (/api/sessions, /api/hub/*, /api/rules,
  │                    /api/runners, /api/tokens, /api/events SSE, /api/agent_events, …)
  ├─ lifecycle.py      provision / run / recycle a session container
  ├─ backends/         docker + utm execution backends, nginx routing
  ├─ hub.py, registry.py, agent_store.py, messages.py — agent hub
  ├─ conversation_*.py  multi-agent Chat: store, runtime/SSE, turn orchestrator,
  │                     promote-a-message, promote-to-session
  ├─ mcp_server.py     the operator-facing MCP server (`brainbox mcp`)
  ├─ gateway_*.py      the MCP **gateway**: per-profile, scoped tool plane (ADR-002),
  │                    mounted at `/gateway` in api.py
  ├─ loop*.py          markdown-defined agent loops (runner, judge, mermaid, templates)
  ├─ node_sync*.py     local-first P2P node sync
  └─ dashboard/        Svelte dashboard served by the API
```

Read `docs/architecture-overview.md`, `docs/architecture/ADR-001-agent-orchestration.md`,
and `docs/architecture/ADR-002-mcp-gateway.md` before making structural changes.

## Key commands

All from the repo root unless noted.

```bash
just                       # list every recipe

# App (Wails)
just app-dev               # live-reload dev (wails dev)
just app-build             # darwin/universal build -> app/build/bin/phantom-ink.app
just app-clean
just app-contract-gen      # regenerate contract bindings; a re-run must produce NO diff
cd app && go test ./... -race          # app tests (CI also runs -coverprofile)
cd app/frontend && npm run check       # svelte-check

# Brainbox (Python, uv)
just bb-api                # uv run python -m brainbox api        (port 9999)
just bb-mcp                # uv run python -m brainbox mcp
just bb-build              # uv sync + build the dashboard
just bb-test               # pytest
just bb-lint               # ruff check src/
just bb-daemon-start|stop|status|restart|logs
just bb-docker-build / bb-docker-start

# shell-profiler (Go)
just sp-build              # -> shell-profiler/bin/shell-profiler
just sp-test               # go test ./...
just sp-lint               # golangci-lint run

# Cross-cutting
just test-all              # bb-test + sp-test
just lint-all              # bb-lint + sp-lint
just validate-output ./script.sh    # validate collection output against the contract (needs ajv-cli)

# Reflex plugin
just reflex-dev            # claude --plugin-dir reflex

# Local service stacks
just opensearch-start|stop|logs|status
just reflex-langfuse
```

`brainbox` CLI subcommands (`python -m brainbox <cmd>`): `provision`, `run`, `recycle`,
`api`, `mcp`, `stop`, `status`, `restart`. Also `manage-secrets`.

`shell-profiler` subcommands: `init`, `create|new|add`, `update|upgrade`, `list|ls`,
`select|use`, `delete|remove|rm`, `restore`, `info|current|show`, `status`, `sync`,
`dotfiles`, `help`.

## MCP surfaces

Three distinct MCP things live here — do not conflate them:

1. **`brainbox mcp`** (`brainbox/src/brainbox/mcp_server.py`) — operator MCP server over
   the Brainbox REST API. Tools: `list_sessions`, `create_session`, `start_session`,
   `stop_session`, `delete_session`, `get_session`, `push_config`, `exec_session`,
   `query_session`, `refresh_secrets`, `get_metrics`, `submit_task`, `get_task`,
   `list_tasks`, `cancel_task`, `get_hub_state`, `get_message_log`, `list_agents`,
   `get_agent`, `list_tokens`, `api_info`, `multiclaude_status`, `get_langfuse_health`,
   `get_qdrant_health`, `get_langfuse_session_traces`, `get_langfuse_session_summary`,
   `get_langfuse_trace_detail`, `channel_read`, `channel_send`, `channel_complete`,
   `channel_join`, `get_event_schema`. Resource: `contract://events/timeline-entry`.
   The four `channel_*` tools keep their historical names but talk to the
   **conversation** engine (`/api/conversations/...`) — the in-memory channels
   hub they were written against was retired; their `channel_id` argument is a
   conversation ULID.
2. **`brainbox-mcp`** (`packages/brainbox-mcp`) — the trimmed *guest* variant installed in
   session containers; a subset of the above (sessions, tasks, hub, agents, metrics).
3. **The MCP gateway** (`gateway_server.py`, mounted at `/gateway`) — the security
   boundary. A Bearer token resolves to `(profile, scope)`; downstream per-profile
   servers are aggregated and namespaced `<server>__<tool>`, filtered by scope and by
   trust-zone residency. Server definitions come from the gateway catalog.

## Conventions

- **Wails binding**: the whole `App` struct is bound. A new frontend-callable method is a
  new exported method on `*App` in an `app_<feature>.go` file — then regenerate/refresh
  `app/frontend/wailsjs`.
- **App state** is SQLite at `~/.phantom-ink.db`; schema changes go through
  `app/db_migrations.go`, never ad-hoc DDL.
- **Contract codegen is a gate**: `app/internal/contract/*` is generated from the
  `phantom-contracts` tag in `app/internal/contract/CONTRACT_TAG`. Never hand-edit it;
  run `just app-contract-gen`. CI (`contract-freshness.yml`) fails on drift.
- **Event bus**: emitting or consuming bus events? Follow
  `docs/event-bus-conformance.md` (`app/app_conformance_test.go` enforces it).
- **Python**: ruff, `line-length = 100`, target py311; `pytest` with `asyncio_mode = "auto"`,
  tests in `brainbox/tests/`. Dependencies are managed with **uv** (`uv sync`, `uv run`).
- **Go**: standard `go test ./... -race`; app module is `phantom-ink`, profiler module is
  `github.com/neverprepared/shell-profile-manager`.
- **Secrets never land in git**: `.gitignore` excludes `docker/**/.env`, `.env.secrets`,
  `.env.local`, and `docker/volumes/`. Compose `.env` files carry bootstrap credentials.
- **CI is path-filtered** (`.github/workflows/`): `test-app`, `test-brainbox`,
  `test-reflex`, `test-shell-profiler` only run when their subtree changes, so a
  root-only or docs-only change triggers **no** checks. Build/release workflows are
  tag- or path-triggered (app, containers, brainbox pkg, brainbox-mcp pkg,
  shell-profiler, reflex).
- **CLAUDE.md is excluded by a global gitignore** in this environment — commit it with
  `git add -f CLAUDE.md`.
