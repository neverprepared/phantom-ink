# Session Handoff

**Generated:** 2026-04-05T02:15:00Z
**Project:** ink-bunny (monorepo: brainbox, reflex, shell-profiler, docs)

## What We Were Working On

Making Ollama a first-class LLM provider in brainbox, then building toward autonomous pipeline orchestration where containers chain work through the brainbox API — mixing private (Ollama) and public (Claude) LLM providers per step with no human intervention.

## Key Decisions Made

- **Ollama client in brainbox**: Created `brainbox/src/brainbox/ollama.py` (singleton httpx client) with 4 API proxy endpoints (`/api/ollama/chat`, `/models`, `/pull`, `/health`). Released as brainbox v0.14.0, reflex v1.26.0.
- **No external frameworks**: Evaluated LangChain/LangGraph, CrewAI, AutoGen — all assume in-process agents. Our container-based microservices architecture is better served by a lightweight Python pipeline runner on top of the existing brainbox REST API.
- **Pipeline storage**: File-based YAML definitions loaded from 3 directories in priority order: built-in (`brainbox/pipelines/`), config (`~/.config/developer/pipelines/`), workspace override (`$BRAINBOX_PIPELINES_DIR`).
- **API runner first**: Build the pipeline engine + API endpoints. Orchestrator container is a follow-up (thin API client).
- **pdf-extract script**: Created `$WORKSPACE_HOME/bin/pdf-extract` with macOS Vision OCR, checkbox normalization for scanned forms, and common OCR artifact correction.
- **brew-install-private script**: Created `$WORKSPACE_HOME/bin/brew-install-private` to install from the private repo using local tarballs + `file://` URL patching.
- **Ollama host config**: brainbox daemon needs `CL_OLLAMA__HOST=http://localhost:11434` when running on the host (default `host.docker.internal` is for containers). Containers reach Ollama via `host.docker.internal:9999/api/ollama/chat` (brainbox proxy).
- **Model sizing**: qwen3:8b is the sweet spot for accuracy on document analysis. 3b/4b models misread OCR checkbox artifacts. deepseek-r1 doesn't support tool calling.

## Files Modified

| File | Change |
|------|--------|
| `brainbox/src/brainbox/ollama.py` | **Created** — Ollama client (health, chat, list_models, pull_model) |
| `brainbox/src/brainbox/api.py` | Added 4 Ollama proxy endpoints |
| `brainbox/src/brainbox/models_api.py` | Added OllamaChatRequest, OllamaPullRequest |
| `brainbox/src/brainbox/config.py` | OllamaSettings already existed |
| `brainbox/tests/test_ollama_client.py` | **Created** — 23 tests for client module |
| `brainbox/tests/test_ollama_api.py` | **Created** — 12 tests for API endpoints |
| `brainbox/tests/test_cosign.py` | Ruff format fix (cosmetic) |
| `brainbox/pyproject.toml` | Version bump to 0.14.0 |
| `reflex/plugins/reflex/scripts/summarize.py` | Added BrainboxProvider, auto-detect when BRAINBOX_HUB_URL set |
| `Formula/brainbox.rb` | Updated for v0.14.0 (release URL + SHA) |
| `Formula/reflex.rb` | Updated for v1.26.0 (release URL + SHA) |
| `CLAUDE.md` | Updated Distribution section to use brew-install-private |
| `$WORKSPACE_HOME/bin/pdf-extract` | **Created** — PDF text extraction with OCR + checkbox normalization |
| `$WORKSPACE_HOME/bin/brew-install-private` | **Created** — Private repo Homebrew installer |

## Current State

**Completed:**
- Ollama client + API proxy: shipped, released (brainbox v0.14.0, reflex v1.26.0), installed via Homebrew
- All 334 brainbox tests pass
- pdf-extract script: working with OCR, checkbox normalization, and artifact correction
- brew-install-private: working for both brainbox and reflex
- Proved container chaining: Container A → Ollama proxy → analysis → creates Container B → formats report → writes to shared mount (full chain in ~60 seconds with qwen3:8b)

**Not started:**
- Pipeline orchestration system (plan approved, tasks created, implementation pending)

## Outstanding Tasks

- [ ] Add pyyaml + anthropic dependencies to pyproject.toml, add PipelineSettings to config.py
- [ ] Create `brainbox/src/brainbox/pipeline.py` (~400 lines: models, YAML parsing, topo sort, 4 step executors, wave-based execution engine, state management)
- [ ] Add 7 pipeline API endpoints to api.py + request models to models_api.py
- [ ] Integrate pipeline state with hub flush/restore cycle
- [ ] Create `brainbox/pipelines/lease-analysis.yaml` example pipeline
- [ ] Write test_pipeline.py and test_pipeline_api.py
- [ ] Format, test, verify all passes

## Important Context

- **Brainbox daemon env vars**: When running locally, start with `CL_OLLAMA__HOST=http://localhost:11434 CL_PROFILE__MOUNT_REFLEX=false just bb-daemon-start`. The reflex mount path (`/opt/homebrew/opt/reflex/share/reflex`) isn't shared with Docker Desktop.
- **Docker image**: No GHCR image available (private repo, 403). Built locally with `just bb-docker-build` — the base image builds but `Dockerfile.developer` is missing. Tagged `brainbox-base:latest` as `ghcr.io/neverprepared/brainbox:latest` to make the API work.
- **OCR checkbox artifacts**: Scanned PDF checkboxes OCR as `E`, `2`, `•`, `D`, etc. The pdf-extract script normalizes these to `[X]`/`[ ]`. Without this, LLMs consistently misread which option is checked.
- **Container Claude Code + Ollama is slow**: Claude Code makes 10-15 sequential LLM calls per task (tool planning, file reads, reasoning). With small Ollama models, each takes 5-10 seconds. Direct Ollama proxy calls (single-shot) are 4-8 seconds total. The `exec` approach (bypassing Claude Code's agentic loop) is the fast path.
- **Pipeline plan file**: Full approved plan at `.claude/plans/shimmering-wiggling-lerdorf.md`
- **Existing patterns to follow**: `langfuse_client.py` for client modules, `router.py` for state management, `api.py` LangFuse section for endpoint structure

## How to Continue

1. Read the approved plan: `cat .claude/plans/shimmering-wiggling-lerdorf.md`
2. Check existing task list for the 7 outstanding pipeline tasks
3. Start with task 7: Add deps + PipelineSettings config (quick, unblocks everything)
4. Then task 8: Create `pipeline.py` core module (the bulk of the work)
5. Follow the implementation sequence in the plan: models → templates → topo sort → step executors → execution engine → API → hub integration → tests
6. Run `cd brainbox && uv run ruff format src/ tests/ && just bb-test` after each major piece
