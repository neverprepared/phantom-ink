# Root Justfile — phantom-ink platform monorepo

default:
    @just --list --unsorted

# === App (Wails desktop, Go + Svelte) ===

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

# === Runner (Swift macOS menu-bar app) ===

# Build BrainboxRunner.app and package it into a distributable DMG
runner-dmg output="dist":
    app/runner/scripts/make-dmg.sh {{output}}

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

# === Cross-cutting ===

test-all: bb-test sp-test

lint-all: bb-lint sp-lint

# Validate a collection script's output against the timeline entry contract.
# Usage: just validate-output ./path/to/script.sh
# Requires: npm install -g ajv-cli
validate-output script:
    {{script}} | ajv validate -s contracts/timeline-entry.schema.json --errors=text
