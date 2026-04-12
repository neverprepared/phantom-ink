"""Pipeline orchestration: YAML-defined multi-step workflows mixing LLM providers.

Pipelines chain container queries, exec commands, Ollama chat, and HTTP API calls
into DAG-structured workflows with wave-based parallel execution.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import defaultdict
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field

from .config import settings
from .log import get_logger

_log = get_logger()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class StepType(str, Enum):
    CONTAINER_QUERY = "container-query"
    CONTAINER_EXEC = "container-exec"
    OLLAMA_CHAT = "ollama-chat"
    CLAUDE_CHAT = "claude-chat"
    API_CALL = "api-call"
    CLAUDE_SESSION = "claude-session"
    SESSION = "session"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineStep(BaseModel):
    """A single step in a pipeline definition."""

    name: str
    type: StepType
    depends_on: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    timeout: int | None = None  # per-step override (seconds)


class Pipeline(BaseModel):
    """A pipeline definition loaded from YAML."""

    name: str
    description: str = ""
    version: str = "1"
    steps: list[PipelineStep]
    defaults: dict[str, Any] = Field(default_factory=dict)
    source_file: str | None = None  # path it was loaded from
    source_tier: str = "generic"  # "generic", "workspace", or "repo"


class StepResult(BaseModel):
    """Result of executing a single pipeline step."""

    step_name: str
    status: StepStatus = StepStatus.PENDING
    output: Any = None
    error: str | None = None
    started_at: int | None = None  # epoch ms
    finished_at: int | None = None  # epoch ms
    duration_ms: int | None = None


class PipelineRun(BaseModel):
    """A running or completed pipeline execution."""

    id: str
    pipeline_name: str
    status: RunStatus = RunStatus.PENDING
    steps: dict[str, StepResult] = Field(default_factory=dict)
    created_at: int  # epoch ms
    started_at: int | None = None
    finished_at: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)  # user-supplied params
    error: str | None = None


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_pipelines: dict[str, Pipeline] = {}  # name -> Pipeline (generic only, loaded at startup)
_runs: dict[str, PipelineRun] = {}  # run_id -> PipelineRun
_run_tasks: dict[str, asyncio.Task] = {}  # run_id -> asyncio.Task
_run_pipelines: dict[str, Pipeline] = {}  # run_id -> resolved Pipeline (for execution)


def get_run(run_id: str) -> PipelineRun | None:
    return _runs.get(run_id)


def list_runs(
    *,
    pipeline_name: str | None = None,
    status: str | None = None,
) -> list[PipelineRun]:
    result = list(_runs.values())
    if pipeline_name:
        result = [r for r in result if r.pipeline_name == pipeline_name]
    if status:
        result = [r for r in result if r.status == status]
    result.sort(key=lambda r: r.created_at, reverse=True)
    return result


def get_pipeline(name: str) -> Pipeline | None:
    return _pipelines.get(name)


def list_pipelines() -> list[Pipeline]:
    return list(_pipelines.values())


# ---------------------------------------------------------------------------
# YAML loading with three-tier resolution
# ---------------------------------------------------------------------------


def _generic_dirs() -> list[Path]:
    """Return generic (always-available) pipeline directories."""
    dirs: list[Path] = []

    # 1. Built-in: brainbox/pipelines/
    builtin = settings.pipeline.builtin_dir
    if builtin:
        dirs.append(Path(builtin))
    else:
        pkg_root = Path(__file__).resolve().parent.parent.parent
        dirs.append(pkg_root / "pipelines")

    # 2. Config: ~/.config/developer/pipelines/
    config = settings.pipeline.config_dir
    if config:
        dirs.append(Path(config))
    else:
        dirs.append(settings.config_dir / "pipelines")

    return dirs


def _load_dir(directory: Path, tier: str) -> dict[str, Pipeline]:
    """Load all YAML pipelines from a single directory, tagging with tier."""
    loaded: dict[str, Pipeline] = {}
    if not directory.is_dir():
        return loaded
    for f in sorted(directory.glob("*.yaml")):
        try:
            pipeline = _parse_yaml(f, tier=tier)
            loaded[pipeline.name] = pipeline
        except Exception as exc:
            _log.warning(
                "pipeline.load_failed",
                metadata={"file": str(f), "tier": tier, "reason": str(exc)},
            )
    return loaded


def load_pipelines() -> dict[str, Pipeline]:
    """Load generic pipeline definitions (built-in + config).

    Called at startup. Workspace and repo pipelines are resolved
    per-request via resolve_pipelines().
    """
    loaded: dict[str, Pipeline] = {}
    for d in _generic_dirs():
        loaded.update(_load_dir(d, tier="generic"))

    _pipelines.clear()
    _pipelines.update(loaded)
    _log.info("pipeline.loaded", metadata={"count": len(loaded)})
    return loaded


def resolve_pipelines(
    *,
    workspace: str | None = None,
    repo: str | None = None,
) -> dict[str, Pipeline]:
    """Resolve pipelines from all three tiers, merged by priority.

    Resolution order (later overrides earlier by name):
      1. Generic — brainbox/pipelines/ + ~/.config/developer/pipelines/
      2. Workspace — $workspace/pipelines/
      3. Repo — $repo/.brainbox/pipelines/

    Returns a merged dict of pipeline name -> Pipeline.
    """
    # Start with cached generic pipelines
    merged: dict[str, Pipeline] = dict(_pipelines)

    # Layer workspace pipelines
    if workspace:
        ws_dir = Path(workspace) / "pipelines"
        merged.update(_load_dir(ws_dir, tier="workspace"))

    # Layer repo pipelines (highest priority)
    if repo:
        repo_dir = Path(repo) / ".brainbox" / "pipelines"
        merged.update(_load_dir(repo_dir, tier="repo"))

    return merged


def resolve_pipeline(
    name: str,
    *,
    workspace: str | None = None,
    repo: str | None = None,
) -> Pipeline | None:
    """Resolve a single pipeline by name across all tiers."""
    all_pipelines = resolve_pipelines(workspace=workspace, repo=repo)
    return all_pipelines.get(name)


def _parse_yaml(path: Path, tier: str = "generic") -> Pipeline:
    """Parse a single pipeline YAML file into a Pipeline model."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Expected YAML dict, got {type(raw).__name__}")

    steps = []
    for s in raw.get("steps", []):
        steps.append(
            PipelineStep(
                name=s["name"],
                type=StepType(s["type"]),
                depends_on=s.get("depends_on", []),
                config=s.get("config", {}),
                timeout=s.get("timeout"),
            )
        )

    return Pipeline(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        version=str(raw.get("version", "1")),
        steps=steps,
        defaults=raw.get("defaults", {}),
        source_file=str(path),
        source_tier=tier,
    )


