"""Playbooks — markdown checklist execution engine.

A playbook is a markdown document containing `- [ ]` checklist items.
Each item becomes a task dispatched sequentially to a fresh ephemeral
worker session (clean context, no bleed between steps). Progress is
tracked in module state and broadcast via the event listener pattern.

Built-in playbooks are loaded from ``brainbox/playbooks/*.md`` on startup.
They appear alongside user-created playbooks but cannot be deleted.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Callable

import httpx

from .config import settings
from .log import get_logger
from .models import Playbook, PlaybookTask

log = get_logger()

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_playbooks: dict[str, Playbook] = {}
_run_tasks: dict[str, asyncio.Task] = {}  # playbook_id -> active asyncio.Task
_listeners: list[Callable] = []

TASK_PATTERN = re.compile(r"^[ \t]*-[ \t]\[[ ]\][ \t](.+)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


def _emit(event: str, data: object) -> None:
    for fn in _listeners:
        try:
            fn(event, data)
        except Exception as exc:
            log.warning("playbooks.event_listener_error", metadata={"event": event, "reason": str(exc)})


def on_event(fn: Callable) -> None:
    """Register a listener for playbook events (used to bridge to SSE)."""
    _listeners.append(fn)


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------


def _parse_tasks(markdown: str) -> list[PlaybookTask]:
    return [
        PlaybookTask(index=i, content=m.group(1).strip())
        for i, m in enumerate(TASK_PATTERN.finditer(markdown))
    ]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_playbook(name: str, markdown: str, workspace_profile: str = "global", runner: str | None = None) -> Playbook:
    pb = Playbook(name=name, markdown=markdown, tasks=_parse_tasks(markdown), workspace_profile=workspace_profile, runner=runner)
    _playbooks[pb.id] = pb
    _emit("playbook.created", pb)
    log.info("playbook.created", metadata={"id": pb.id, "name": name, "tasks": len(pb.tasks)})
    return pb


def get_playbook(playbook_id: str) -> Playbook | None:
    return _playbooks.get(playbook_id)


def list_playbooks(profile: str | None = None) -> list[Playbook]:
    """Return playbooks for a profile plus all global ones. None = return all."""
    if profile is None:
        return list(_playbooks.values())
    return [
        pb for pb in _playbooks.values()
        if pb.workspace_profile == profile or pb.workspace_profile == "global"
    ]


_UNSET = object()


def update_playbook(
    playbook_id: str,
    *,
    name: str | None = None,
    markdown: str | None = None,
    runner: object = _UNSET,
) -> Playbook:
    pb = _playbooks.get(playbook_id)
    if pb is None:
        raise ValueError(f"Playbook '{playbook_id}' not found")
    if pb.status == "running":
        raise ValueError("Cannot update a running playbook")
    if name is not None:
        pb.name = name
    if markdown is not None:
        pb.markdown = markdown
        pb.tasks = _parse_tasks(markdown)
    if runner is not _UNSET:
        pb.runner = runner  # type: ignore[assignment]
    _emit("playbook.updated", pb)
    log.info("playbook.updated", metadata={"id": playbook_id})
    return pb


def delete_playbook(playbook_id: str) -> None:
    if playbook_id not in _playbooks:
        raise ValueError(f"Playbook '{playbook_id}' not found")
    cancel_playbook(playbook_id)
    del _playbooks[playbook_id]
    log.info("playbook.deleted", metadata={"id": playbook_id})


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


async def run_playbook(
    playbook_id: str,
    *,
    workspace_profile: str | None = None,
    runner: str | None = None,
) -> Playbook:
    """Start sequential execution; returns immediately. Runs in background.

    *workspace_profile* overrides the playbook's saved profile for this run.
    *runner* overrides the playbook's saved runner for this run.
    """
    pb = _playbooks.get(playbook_id)
    if not pb:
        raise ValueError(f"Playbook '{playbook_id}' not found")
    if pb.status == "running":
        raise ValueError(f"Playbook '{playbook_id}' is already running")

    run_profile = workspace_profile or pb.workspace_profile
    run_runner = runner if runner is not None else pb.runner

    for task in pb.tasks:
        task.status = "pending"
        task.output = None
        task.error = None
        task.session_name = None
        task.started_at = None
        task.finished_at = None

    task = asyncio.create_task(_execute(playbook_id, run_profile=run_profile, run_runner=run_runner))
    _run_tasks[playbook_id] = task
    return pb


def cancel_playbook(playbook_id: str) -> None:
    task = _run_tasks.get(playbook_id)
    if task and not task.done():
        task.cancel()
    pb = _playbooks.get(playbook_id)
    if pb and pb.status == "running":
        pb.status = "cancelled"
        _emit("playbook.cancelled", pb)
        log.info("playbook.cancelled", metadata={"id": playbook_id})


async def _execute(playbook_id: str, *, run_profile: str = "global", run_runner: str | None = None) -> None:
    from .models import _now_ms
    pb = _playbooks[playbook_id]
    pb.status = "running"
    pb.started_at = _now_ms()
    _emit("playbook.started", pb)
    log.info("playbook.started", metadata={"id": playbook_id, "tasks": len(pb.tasks), "profile": run_profile, "runner": run_runner})

    try:
        for task in pb.tasks:
            if pb.status == "cancelled":
                break
            await _run_task(pb, task, run_profile=run_profile, run_runner=run_runner)
            if task.status == "failed":
                pb.status = "failed"
                pb.finished_at = _now_ms()
                _emit("playbook.failed", pb)
                log.warning("playbook.failed", metadata={"id": playbook_id, "task": task.index})
                return

        if pb.status != "cancelled":
            pb.status = "completed"
        pb.finished_at = _now_ms()
        _emit("playbook.completed", pb)
        log.info("playbook.completed", metadata={"id": playbook_id, "status": pb.status})

    except asyncio.CancelledError:
        pb.status = "cancelled"
        pb.finished_at = _now_ms()
        _emit("playbook.cancelled", pb)
    except Exception as exc:
        pb.status = "failed"
        pb.finished_at = _now_ms()
        _emit("playbook.failed", pb)
        log.warning("playbook.execute_error", metadata={"id": playbook_id, "reason": str(exc)})
    finally:
        _run_tasks.pop(playbook_id, None)


async def _run_task(pb: Playbook, task: PlaybookTask, *, run_profile: str = "global", run_runner: str | None = None) -> None:
    from .models import _now_ms
    session_name = f"pb-{pb.id[:6]}-t{task.index}"
    task.status = "running"
    task.session_name = session_name
    task.started_at = _now_ms()
    _emit("playbook.task_started", {"playbook_id": pb.id, "task_id": task.id})
    log.info("playbook.task_started", metadata={"playbook": pb.id, "task": task.index, "session": session_name, "runner": run_runner})

    api_key = _load_api_key()
    base_url = f"http://localhost:{settings.api_port}"

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=600) as client:
            headers = {"X-API-Key": api_key}

            # Create fresh worker session with the runtime profile context
            create_body: dict = {"name": session_name}
            if run_profile and run_profile != "global":
                create_body["workspace_profile"] = run_profile
                # Resolve workspace_home from profile name so mounts are correct
                workspace_home = _resolve_workspace_home(run_profile)
                if workspace_home:
                    create_body["workspace_home"] = workspace_home
            if run_runner:
                create_body["runner"] = run_runner
            resp = await client.post("/api/create", json=create_body, headers=headers)
            resp.raise_for_status()

            try:
                # Wait for Claude Code to be ready inside the container
                await _wait_for_session(client, session_name, api_key)

                # Send the task prompt
                resp = await client.post(
                    f"/api/sessions/{session_name}/query",
                    json={"prompt": task.content, "timeout": 300},
                    headers=headers,
                )
                resp.raise_for_status()
                body = resp.json()
                task.output = body.get("output") or body.get("response", "")
                task.status = "completed"

            finally:
                # Clean up ephemeral session
                try:
                    await client.post("/api/stop", json={"name": session_name}, headers=headers)
                    await client.post("/api/delete", json={"name": session_name}, headers=headers)
                except Exception as cleanup_exc:
                    log.warning("playbook.session_cleanup_failed", metadata={"session": session_name, "reason": str(cleanup_exc)})

    except asyncio.CancelledError:
        task.status = "failed"
        task.error = "Cancelled"
        raise
    except Exception as exc:
        task.status = "failed"
        task.error = str(exc)
        log.warning("playbook.task_failed", metadata={"playbook": pb.id, "task": task.index, "reason": str(exc)})
    finally:
        from .models import _now_ms as _ms
        task.finished_at = _ms()
        _emit("playbook.task_done", {"playbook_id": pb.id, "task_id": task.id, "status": task.status})


async def _wait_for_session(client: httpx.AsyncClient, session_name: str, api_key: str, max_wait: int = 120) -> None:
    """Poll until Claude Code's tmux session is ready inside the container."""
    headers = {"X-API-Key": api_key}
    deadline = asyncio.get_event_loop().time() + max_wait
    tmux_started = False

    while asyncio.get_event_loop().time() < deadline:
        try:
            resp = await client.post(
                f"/api/sessions/{session_name}/exec",
                json={"command": "echo alive"},
                headers=headers,
            )
            if resp.status_code != 200:
                await asyncio.sleep(3)
                continue

            if not tmux_started:
                await client.post(
                    f"/api/sessions/{session_name}/exec",
                    json={"command": "tmux has-session -t main 2>/dev/null || tmux new-session -d -s main 'claude --dangerously-skip-permissions'"},
                    headers=headers,
                )
                tmux_started = True
                await asyncio.sleep(5)

            resp = await client.post(
                f"/api/sessions/{session_name}/exec",
                json={"command": "tmux has-session -t main 2>/dev/null && echo claude_ready || echo waiting"},
                headers=headers,
            )
            if resp.status_code == 200:
                output = resp.json().get("output", "")
                if "claude_ready" in output:
                    return

        except Exception:
            pass

        await asyncio.sleep(3)

    raise TimeoutError(f"Session '{session_name}' did not become ready within {max_wait}s")


