# OpenAI Codex CLI — Integration Research

> **Date:** 2026-04-12
> **Status:** Research / Draft
> **Purpose:** Evaluate OpenAI Codex CLI for integration with the phantom-ink architecture

---

## Table of Contents

1. [Overview](#overview)
2. [Installation](#installation)
3. [Authentication](#authentication)
4. [Environment Variables](#environment-variables)
5. [CLI Usage Examples](#cli-usage-examples)
6. [Supported Models](#supported-models)
7. [Docker Support](#docker-support)
8. [Configuration Reference](#configuration-reference)
9. [Integration with phantom-ink](#integration-with-phantom-ink)

---

## Overview

Codex CLI is OpenAI's open-source coding agent that runs locally in a terminal. Written in Rust for speed and efficiency, it can read, modify, and execute code on the local machine within a selected working directory. It is the CLI counterpart to the Codex Web product at `chatgpt.com/codex`.

**Key capabilities:**
- Interactive terminal UI (full-screen TUI)
- Non-interactive scripting / CI mode (`codex exec`)
- Image input (screenshots, design specs)
- Local code review (`/review` command)
- Web search (first-party, enabled by default)
- Model Context Protocol (MCP) server support
- Configurable sandbox and approval policies

**Source:** https://github.com/openai/codex  
**License:** Apache-2.0  
**Primary Language:** Rust

---

## Installation

### npm (recommended for CI/automation)

```bash
npm install -g @openai/codex
```

Upgrade:

```bash
npm install -g @openai/codex@latest
```

### Homebrew (macOS)

```bash
brew install --cask codex
```

### Pre-built Binaries

Download from the [latest GitHub Release](https://github.com/openai/codex/releases/latest). Available platform binaries:

| Platform | Archive |
|----------|---------|
| macOS (Apple Silicon) | `codex-aarch64-apple-darwin.tar.gz` |
| macOS (x86_64) | `codex-x86_64-apple-darwin.tar.gz` |
| Linux (x86_64) | `codex-x86_64-unknown-linux-musl.tar.gz` |
| Linux (arm64) | `codex-aarch64-unknown-linux-musl.tar.gz` |

After downloading, extract and rename the binary to `codex`, then place it on `$PATH`.

**Binary name:** `codex`  
**npm package:** `@openai/codex`

---

## Authentication

### Option 1 — Sign in with ChatGPT (recommended)

Run `codex` and select **Sign in with ChatGPT**. Requires a ChatGPT Plus, Pro, Business, Edu, or Enterprise subscription.

```bash
codex login    # OAuth browser flow or API key prompt
codex logout   # Remove stored credentials
```

### Option 2 — API Key

Set `OPENAI_API_KEY` as an environment variable before running `codex`. Also accepted as `CODEX_API_KEY`. Detailed steps at https://developers.openai.com/codex/auth#sign-in-with-an-api-key.

### Option 3 — Azure OpenAI

Configure a custom provider in `~/.codex/config.toml`:

```toml
[model_providers.azure]
base_url = "https://YOUR_PROJECT_NAME.openai.azure.com/openai"
env_key = "AZURE_OPENAI_API_KEY"
wire_api = "responses"
```

Then set `AZURE_OPENAI_API_KEY` in the environment.

---

## Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `OPENAI_API_KEY` | OpenAI API key for authentication | Yes (if not using OAuth login) |
| `CODEX_API_KEY` | Alternative name for the OpenAI API key | Alternative to above |
| `AZURE_OPENAI_API_KEY` | API key when using Azure OpenAI provider | Azure only |
| `CODEX_HOME` | Override location of `~/.codex` config directory | No |

### codex-universal Docker image — Language Version Variables

When using the `ghcr.io/openai/codex-universal` Docker image, runtime versions are selected via `CODEX_ENV_*` variables:

| Variable | Accepted Values |
|----------|----------------|
| `CODEX_ENV_PYTHON_VERSION` | `3.10`, `3.11.12`, `3.12`, `3.13` |
| `CODEX_ENV_NODE_VERSION` | `18`, `20`, `22` |
| `CODEX_ENV_RUST_VERSION` | `1.83.0`, `1.84.1`, `1.85.1`, `1.86.0`, `1.87.0` |
| `CODEX_ENV_GO_VERSION` | `1.22.12`, `1.23.8`, `1.24.3` |
| `CODEX_ENV_SWIFT_VERSION` | `5.10`, `6.1` |

---

## CLI Usage Examples

### Interactive mode (default)

```bash
# Launch TUI
codex

# Open with an initial prompt
codex "explain this codebase"

# Attach an image (screenshot / design spec)
codex --image screenshot.png "implement the UI shown here"
```

### Non-interactive / scripting mode (`exec`)

```bash
# Run a task headlessly, output to stdout
codex exec "fix all TypeScript compile errors"

# JSON event stream (useful in CI)
codex exec --json "run tests and summarize failures"

# Capture final message to a file
codex exec --output-last-message result.txt "add docstrings to all functions"

# Skip safety prompts (use inside a hardened sandbox only)
codex exec --full-auto --dangerously-bypass-approvals-and-sandbox "refactor auth module"
```

### Model selection

```bash
# Specify model at launch
codex --model gpt-5.4 "add unit tests"

# Switch model mid-session with the /model slash command (interactive mode)
```

### Session management

```bash
codex resume          # Continue the most recent session
codex fork            # Branch current session into a new thread
codex apply           # Apply a cloud task diff locally
```

### Sandbox and approval policies

```bash
# Limit to read-only (consultative mode)
codex --sandbox read-only "review this code for security issues"

# Full workspace write access, no approval prompts
codex --sandbox workspace-write --ask-for-approval never "update all dependencies"
```

### Shell completions

```bash
codex completion bash   # Generate bash completions
codex completion zsh    # Generate zsh completions
codex completion fish   # Generate fish completions
```

---

## Supported Models

| Model | Notes |
|-------|-------|
| `gpt-5.4` | Primary recommended model; combines frontier coding with strong reasoning and native computer use |
| `gpt-5.3-codex` | Strong coding-optimised model |
| `gpt-5.3-codex-spark` | Extra-fast variant; ChatGPT Pro subscribers only (research preview) |
| Custom / OSS | Use `--oss` flag to point at a local Ollama instance for open-source models |

Switch models mid-session via the `/model` slash command in interactive mode, or set a default in config:

```toml
model = "gpt-5.4"
```

---

## Docker Support

### Official reference image: `codex-universal`

OpenAI publishes a reference Dockerfile and pre-built image at:

```
ghcr.io/openai/codex-universal:latest
```

Source: https://github.com/openai/codex-universal

**Pull and run locally:**

```bash
docker pull ghcr.io/openai/codex-universal:latest

docker run --rm -it \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e CODEX_ENV_PYTHON_VERSION=3.12 \
  -e CODEX_ENV_NODE_VERSION=20 \
  -e CODEX_ENV_RUST_VERSION=1.87.0 \
  -v "$(pwd):/workspace/project" \
  -w /workspace/project \
  ghcr.io/openai/codex-universal:latest
```

**Pre-installed runtimes in `codex-universal`:** Python, Node.js, Rust, Go, Swift, and common package managers (npm, yarn, pnpm, pip, poetry, etc.).

### Running the Codex CLI binary inside any Docker container

```dockerfile
FROM node:20-alpine

# Install Codex CLI
RUN npm install -g @openai/codex

# Provide API key at runtime via env var
ENV OPENAI_API_KEY=""

WORKDIR /workspace
```

```bash
docker run --rm \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -v "$(pwd):/workspace" \
  my-codex-image \
  codex exec "add docstrings to all Python files"
```

### Docker Sandbox (Docker Desktop integration)

Docker Desktop 4.x integrates Codex CLI as a first-class sandbox via `sbx`:

```bash
# Run Codex in a Docker sandbox
sbx run codex ~/my-project

# Store API key as a managed secret
sbx secret set -g openai
```

The sandbox template is `docker/sandbox-templates:codex`. It does **not** inherit `~/.codex` from the host; only project-level `.codex/config.toml` inside the working directory is accessible.

### Cloud execution model

In the Codex Web cloud environment, each task runs in a fresh container based on `codex-universal`. The execution flow is:

1. Container creation and repository checkout
2. Setup script runs (internet access enabled)
3. Agent phase runs terminal commands in a loop
4. Results returned as file diffs

Containers are cached for up to 12 hours. Environment variables and secrets set in the environment are available to setup scripts; secrets are **removed before the agent phase** for security.

---

## Configuration Reference

### Config file location

```
~/.codex/config.toml          # User-level defaults
<repo>/.codex/config.toml     # Project-level overrides (committed to repo)
```

### Core options

```toml
# Model
model = "gpt-5.4"
model_provider = "openai"
model_reasoning_effort = "high"
model_context_window = 128000

# Sandbox & approval
approval_policy = "on-request"   # untrusted | on-request | never
sandbox_mode = "workspace-write" # workspace-write | danger-full-access

# Shell environment passthrough
[shell_environment_policy]
inherit = "core"
exclude = ["AWS_*"]

# Disable session history
[history]
persistence = "none"

# OpenTelemetry export
[otel]
exporter = "otlp-http"
endpoint = "https://otel.example.com/v1/logs"
log_user_prompt = false
```

---

## Integration with phantom-ink

phantom-ink's architecture (see [`docs/architecture-overview.md`](./architecture-overview.md)) centres on:

- **Brainbox** — FastAPI + Svelte session manager that provisions sandboxed Docker containers
- **Reflex** — Claude Code plugin with skills, slash commands, agents, workflow templates, and hooks
- **Three container roles** — `developer` (full access), `performer` (restricted execution), `researcher` (read-only)
- **Supporting infrastructure** — Qdrant vector DB, LangFuse observability, MinIO object storage

Below are concrete integration points:

### 1. Codex as a Brainbox container role

A **fourth container role** (`codex-agent`) can be added alongside the existing three. Brainbox would provision the container from `ghcr.io/openai/codex-universal:latest` and inject `OPENAI_API_KEY` from its Secrets Manager.

```yaml
# docker/brainbox/docker-compose.yml (new service)
codex-agent:
  image: ghcr.io/openai/codex-universal:latest
  environment:
    - OPENAI_API_KEY=${OPENAI_API_KEY}
    - CODEX_ENV_NODE_VERSION=20
    - CODEX_ENV_PYTHON_VERSION=3.12
  volumes:
    - workspace:/workspace
  working_dir: /workspace
```

### 2. Reflex skill: `reflex:codex-exec`

A new Reflex skill could wrap `codex exec` for non-interactive task dispatch from within a Claude Code session. The skill would:
- Accept a natural-language task string
- Run `codex exec --json "<task>"` inside the appropriate Brainbox container
- Stream JSON events back to Claude Code via the existing SSE event stream
- Store findings in Qdrant via the existing `ingest.py` script

### 3. CI/CD via `codex exec`

Phantom-ink's Justfile already drives automation. Adding a `codex` target would enable AI-powered code tasks in the pipeline:

```makefile
codex-fix:
    codex exec --full-auto --dangerously-bypass-approvals-and-sandbox \
        "fix all linting errors and run tests"
```

For GitHub Actions, the workflow would install Codex with `npm i -g @openai/codex` and supply `OPENAI_API_KEY` from repository secrets.

### 4. MCP bridge

Codex CLI supports Model Context Protocol (MCP) servers. Since Brainbox exposes an MCP server endpoint (`/api/mcp`), Codex can be configured to use Brainbox as an MCP tool provider, giving Codex direct access to container lifecycle management:

```toml
# .codex/config.toml (project-level)
[mcp.servers.brainbox]
command = "npx"
args = ["-y", "@brainbox/mcp-server"]
env = { BRAINBOX_URL = "http://localhost:8000" }
```

### 5. LangFuse observability

Codex's built-in OpenTelemetry export can be pointed at LangFuse's OTLP ingest endpoint, unifying LLM observability across Claude Code (Reflex) and Codex CLI sessions:

```toml
# ~/.codex/config.toml
[otel]
exporter = "otlp-http"
endpoint = "http://localhost:3000/api/public/otelTraces"
```

### 6. Shell Profiler integration

The `shell-profiler` Go CLI manages per-workspace direnv profiles. Adding a `codex` profile type would inject `OPENAI_API_KEY` and default `CODEX_HOME` automatically whenever a developer enters a workspace that uses Codex.

---

## Summary

| Aspect | Detail |
|--------|--------|
| npm package | `@openai/codex` |
| Binary | `codex` |
| Primary auth env var | `OPENAI_API_KEY` (also `CODEX_API_KEY`) |
| Scripting / CI command | `codex exec` |
| Docker image | `ghcr.io/openai/codex-universal:latest` |
| Recommended model | `gpt-5.4` |
| Config file | `~/.codex/config.toml` |
| License | Apache-2.0 |
| Platform support | macOS, Linux (stable); Windows (experimental/WSL) |

---

## References

- GitHub repo: https://github.com/openai/codex
- npm package: https://www.npmjs.com/package/@openai/codex
- Official docs quickstart: https://developers.openai.com/codex/quickstart
- CLI reference: https://developers.openai.com/codex/cli/reference
- Advanced config: https://developers.openai.com/codex/config-advanced
- Docker universal image: https://github.com/openai/codex-universal
- Docker Desktop sandbox: https://docs.docker.com/ai/sandboxes/agents/codex/
- Auth guide: https://developers.openai.com/codex/auth