# ---------------------------------------------------------------------------
# Topological sort (Kahn's algorithm)
# ---------------------------------------------------------------------------


def topo_sort(steps: list[PipelineStep]) -> list[list[str]]:
    """Sort steps into execution waves respecting dependencies.

    Returns a list of waves, where each wave is a list of step names
    that can execute in parallel. Raises ValueError on cycles.
    """
    name_set = {s.name for s in steps}
    in_degree: dict[str, int] = {s.name: 0 for s in steps}
    dependents: dict[str, list[str]] = defaultdict(list)

    for s in steps:
        for dep in s.depends_on:
            if dep not in name_set:
                raise ValueError(f"Step '{s.name}' depends on unknown step '{dep}'")
            in_degree[s.name] += 1
            dependents[dep].append(s.name)

    waves: list[list[str]] = []
    remaining = dict(in_degree)

    while remaining:
        wave = [name for name, deg in remaining.items() if deg == 0]
        if not wave:
            raise ValueError(f"Cycle detected among steps: {list(remaining.keys())}")

        waves.append(sorted(wave))

        for name in wave:
            del remaining[name]
            for dep_name in dependents.get(name, []):
                if dep_name in remaining:
                    remaining[dep_name] -= 1

    return waves


# ---------------------------------------------------------------------------
# Step executors
# ---------------------------------------------------------------------------


