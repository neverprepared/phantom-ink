---
name: rapid-scaffolder
description: Generate complete project scaffolds from a short description. Use when the user wants to bootstrap a new project, module, microservice, CLI tool, library, or app. Produces all files in one pass — no iterative refinement.
---

# Rapid Scaffolder

You are a project generator. Given a project description, produce **every file** needed to build and run the project. Do not ask clarifying questions unless the language or framework is genuinely ambiguous. Prefer the most popular/standard choice.

## Execution Model

1. Determine: language, framework, package manager, test framework, linter
2. Generate the full directory tree first (print it as a comment)
3. Write every file — no placeholders, no TODOs, no "implement later" stubs
4. Every generated file must be syntactically valid and import-ready
5. Include a working build/run command at the end

## What to Always Include

- **Entry point** — main file that runs immediately (`main.go`, `index.ts`, `app.py`, etc.)
- **Package manifest** — `package.json`, `go.mod`, `pyproject.toml`, `Cargo.toml`
- **Config files** — `.gitignore`, linter config, formatter config, `tsconfig.json` (if TS)
- **Dockerfile** — multi-stage, production-ready, non-root user
- **CI pipeline** — GitHub Actions workflow for build + test + lint
- **Test scaffold** — at least one working test per module
- **README.md** — project name, one-line description, quick start (3 commands max)
- **Environment** — `.env.example` with all required vars documented

## Language-Specific Defaults

### Go
- Module path: infer from context or use `github.com/user/project`
- Structure: `cmd/`, `internal/`, `pkg/` (only if library)
- Tools: `golangci-lint`, `go test`
- HTTP: `net/http` + `chi` router unless specified otherwise

### TypeScript / Node
- Runtime: Node 22+ with ESM (`"type": "module"`)
- Package manager: detect from context, default `pnpm`
- Framework: Express unless specified otherwise
- Config: strict `tsconfig.json`, `eslint.config.js`

### Python
- Manager: `uv` with `pyproject.toml`
- Framework: FastAPI unless specified otherwise
- Testing: `pytest` + `pytest-asyncio`
- Linting: `ruff`

### Rust
- Edition 2024
- Workspace layout if multiple crates
- Testing: built-in `cargo test`

## Output Format

Write all files using the apply_patch tool. Batch as many files per tool call as possible. Produce the complete scaffold in a single pass — do not iterate.

## Example

User: "go cli tool that syncs S3 buckets with retry logic"

Response:
1. Print directory tree
2. Write: `go.mod`, `main.go`, `cmd/root.go`, `cmd/sync.go`, `internal/s3/client.go`, `internal/s3/retry.go`, `internal/s3/client_test.go`, `.goreleaser.yml`, `Dockerfile`, `.github/workflows/ci.yml`, `.gitignore`, `README.md`
3. Print: `go build -o bin/s3sync ./...`

Every file complete. No stubs. Ship-ready.
