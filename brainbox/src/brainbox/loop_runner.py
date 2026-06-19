"""Loop runner — markdown-format edition.

Drives a parsed ``LoopMarkdown`` end-to-end:

    start_loop(loop, envelope) → LoopInstance with iteration 1 enqueued
    advance_loop(loop_id, envelope) → judge decides; either enqueue
        iteration N+1, terminate CONVERGED, escalate (STOPPED_BY_CONDITION),
        or MAX_ITER.

The parent task is RUNNING throughout the loop's life — it represents
the loop in the existing Tasks panel and never gets dispatched
(scheduler only dispatches PENDING). Each iteration is a child task.

Termination decisions per iteration, in order:

  1. Judge.evaluate_stop fires            → CONVERGED, parent COMPLETED
  2. Judge.evaluate_escalation fires      → STOPPED_BY_CONDITION (judge says page),
                                            parent FAILED
  3. iteration >= max_iterations          → MAX_ITER, parent FAILED
                                            (also covered by evaluate_escalation
                                             objectively; redundant guard kept for
                                             belt-and-braces)
  4. budget_usd exceeded                  → STOPPED_BY_CONDITION, parent FAILED
                                            (also surfaced via evaluate_escalation)
  5. otherwise                            → enqueue iteration N+1

Thrash detection is now a prose clause in the template's "# When to
stop" section, evaluated by the judge — no more separate metric series.
``cost_history`` replaces ``metric_history``.

The runner does NOT call the Anthropic API directly anywhere. The judge
itself dispatches its own brainbox sessions per CLAUDE.md.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from .log import get_logger
from .loop_judge import evaluate_escalation, evaluate_stop
from .loop_md import LoopMarkdown
from .loop_mermaid import render as render_mermaid
from .loop_template import content_hash
from .loops import (
    HandoffEnvelope,
    LoopInstance,
    LoopStatus,
)
from .models import Task, TaskStatus
from .utils import now_ms as _now_ms

log = get_logger()


# ---------------------------------------------------------------------------
# State — in-memory cache backed by persistence in store.py
# ---------------------------------------------------------------------------

_instances: dict[str, LoopInstance] = {}
_child_to_loop: dict[str, str] = {}
# Per-loop parsed template kept in memory so we don't re-parse the
# frozen text on every advance call.
_parsed: dict[str, LoopMarkdown] = {}


def get_instance(loop_id: str) -> LoopInstance | None:
    return _instances.get(loop_id)


def list_instances() -> list[LoopInstance]:
    return list(_instances.values())


def loop_id_for_child(child_task_id: str) -> str | None:
    return _child_to_loop.get(child_task_id)


def _parsed_for(inst: LoopInstance) -> LoopMarkdown:
    """Return the parsed LoopMarkdown for an instance, caching the
    parse. The frozen ``template_text`` is the source of truth."""
    if inst.id in _parsed:
        return _parsed[inst.id]
    from .loop_md import parse

    loop = parse(inst.template_text)
    _parsed[inst.id] = loop
    return loop


# ---------------------------------------------------------------------------
# start_loop
# ---------------------------------------------------------------------------


async def start_loop(
    loop: LoopMarkdown,
    initial_envelope: HandoffEnvelope,
    *,
    workspace_profile: str | None = None,
    workspace_home: str | None = None,
) -> LoopInstance:
    """Create a LoopInstance, its parent task, and enqueue iteration 1.

    Returns the instance with ``status == RUNNING`` and
    ``current_child_id`` set to the iteration-1 child task. Raises
    ValueError when a required artifact_ref is missing from the initial
    envelope.
    """
    from . import router

    missing_refs = [
        ref.name
        for ref in loop.required_refs
        if ref.required and ref.name not in (initial_envelope.artifact_refs or {})
    ]
    if missing_refs:
        raise ValueError(
            f"missing required artifact_refs: {', '.join(missing_refs)}"
        )

    if not loop.agent.strip():
        raise ValueError(f"loop {loop.name!r}: agent not set and name is empty")

    # Fail fast if the template names an agent that isn't registered.
    # Without this guard we'd create the parent task + LoopInstance,
    # then router.submit_task would 400 on the iteration child, leaving
    # an orphan parent in RUNNING state.
    from .registry import get_agent

    if get_agent(loop.agent) is None:
        from .registry import list_agents

        known = ", ".join(sorted(a.name for a in list_agents())) or "(none registered)"
        raise ValueError(
            f"Agent {loop.agent!r} not registered. "
            f"Edit the template's `agent:` frontmatter — known agents: {known}."
        )

    loop_id = str(uuid.uuid4())
    now = _now_ms()

    initial_envelope = initial_envelope.model_copy(update={
        "loop_id": loop_id,
        "iteration": 0,
    })

    parent_id = str(uuid.uuid4())
    parent = Task(
        id=parent_id,
        description=f"loop: {loop.name}",
        agent_name=f"loop:{loop.name}",
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
        job_id=parent_id,
        workspace_profile=workspace_profile,
        workspace_home=workspace_home,
    )
    router._tasks[parent_id] = parent

    inst = LoopInstance(
        id=loop_id,
        template_name=loop.name,
        template_text=loop.raw,
        template_hash=content_hash(loop.raw),
        mermaid=render_mermaid(loop),
        parent_task_id=parent_id,
        status=LoopStatus.RUNNING,
        iteration=0,
        envelope=initial_envelope,
        workspace_profile=workspace_profile,
        created_at=now,
        updated_at=now,
    )
    _instances[loop_id] = inst
    _parsed[loop_id] = loop

    child_id = await _enqueue_iteration(
        inst,
        envelope=initial_envelope,
        iteration=1,
        workspace_home=workspace_home,
    )
    inst.iteration = 1
    inst.current_child_id = child_id
    inst.updated_at = _now_ms()

    await _persist_instance(inst)

    log.info(
        "loop.started",
        metadata={
            "loop_id": loop_id,
            "parent_task_id": parent_id,
            "first_child_id": child_id,
            "template": loop.name,
        },
    )
    return inst


# ---------------------------------------------------------------------------
# advance_loop
# ---------------------------------------------------------------------------


async def advance_loop(
    loop_id: str,
    completed_envelope: HandoffEnvelope,
) -> LoopInstance:
    """Called when an iteration child task completes.

    Order:
      1. Stamp envelope with loop_id + current iteration.
      2. evaluate_stop — judge says done? → CONVERGED.
      3. evaluate_escalation — judge says page (or hard cap hit)?
         → STOPPED_BY_CONDITION.
      4. Otherwise enqueue iteration N+1.

    Judge errors are NOT terminal — the loop keeps iterating until
    max_iterations is the backstop. See ``loop_judge.evaluate_stop``
    for the rationale.
    """
    from . import router

    inst = _instances.get(loop_id)
    if inst is None:
        raise ValueError(f"Loop '{loop_id}' not found")
    if inst.status not in (LoopStatus.RUNNING, LoopStatus.PENDING):
        raise ValueError(f"Loop '{loop_id}' is not active (status: {inst.status})")

    loop = _parsed_for(inst)

    iteration = inst.iteration
    stamped = completed_envelope.model_copy(update={
        "loop_id": loop_id,
        "iteration": iteration,
    })
    inst.envelope = stamped

    # Cost per iteration. Session-based execution doesn't surface
    # tokens; we keep the column populated with 0.0 for shape consistency
    # and future swap to a real cost source.
    iter_cost = 0.0
    inst.cost_history.append(iter_cost)
    inst.cost_usd += iter_cost

    envelope_dict = stamped.model_dump()

    # 1. Stop?
    stop_verdict = await evaluate_stop(
        envelope=envelope_dict,
        objective=loop.objective,
        stop_prose=loop.stop_prose,
    )
    if stop_verdict.fired:
        inst.stop_reason = stop_verdict.reason
        _terminate(inst, LoopStatus.CONVERGED, parent_status=TaskStatus.COMPLETED)
        await _persist_iteration(inst, iteration, iter_cost)
        await _persist_instance(inst)
        log.info(
            "loop.converged",
            metadata={
                "loop_id": loop_id,
                "iteration": iteration,
                "via": stop_verdict.via,
                "reason": stop_verdict.reason,
            },
        )
        return inst

    # 2. Escalate? (hard caps included)
    esc_verdict = await evaluate_escalation(
        envelope=envelope_dict,
        escalation_prose=loop.escalation_prose,
        iteration=iteration,
        max_iterations=loop.max_iterations,
        cost_usd=inst.cost_usd,
        budget_usd=loop.budget_usd,
    )
    if esc_verdict.fired:
        inst.stop_reason = esc_verdict.reason
        terminal = LoopStatus.MAX_ITER if "iteration cap" in esc_verdict.reason else LoopStatus.STOPPED_BY_CONDITION
        _terminate(inst, terminal, parent_status=TaskStatus.FAILED)
        await _persist_iteration(inst, iteration, iter_cost)
        await _persist_instance(inst)
        log.info(
            "loop.escalated",
            metadata={
                "loop_id": loop_id,
                "iteration": iteration,
                "via": esc_verdict.via,
                "reason": esc_verdict.reason,
                "status": terminal.value,
            },
        )
        return inst

    # 3. Continue — enqueue iteration N+1
    await _persist_iteration(inst, iteration, iter_cost)

    next_iter = iteration + 1
    if inst.current_child_id is not None:
        _child_to_loop.pop(inst.current_child_id, None)
    next_child_id = await _enqueue_iteration(
        inst,
        envelope=stamped,
        iteration=next_iter,
        workspace_home=router._tasks[inst.parent_task_id].workspace_home,
    )
    inst.iteration = next_iter
    inst.current_child_id = next_child_id
    inst.updated_at = _now_ms()

    await _persist_instance(inst)

    log.info(
        "loop.advanced",
        metadata={
            "loop_id": loop_id,
            "iteration": next_iter,
            "child_task_id": next_child_id,
            "cost_usd": inst.cost_usd,
        },
    )
    return inst


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _enqueue_iteration(
    inst: LoopInstance,
    *,
    envelope: HandoffEnvelope,
    iteration: int,
    workspace_home: str | None,
) -> str:
    """Enqueue the child task for one iteration.

    Single-agent shape: every iteration dispatches the same agent (the
    template's ``agent`` frontmatter field, defaulting to the template
    name). The role prose lives in the template body and is read by
    the agent at task start via the standard role-binding mechanism.
    """
    from . import router

    loop = _parsed_for(inst)
    description = f"loop {inst.id[:8]} iter {iteration}: {loop.agent}"

    task = await router.submit_task(
        description=description,
        agent_name=loop.agent,
        workspace_profile=inst.workspace_profile,
        workspace_home=workspace_home,
        job_id=inst.parent_task_id,
        loop_id=inst.id,
        loop_iteration=iteration,
        permission_tier=loop.permissions.value,
        node_requires=[],
    )
    _child_to_loop[task.id] = inst.id
    return task.id


def _terminate(
    inst: LoopInstance,
    loop_status: LoopStatus,
    *,
    parent_status: TaskStatus,
    error: str | None = None,
) -> None:
    """Move the LoopInstance to a terminal state and transition the parent
    task accordingly. Cleans up the child→loop reverse mapping."""
    from . import router

    now = _now_ms()
    inst.status = loop_status
    if error:
        inst.error = error
    inst.updated_at = now

    if inst.current_child_id is not None:
        _child_to_loop.pop(inst.current_child_id, None)
    inst.current_child_id = None

    parent = router._tasks.get(inst.parent_task_id)
    if parent is not None:
        parent.status = parent_status
        parent.updated_at = now
        if error:
            parent.error = error
        if parent_status == TaskStatus.COMPLETED:
            router._emit("task.completed", parent)
        elif parent_status == TaskStatus.FAILED:
            parent.error = parent.error or f"loop terminated: {loop_status.value}"
            router._emit("task.failed", parent)


async def cancel_loop(loop_id: str, reason: str = "operator cancelled") -> LoopInstance:
    """Operator-initiated termination of an in-flight loop."""
    from . import router

    inst = _instances.get(loop_id)
    if inst is None:
        raise ValueError(f"Loop '{loop_id}' not found")
    if inst.status not in (LoopStatus.RUNNING, LoopStatus.PENDING):
        return inst

    child_id = inst.current_child_id
    if child_id is not None:
        child = router._tasks.get(child_id)
        if child is not None and child.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            try:
                await router.cancel_task(child_id)
            except Exception as exc:
                log.warning(
                    "loop.cancel_child_failed",
                    metadata={"loop_id": loop_id, "child_id": child_id, "reason": str(exc)},
                )

    inst.error = reason
    _terminate(inst, LoopStatus.CANCELLED, parent_status=TaskStatus.CANCELLED, error=reason)
    await _persist_instance(inst)
    log.info("loop.cancelled", metadata={"loop_id": loop_id, "reason": reason})
    return inst


async def on_iteration_failed(child_task_id: str, error: str) -> LoopInstance | None:
    """Called when an iteration child task transitions to FAILED."""
    loop_id = _child_to_loop.get(child_task_id)
    if loop_id is None:
        return None
    inst = _instances.get(loop_id)
    if inst is None or inst.status not in (LoopStatus.RUNNING, LoopStatus.PENDING):
        return inst
    _terminate(
        inst,
        LoopStatus.FAILED,
        parent_status=TaskStatus.FAILED,
        error=f"iteration {inst.iteration} failed: {error}",
    )
    await _persist_instance(inst)
    log.info(
        "loop.iteration_failed",
        metadata={"loop_id": loop_id, "iteration": inst.iteration, "error": error},
    )
    return inst


# ---------------------------------------------------------------------------
# Persistence — async wrappers around store helpers
# ---------------------------------------------------------------------------


async def _persist_instance(inst: LoopInstance) -> None:
    from . import store

    try:
        await store.async_upsert_loop_instance(inst)
    except Exception as exc:
        log.warning(
            "loop.persist_instance_failed",
            metadata={"loop_id": inst.id, "reason": str(exc)},
        )


async def _persist_iteration(
    inst: LoopInstance,
    iteration: int,
    cost_value: float,
) -> None:
    """Write one iteration row. The ``convergence_metric_value`` column
    name is preserved for schema continuity; semantically it now carries
    the iteration's USD cost. The frontend label will be updated in
    Phase 3 to match."""
    from . import store

    try:
        await store.async_insert_loop_iteration_metric(
            loop_id=inst.id,
            iteration=iteration,
            convergence_metric_value=cost_value,
            timestamp_ms=_now_ms(),
            state_at_end=inst.status.value,
        )
    except Exception as exc:
        log.warning(
            "loop.persist_metric_failed",
            metadata={"loop_id": inst.id, "iteration": iteration, "reason": str(exc)},
        )


async def rehydrate_from_store() -> int:
    """Load every active LoopInstance from the DB into the in-memory map.
    Called from hub.init on daemon startup."""
    from . import store

    actives = await store.async_load_active_loop_instances()
    for inst in actives:
        _instances[inst.id] = inst
        if inst.current_child_id is not None:
            _child_to_loop[inst.current_child_id] = inst.id
    log.info("loop.rehydrated", metadata={"count": len(actives)})
    return len(actives)


# ---------------------------------------------------------------------------
# Router event bridge
# ---------------------------------------------------------------------------


_listener_registered = False
_pending_bridges: list[asyncio.Task] = []


def _envelope_from_task(task: Task) -> HandoffEnvelope:
    """Extract an envelope from the completed task's result. See the
    earlier docstring for the four shapes; behavior unchanged from the
    pre-markdown runner."""
    result = task.result
    if isinstance(result, HandoffEnvelope):
        return result
    if isinstance(result, dict):
        try:
            return HandoffEnvelope.model_validate(result)
        except Exception:
            return HandoffEnvelope()
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return HandoffEnvelope()
        if isinstance(parsed, dict):
            try:
                return HandoffEnvelope.model_validate(parsed)
            except Exception:
                return HandoffEnvelope()
        return HandoffEnvelope()
    return HandoffEnvelope()


def _on_router_event(event: str, task: Task) -> None:
    if event not in ("task.completed", "task.failed"):
        return
    loop_id = _child_to_loop.get(task.id)
    if loop_id is None:
        return

    if event == "task.completed":
        envelope = _envelope_from_task(task)
        coro = advance_loop(loop_id, envelope)
    else:
        coro = on_iteration_failed(task.id, task.error or "unknown failure")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.debug("loop.bridge_no_running_loop", metadata={"event": event, "task_id": task.id})
        return

    aio_task = loop.create_task(coro)
    _pending_bridges.append(aio_task)

    def _on_done(t: asyncio.Task) -> None:
        try:
            _pending_bridges.remove(t)
        except ValueError:
            pass
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.warning(
                "loop.bridge_advance_failed",
                metadata={
                    "event": event,
                    "task_id": task.id,
                    "loop_id": loop_id,
                    "reason": str(exc),
                },
            )

    aio_task.add_done_callback(_on_done)


async def wait_for_bridges() -> None:
    """Test helper: await every in-flight bridge task."""
    tasks = list(_pending_bridges)
    if not tasks:
        return
    await asyncio.gather(*tasks, return_exceptions=True)


def start() -> None:
    """Register the router event listener. Called from hub.init."""
    from . import router

    global _listener_registered
    if _listener_registered:
        return
    router.on_event(_on_router_event)
    _listener_registered = True
    log.info("loop.bridge_started")


def reset_for_tests() -> None:
    global _listener_registered
    _instances.clear()
    _child_to_loop.clear()
    _parsed.clear()
    _pending_bridges.clear()
    _listener_registered = False
