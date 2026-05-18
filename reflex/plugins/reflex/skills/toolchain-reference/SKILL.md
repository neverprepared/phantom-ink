---
name: toolchain-reference
description: Language-specific build, test, and lint commands for Go, Python, and TypeScript projects. Use when starting development work, running tests, or verifying changes.
---

# Toolchain Reference

Detect the project language and use the appropriate toolchain. When a project has its own CLAUDE.md with specific commands, defer to that.

## Go

```bash
go build ./...                  # Build
go test ./...                   # Test all
go test ./... -run TestName     # Single test
go vet ./...                    # Lint
golangci-lint run               # Extended lint (if available)
```

## Python

```bash
# Prefer uv if available, fall back to pip
uv run pytest                   # Test all
uv run pytest -k "test_name"   # Single test
uv run ruff check .            # Lint
uv run ruff format --check .   # Format check
uv run mypy .                  # Type check (if configured)
```

## TypeScript

```bash
# Prefer bun if available, fall back to npm
bun run build                   # Build
bun run test                    # Test all
bun run test -- -t "test name" # Single test
bun run lint                    # Lint
bun run typecheck               # Type check
```

## General

- Check for `Makefile`, `justfile`, `Taskfile`, or `package.json` scripts first — use project-defined commands over defaults.
- If a lockfile exists (`go.sum`, `uv.lock`, `bun.lockb`, `package-lock.json`), use the matching package manager.
