# Root Justfile — phantom-ink platform monorepo

default:
    @just --list --unsorted

# === App (Wails desktop, Go + Svelte) ===

# Regenerate the timeline-entry Go bindings + committed schema from the pinned
# phantom-contracts tag (app/internal/contract/CONTRACT_TAG). Needs SSH access to
# neverprepared/phantom-contracts. A re-run must produce no git diff.
app-contract-gen:
    cd app && go generate ./internal/contract

app-dev:
    cd app && $(shell which wails 2>/dev/null || echo $(HOME)/go/bin/wails) dev

app-build:
    cd app && $(shell which wails 2>/dev/null || echo $(HOME)/go/bin/wails) build -platform darwin/universal

app-clean:
    rm -rf app/build/bin app/frontend/dist

# === Brainbox (Python) ===

bb-api:
    cd brainbox && uv run python -m brainbox api

bb-build:
    cd brainbox && uv sync
    cd brainbox/dashboard && npm install && npx vite build

bb-test:
    cd brainbox && uv run python -m pytest

bb-lint:
    cd brainbox && uv run ruff check src/

bb-mcp:
    cd brainbox && uv run python -m brainbox mcp

bb-dashboard:
    cd brainbox && npm run dashboard

bb-daemon-start:
    cd brainbox && uv run python -m brainbox api --daemon

bb-daemon-stop:
    cd brainbox && uv run python -m brainbox stop

bb-daemon-status:
    cd brainbox && uv run python -m brainbox status

bb-daemon-restart:
    cd brainbox && uv run python -m brainbox restart

bb-daemon-logs:
    cd brainbox && tail -f "$(uv run python -c 'from brainbox.config import settings; print(settings.config_dir / "logs" / "brainbox.log")')"

bb-docker-build:
    cd brainbox && ./scripts/build.sh

bb-docker-start *ARGS:
    cd brainbox && ./scripts/run.sh {{ ARGS }}

# === Shell Profiler (Go) ===

sp-build:
    cd shell-profiler && go build -o bin/shell-profiler ./cmd/shell-profiler

sp-test:
    cd shell-profiler && go test ./...

sp-lint:
    cd shell-profiler && golangci-lint run

# === Reflex (Plugin) ===

reflex-dev:
    claude --plugin-dir reflex

reflex-langfuse:
    cd docker/langfuse && docker compose up -d

# === OpenSearch (observability stack) ===

opensearch-start:
    cd docker/opensearch && docker compose up -d

opensearch-stop:
    cd docker/opensearch && docker compose down

opensearch-logs:
    cd docker/opensearch && docker compose logs -f

opensearch-status:
    cd docker/opensearch && docker compose ps

# === Tier-0 (local ollama harness) ===

# Launch opencode wired to local ollama + the MCP gateway, scoped to a profile.
# Usage: just tier0-opencode [profile] [-- opencode args...]
tier0-opencode profile="" *args="":
    tier0/opencode-launch.sh {{ if profile != "" { "-p " + profile } else { "" } }} {{args}}

# === Cross-cutting ===

test-all: bb-test sp-test

lint-all: bb-lint sp-lint

# Validate a collection script's output against the timeline entry contract.
# The item shape comes from the SAME schema the Go codegen fetches
# (app/internal/contract/timeline-entry.schema.json, from the pinned
# phantom-contracts tag — run `just app-contract-gen` to refresh it);
# contracts/collection-output.schema.json only adds the array framing scripts
# emit and $refs that fetched schema by $id. ajv-cli reads its data from a file
# (-d), not stdin, so the script output is captured to a temp file first.
# Usage: just validate-output ./path/to/script.sh
# Requires: npm install -g ajv-cli
validate-output script:
    #!/usr/bin/env bash
    set -euo pipefail
    # ajv-cli infers the data format from the file extension, so the temp file
    # must end in .json or it is not parsed as JSON ("data must be array").
    out="$(mktemp).json"
    trap 'rm -f "$out"' EXIT
    {{script}} > "$out"
    ajv validate \
        -s contracts/collection-output.schema.json \
        -r app/internal/contract/timeline-entry.schema.json \
        -d "$out" \
        --errors=text