def _interpolate(template: str, context: dict[str, Any]) -> str:
    """Simple variable interpolation: ${step.output} and ${params.key}."""
    result = template
    for key, val in context.items():
        result = result.replace(f"${{{key}}}", str(val))
    return result


def _build_step_context(run: PipelineRun) -> dict[str, Any]:
    """Build interpolation context from completed step outputs and params."""
    ctx: dict[str, Any] = {}
    for step_name, step_result in run.steps.items():
        if step_result.status == StepStatus.COMPLETED and step_result.output is not None:
            ctx[f"steps.{step_name}.output"] = step_result.output
    for key, val in run.params.items():
        ctx[f"params.{key}"] = val
    return ctx


async def _exec_container_query(
    config: dict[str, Any],
    context: dict[str, Any],
    timeout: int,
) -> Any:
    """Execute a container query step via the brainbox API."""
    session = _interpolate(config.get("session", ""), context)
    prompt = _interpolate(config.get("prompt", ""), context)
    working_dir = config.get("working_dir")

    api_key = _load_api_key()
    base_url = config.get("api_url", f"http://localhost:{settings.api_port}")

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        resp = await client.post(
            f"/api/sessions/{session}/query",
            json={"prompt": prompt, "working_dir": working_dir, "timeout": timeout},
            headers={"X-API-Key": api_key},
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("output") or body.get("response", "")


async def _exec_container_exec(
    config: dict[str, Any],
    context: dict[str, Any],
    timeout: int,
) -> Any:
    """Execute a container exec step via the brainbox API."""
    session = _interpolate(config.get("session", ""), context)
    command = _interpolate(config.get("command", ""), context)

    api_key = _load_api_key()
    base_url = config.get("api_url", f"http://localhost:{settings.api_port}")

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        resp = await client.post(
            f"/api/sessions/{session}/exec",
            json={"command": command},
            headers={"X-API-Key": api_key},
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            raise RuntimeError(
                f"exec failed (exit {body.get('exit_code')}): {body.get('output', '')}"
            )
        return body.get("output", "")


async def _exec_ollama_chat(
    config: dict[str, Any],
    context: dict[str, Any],
    timeout: int,
) -> Any:
    """Execute an Ollama chat step via the brainbox Ollama proxy."""
    prompt = _interpolate(config.get("prompt", ""), context)
    model = config.get("model")
    system_prompt = config.get("system_prompt", "")

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": _interpolate(system_prompt, context)})
    messages.append({"role": "user", "content": prompt})

    api_key = _load_api_key()
    base_url = config.get("api_url", f"http://localhost:{settings.api_port}")

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        payload: dict[str, Any] = {"messages": messages}
        if model:
            payload["model"] = model
        resp = await client.post(
            "/api/ollama/chat",
            json=payload,
            headers={"X-API-Key": api_key},
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("message", {}).get("content", "")


async def _exec_claude_chat(
    config: dict[str, Any],
    context: dict[str, Any],
    timeout: int,
) -> Any:
    """Execute a Claude chat step via the Anthropic API."""
    import anthropic

    prompt = _interpolate(config.get("prompt", ""), context)
    model = config.get("model", "claude-sonnet-4-20250514")
    system_prompt = config.get("system_prompt", "")
    max_tokens = config.get("max_tokens", 4096)

    if system_prompt:
        system_prompt = _interpolate(system_prompt, context)

    client = anthropic.AsyncAnthropic()
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt if system_prompt else anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    finally:
        await client.close()


async def _exec_claude_session(
    config: dict[str, Any],
    context: dict[str, Any],
    timeout: int,
) -> Any:
    """Execute a prompt in a temporary Claude Code container session.

    Creates a new brainbox session (using Claude as the LLM), sends a query,
    captures the response, and tears down the session. This uses the user's
    existing Claude credentials injected into the container.

    Config keys:
        prompt: The prompt to send to Claude Code
        system_prompt: Optional — prepended to the prompt for context
        session_prefix: Optional — prefix for the temp session name (default: "pipeline")
        volumes: Optional — list of volume mounts for the session
    """
    prompt = _interpolate(config.get("prompt", ""), context)
    system_prompt = config.get("system_prompt", "")
    if system_prompt:
        system_prompt = _interpolate(system_prompt, context)
        prompt = f"{system_prompt}\n\n{prompt}"

    session_prefix = config.get("session_prefix", "pipeline")
    session_name = f"{session_prefix}-{uuid.uuid4().hex[:8]}"
    volumes = config.get("volumes", [])
    if isinstance(volumes, list):
        volumes = [_interpolate(v, context) for v in volumes]

    api_key = _load_api_key()
    base_url = config.get("api_url", f"http://localhost:{settings.api_port}")

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        # Create session
        create_payload: dict[str, Any] = {"name": session_name}
        if volumes:
            create_payload["volumes"] = volumes

        try:
            resp = await client.post(
                "/api/create",
                json=create_payload,
                headers={"X-API-Key": api_key},
            )
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"Failed to create Claude session '{session_name}': {exc}")

        try:
            # Wait for session to become healthy
            await _wait_for_session(client, session_name, api_key, timeout)

            # Query the session
            resp = await client.post(
                f"/api/sessions/{session_name}/query",
                json={"prompt": prompt, "timeout": timeout},
                headers={"X-API-Key": api_key},
            )
            resp.raise_for_status()
            body = resp.json()
            return body.get("output") or body.get("response", "")
        finally:
            # Clean up: stop and remove the session
            try:
                await client.post(
                    "/api/stop",
                    json={"name": session_name},
                    headers={"X-API-Key": api_key},
                )
                await client.post(
                    "/api/delete",
                    json={"name": session_name},
                    headers={"X-API-Key": api_key},
                )
            except Exception:
                pass  # Best-effort cleanup


async def _exec_session(
    config: dict[str, Any],
    context: dict[str, Any],
    timeout: int,
) -> Any:
    """Execute a prompt in a temporary container or UTM VM session.

    Generic session step — supports any backend (docker, utm) and any LLM
    provider (claude, ollama). Creates a session, sends the query, captures
    the response, and tears down the session.

    Config keys:
        prompt: The prompt to send (required)
        system_prompt: Optional — prepended to the prompt for context
        session_prefix: Prefix for the temp session name (default: "pipeline")
        backend: "docker" (default) or "utm"
        llm_provider: "claude" (default) or "ollama"
        llm_model: Model name override (e.g. "qwen3:8b", "deepseek-r1")
        ollama_host: Ollama server URL override
        guest_os: UTM only — "linux" (default), "macos", or "windows"
        vm_template: UTM only — template VM name
        volumes: List of volume mounts (docker only)
        role: Agent role (default: "developer")
    """
    prompt = _interpolate(config.get("prompt", ""), context)
    system_prompt = config.get("system_prompt", "")
    if system_prompt:
        system_prompt = _interpolate(system_prompt, context)
        prompt = f"{system_prompt}\n\n{prompt}"

    session_prefix = config.get("session_prefix", "pipeline")
    session_name = f"{session_prefix}-{uuid.uuid4().hex[:8]}"

    backend = config.get("backend", "docker")
    llm_provider = config.get("llm_provider", "claude")
    llm_model = config.get("llm_model")
    ollama_host = config.get("ollama_host")
    guest_os = config.get("guest_os", "linux")
    vm_template = config.get("vm_template")
    role = config.get("role", "developer")
    volumes = config.get("volumes", [])
    if isinstance(volumes, list):
        volumes = [_interpolate(v, context) for v in volumes]

    api_key = _load_api_key()
    base_url = config.get("api_url", f"http://localhost:{settings.api_port}")

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        # Build create payload
        create_payload: dict[str, Any] = {
            "name": session_name,
            "backend": backend,
            "llm_provider": llm_provider,
            "role": role,
        }
        if llm_model:
            create_payload["llm_model"] = llm_model
        if ollama_host:
            create_payload["ollama_host"] = ollama_host
        if volumes:
            create_payload["volumes"] = volumes
        if backend == "utm":
            create_payload["guest_os"] = guest_os
            if vm_template:
                create_payload["vm_template"] = vm_template

        # Create session
        try:
            resp = await client.post(
                "/api/create",
                json=create_payload,
                headers={"X-API-Key": api_key},
            )
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to create {backend}/{llm_provider} session '{session_name}': {exc}"
            )

        try:
            # Wait for session to become healthy before querying
            await _wait_for_session(client, session_name, api_key, timeout)

            # Query the session
            resp = await client.post(
                f"/api/sessions/{session_name}/query",
                json={"prompt": prompt, "timeout": timeout},
                headers={"X-API-Key": api_key},
            )
            resp.raise_for_status()
            body = resp.json()
            return body.get("output") or body.get("response", "")
        finally:
            # Tear down
            try:
                await client.post(
                    "/api/stop",
                    json={"name": session_name},
                    headers={"X-API-Key": api_key},
                )
                await client.post(
                    "/api/delete",
                    json={"name": session_name},
                    headers={"X-API-Key": api_key},
                )
            except Exception:
                pass  # Best-effort cleanup


async def _wait_for_session(
    client: httpx.AsyncClient,
    session_name: str,
    api_key: str,
    timeout: int,
) -> None:
    """Poll until Claude Code is ready inside the container.

    The container may be 'active' (Docker running) but Claude Code's tmux
    session takes additional time to initialize. We probe with a lightweight
    exec command and wait until it succeeds.
    """
    max_wait = min(timeout, 120)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait
    _log.info(
        "pipeline.waiting_for_session", metadata={"session": session_name, "max_wait": max_wait}
    )

    tmux_started = False
    while loop.time() < deadline:
        try:
            # First check if container is responsive
            resp = await client.post(
                f"/api/sessions/{session_name}/exec",
                json={"command": "echo alive"},
                headers={"X-API-Key": api_key},
            )
            if resp.status_code != 200:
                await asyncio.sleep(3)
                continue

            # Start tmux + Claude Code if not already running
            # (ttyd only starts Claude when a browser connects — pipelines don't use browsers)
            if not tmux_started:
                await client.post(
                    f"/api/sessions/{session_name}/exec",
                    json={
                        "command": "tmux has-session -t main 2>/dev/null || tmux new-session -d -s main 'claude --dangerously-skip-permissions'"
                    },
                    headers={"X-API-Key": api_key},
                )
                tmux_started = True
                _log.info("pipeline.tmux_started", metadata={"session": session_name})
                await asyncio.sleep(5)  # Give Claude a moment to initialize

            # Check if tmux session is alive
            resp = await client.post(
                f"/api/sessions/{session_name}/exec",
                json={
                    "command": "tmux has-session -t main 2>/dev/null && echo claude_ready || echo waiting"
                },
                headers={"X-API-Key": api_key},
            )
            if resp.status_code == 200:
                body = resp.json()
                if "claude_ready" in body.get("output", ""):
                    _log.info("pipeline.session_ready", metadata={"session": session_name})
                    return
        except Exception:
            pass
        await asyncio.sleep(3)
    raise RuntimeError(f"Session '{session_name}' did not become ready within {max_wait}s")


async def _exec_api_call(
    config: dict[str, Any],
    context: dict[str, Any],
    timeout: int,
) -> Any:
    """Execute an arbitrary HTTP API call step."""
    url = _interpolate(config.get("url", ""), context)
    method = config.get("method", "GET").upper()
    headers = {k: _interpolate(v, context) for k, v in config.get("headers", {}).items()}
    body_raw = config.get("body")
    body = None
    if body_raw:
        if isinstance(body_raw, str):
            body = _interpolate(body_raw, context)
        else:
            body = body_raw

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(method, url, headers=headers, json=body if body else None)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return resp.text


_EXECUTORS = {
    StepType.CONTAINER_QUERY: _exec_container_query,
    StepType.CONTAINER_EXEC: _exec_container_exec,
    StepType.OLLAMA_CHAT: _exec_ollama_chat,
    StepType.CLAUDE_CHAT: _exec_claude_chat,
    StepType.CLAUDE_SESSION: _exec_claude_session,
    StepType.SESSION: _exec_session,
    StepType.API_CALL: _exec_api_call,
}


def _load_api_key() -> str:
    """Load the brainbox API key."""
    try:
        return settings.api_key_file.read_text().strip()
    except FileNotFoundError:
        return ""


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------


async def start_run(
    pipeline_name: str,
    params: dict[str, Any] | None = None,
    *,
    workspace: str | None = None,
    repo: str | None = None,
) -> PipelineRun:
    """Start a new pipeline run. Returns immediately; execution is async."""
    pipeline = resolve_pipeline(pipeline_name, workspace=workspace, repo=repo)
    if not pipeline:
        raise ValueError(f"Pipeline '{pipeline_name}' not found")

    run_id = str(uuid.uuid4())
    now = _now_ms()

    run = PipelineRun(
        id=run_id,
        pipeline_name=pipeline_name,
        status=RunStatus.PENDING,
        created_at=now,
        params=params or {},
    )

    # Initialize step results
    for step in pipeline.steps:
        run.steps[step.name] = StepResult(step_name=step.name)

    _runs[run_id] = run
    _run_pipelines[run_id] = pipeline  # cache resolved pipeline for execution

    # Launch execution in background
    task = asyncio.create_task(_execute_run(run_id))
    _run_tasks[run_id] = task

    _log.info(
        "pipeline.run_started",
        metadata={"run_id": run_id, "pipeline": pipeline_name},
    )
    return run


async def cancel_run(run_id: str) -> PipelineRun:
    """Cancel a running pipeline."""
    run = _runs.get(run_id)
    if not run:
        raise ValueError(f"Pipeline run '{run_id}' not found")
    if run.status not in (RunStatus.PENDING, RunStatus.RUNNING):
        raise ValueError(f"Run '{run_id}' cannot be cancelled (status: {run.status})")

    run.status = RunStatus.CANCELLED
    run.finished_at = _now_ms()

    # Cancel the asyncio task
    task = _run_tasks.pop(run_id, None)
    if task and not task.done():
        task.cancel()

    # Mark pending/running steps as skipped
    for sr in run.steps.values():
        if sr.status in (StepStatus.PENDING, StepStatus.RUNNING):
            sr.status = StepStatus.SKIPPED

    _log.info("pipeline.run_cancelled", metadata={"run_id": run_id})
    return run


async def _execute_run(run_id: str) -> None:
    """Execute a pipeline run wave by wave."""
    run = _runs.get(run_id)
    if not run:
        return

    pipeline = _run_pipelines.get(run_id)
    if not pipeline:
        run.status = RunStatus.FAILED
        run.error = f"Pipeline definition not found for run '{run_id}'"
        run.finished_at = _now_ms()
        return

    run.status = RunStatus.RUNNING
    run.started_at = _now_ms()

    try:
        waves = topo_sort(pipeline.steps)
    except ValueError as exc:
        run.status = RunStatus.FAILED
        run.error = str(exc)
        run.finished_at = _now_ms()
        return

    step_map = {s.name: s for s in pipeline.steps}

    try:
        for wave in waves:
            if run.status == RunStatus.CANCELLED:
                break

            # Limit concurrency per wave
            sem = asyncio.Semaphore(settings.pipeline.max_concurrent_steps)
            tasks = []
            for step_name in wave:
                step = step_map[step_name]
                tasks.append(_run_step(run, step, sem))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Check for failures
            for step_name, result in zip(wave, results):
                if isinstance(result, Exception):
                    sr = run.steps[step_name]
                    sr.status = StepStatus.FAILED
                    sr.error = str(result)
                    sr.finished_at = _now_ms()

            # If any step in this wave failed, fail the whole run
            if any(run.steps[s].status == StepStatus.FAILED for s in wave):
                run.status = RunStatus.FAILED
                run.error = "One or more steps failed"
                run.finished_at = _now_ms()
                # Skip remaining steps
                for sr in run.steps.values():
                    if sr.status == StepStatus.PENDING:
                        sr.status = StepStatus.SKIPPED
                return

        if run.status != RunStatus.CANCELLED:
            run.status = RunStatus.COMPLETED
            run.finished_at = _now_ms()
            _log.info("pipeline.run_completed", metadata={"run_id": run_id})

    except asyncio.CancelledError:
        run.status = RunStatus.CANCELLED
        run.finished_at = _now_ms()
    except Exception as exc:
        run.status = RunStatus.FAILED
        run.error = str(exc)
        run.finished_at = _now_ms()
        _log.error("pipeline.run_failed", metadata={"run_id": run_id, "reason": str(exc)})
    finally:
        _run_tasks.pop(run_id, None)
        _run_pipelines.pop(run_id, None)


async def _run_step(
    run: PipelineRun,
    step: PipelineStep,
    sem: asyncio.Semaphore,
) -> None:
    """Execute a single step within a pipeline run."""
    sr = run.steps[step.name]

    async with sem:
        sr.status = StepStatus.RUNNING
        sr.started_at = _now_ms()

        timeout = step.timeout or settings.pipeline.default_timeout
        context = _build_step_context(run)

        executor = _EXECUTORS.get(step.type)
        if not executor:
            sr.status = StepStatus.FAILED
            sr.error = f"Unknown step type: {step.type}"
            sr.finished_at = _now_ms()
            return

        # Merge pipeline defaults with step config (step wins)
        merged_config = {**run.params.get("_defaults", {}), **step.config}

        try:
            result = await asyncio.wait_for(
                executor(merged_config, context, timeout),
                timeout=timeout,
            )
            sr.status = StepStatus.COMPLETED
            sr.output = result
        except asyncio.TimeoutError:
            sr.status = StepStatus.FAILED
            sr.error = f"Step timed out after {timeout}s"
        except Exception as exc:
            sr.status = StepStatus.FAILED
            sr.error = str(exc)

        sr.finished_at = _now_ms()
        if sr.started_at:
            sr.duration_ms = sr.finished_at - sr.started_at


# ---------------------------------------------------------------------------
# State serialization (for hub flush/restore)
# ---------------------------------------------------------------------------


def get_state() -> dict:
    """Return serializable pipeline state for hub persistence."""
    return {
        "runs": [
            (rid, r.model_dump())
            for rid, r in _runs.items()
            if r.status in (RunStatus.RUNNING, RunStatus.PENDING)
        ],
    }


def restore_state(state: dict | None) -> None:
    """Restore pipeline state from hub persistence.

    Note: running pipelines cannot be resumed after restart — they are marked failed.
    """
    if not state:
        return
    for rid, data in state.get("runs", []):
        run = PipelineRun(**data)
        # Can't resume async execution — mark as failed
        if run.status in (RunStatus.RUNNING, RunStatus.PENDING):
            run.status = RunStatus.FAILED
            run.error = "Pipeline interrupted by API restart"
            run.finished_at = _now_ms()
            for sr in run.steps.values():
                if sr.status in (StepStatus.RUNNING, StepStatus.PENDING):
                    sr.status = StepStatus.SKIPPED
        _runs[rid] = run


def _now_ms() -> int:
    return int(time.time() * 1000)