def _load_api_key() -> str:
    try:
        return settings.api_key_file.read_text().strip()
    except FileNotFoundError:
        return ""


def _resolve_workspace_home(profile_name: str) -> str | None:
    """Look up a profile's workspace_home from the volatile cache or profiles dir.

    Shell-profiler writes a cache at $TMPDIR/sp-profiles/{name}/.env with
    WORKSPACE_HOME=... on every `cd`. We read that, or fall back to the
    conventional profiles directory layout.
    """
    import os
    import tempfile

    # Try volatile cache first (written by shell-profiler on cd)
    cache_env = Path(tempfile.gettempdir()) / "sp-profiles" / profile_name / ".env"
    if cache_env.is_file():
        for line in cache_env.read_text().splitlines():
            line = line.strip()
            if line.startswith("WORKSPACE_HOME="):
                return line.split("=", 1)[1].strip().strip("'\"")

    # Fall back to profiles directory convention: ~/workspaces/profiles/<name>
    home = os.environ.get("HOME", str(Path.home()))
    candidate = Path(home) / "workspaces" / "profiles" / profile_name
    if candidate.is_dir():
        return str(candidate)

    return None


# ---------------------------------------------------------------------------
# Hub state persistence
# ---------------------------------------------------------------------------


def get_state() -> dict:
    return {
        "playbooks": [(pb.id, pb.model_dump()) for pb in _playbooks.values()],
    }


def restore_state(state: dict | None) -> None:
    if not state:
        return
    for pb_id, pb_data in state.get("playbooks", []):
        try:
            # Reset any in-flight status from before restart
            if pb_data.get("status") == "running":
                pb_data["status"] = "idle"
            _playbooks[pb_id] = Playbook(**pb_data)
        except Exception as exc:
            log.warning("playbooks.restore_failed", metadata={"id": pb_id, "reason": str(exc)})
    log.info("playbooks.state_restored", metadata={"count": len(_playbooks)})
