"""Loop runner core — Phase A3b.

Drives a LoopSpec end-to-end:
  start_loop(spec, envelope) → creates LoopInstance, parent task, first child
  advance_loop(loop_id, envelope) → called when an iteration completes;
    evaluates convergence/stop/max-iter/thrash and either enqueues the next
    iteration or terminates the loop.

The parent task is RUNNING throughout the loop's life — it represents the
loop itself in the existing Tasks panel and never gets dispatched (the
scheduler only dispatches PENDING). The iteration *children* are normal
tasks dispatched by the scheduler. When a child completes, the loop runner
(via a router event listener wired up in A3c, or directly in tests) calls
advance_loop with the child's emitted envelope.

A3b scope:
  - Single-node iteration. Each iteration enqueues a child task that runs
    the body's FIRST node. Multi-node flowchart traversal (edge predicates,
    fan-out/fan-in, EdgeTransform) is a follow-up — the data model supports
    it, the runner doesn't traverse it yet.
  - In-memory instance store. Persistence to the brainbox store comes
    with A3c, when iteration-metric rows also start being written.
  - No router event-listener registration here. Tests call advance_loop
    directly; the listener wiring lands in A3c so the dispatch path is
    intact across PR boundaries.

See ~/.claude/plans/okay-the-idea-of-replicated-popcorn.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from .log import get_logger
from .loop_predicate import eval_metric, eval_predicate
from .loops import (
    HandoffEnvelope,
    LoopInstance,
    LoopSpec,
    LoopStatus,
    StopCondition,
    TemplateSnapshot,
)
from .models import Task, TaskStatus
from .utils import now_ms as _now_ms

log = get_logger()

# ---------------------------------------------------------------------------
# State — in-memory until A3c adds persistence
# ---------------------------------------------------------------------------

_instances: dict[str, LoopInstance] = {}

# Reverse index: child_task_id → loop_id. Lets the router event listener
# (A3c) find the loop a completing task belongs to in O(1).
_child_to_loop: dict[str, str] = {}


def get_instance(loop_id: str) -> LoopInstance | None:
    return _instances.get(loop_id)


def list_instances() -> list[LoopInstance]:
    return list(_instances.values())


def loop_id_for_child(child_task_id: str) -> str | None:
    return _child_to_loop.get(child_task_id)


# ---------------------------------------------------------------------------
# start_loop
# ---------------------------------------------------------------------------


async def start_loop(
    spec: LoopSpec,
    initial_envelope: HandoffEnvelope,
    *,
    workspace_profile: str | None = None,
    workspace_home: str | None = None,
) -> LoopInstance:
    """Create a Loop instance, its parent task, and enqueue iteration 1.

    Returns the LoopInstance with ``status == RUNNING`` and
    ``current_child_id`` set to the iteration-1 child task.

    Raises ValueError if the LoopSpec body is empty (a loop with no nodes
    cannot iterate).
    """
    from . import router

    if not spec.body.nodes:
        raise ValueError("LoopSpec.body.nodes must contain at least one node")

    # required_refs are presence-checked against initial_envelope.artifact_refs
    # before we enqueue iteration 1. Day 1 doesn't enforce types; missing
    # required ref names surface as a single ValueError with the full list,
    # so the operator sees every gap at once rather than fixing them one by
    # one.
    missing_refs = [
        ref.name
        for ref in spec.required_refs
        if ref.required and ref.name not in (initial_envelope.artifact_refs or {})
    ]
    if missing_refs:
        raise ValueError(
            f"missing required artifact_refs: {', '.join(missing_refs)}"
        )

    loop_id = str(uuid.uuid4())
    now = _now_ms()

    # Pin the template snapshot if the caller didn't already. The runner
    # only reads from the snapshot for the duration of the loop — see plan
    # for the pin-template / live-bind-roles rationale.
    if spec.template_snapshot is None:
        body_json = spec.model_dump_json(by_alias=True)
        snapshot = TemplateSnapshot(
            name=spec.name or "ad-hoc",
            version="",
            hash=hashlib.sha256(body_json.encode()).hexdigest()[:16],
            body_json=body_json,
        )
        spec = spec.model_copy(update={"template_snapshot": snapshot})

    # Stamp the loop_id onto the envelope so every downstream predicate has
    # a stable handle to the loop it came from.
    initial_envelope = initial_envelope.model_copy(update={
        "loop_id": loop_id,
        "iteration": 0,
    })

    # Create the parent task directly in router._tasks — register_ci_ratchet_task
    # is the existing precedent for special-purpose tasks that bypass
    # submit_task's policy / agent-registry check. The parent stays RUNNING
    # throughout the loop's life and never gets dispatched (scheduler only
    # picks up PENDING).
    parent_id = str(uuid.uuid4())
    parent = Task(
        id=parent_id,
        description=f"loop: {spec.name or spec.id or 'ad-hoc'}",
        agent_name=f"loop:{spec.name or 'ad-hoc'}",
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
        job_id=parent_id,
        workspace_profile=workspace_profile,
        workspace_home=workspace_home,
    )
    router._tasks[parent_id] = parent

    # Create the instance BEFORE enqueueing the first child so the
    # child_to_loop reverse index is consistent if anything fires immediately.
    inst = LoopInstance(
        id=loop_id,
        spec_snapshot=spec,
        parent_task_id=parent_id,
        status=LoopStatus.RUNNING,
        iteration=0,
        envelope=initial_envelope,
        workspace_profile=workspace_profile,
        created_at=now,
        updated_at=now,
    )
    _instances[loop_id] = inst

    # Enqueue iteration 1 — see _enqueue_iteration for the single-node
    # simplification A3b is taking.
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
            "spec": spec.name,
        },
    )
    return inst


# ---------------------------------------------------------------------------
# advance_loop — convergence / stop / thrash / max-iter / continue
# ---------------------------------------------------------------------------


async def advance_loop(
    loop_id: str,
    completed_envelope: HandoffEnvelope,
) -> LoopInstance:
    """Called when an iteration child task completes.

    Evaluates the loop's predicates in order:
      1. convergence_predicate — true → CONVERGED, parent COMPLETED
      2. stop_conditions       — any match → STOPPED_BY_CONDITION, parent FAILED
      3. max_iterations cap    — reached → MAX_ITER, parent FAILED
      4. thrash detection      — metric non-decreasing for 2 iterations → THRASHING, parent FAILED
      5. otherwise             → enqueue next iteration, update current_child_id

    The completed_envelope is stamped with the loop_id and iteration before
    the metric is read off it — so callers can pass either a freshly-built
    envelope or the actual child task's result.
    """
    from . import router

    inst = _instances.get(loop_id)
    if inst is None:
        raise ValueError(f"Loop '{loop_id}' not found")
    if inst.status not in (LoopStatus.RUNNING, LoopStatus.PENDING):
        raise ValueError(
            f"Loop '{loop_id}' is not active (status: {inst.status})"
        )

    spec = inst.spec_snapshot
    iteration = inst.iteration
    stamped = completed_envelope.model_copy(update={
        "loop_id": loop_id,
        "iteration": iteration,
    })
    inst.envelope = stamped

    # Record the convergence metric for this iteration. Missing field is 0.0
    # (matches the Go side coercion); we let that through so charts can
    # render iteration N even when no findings populated.
    #
    # The metric row is PERSISTED after the status decision below so
    # ``state_at_end`` carries the correct terminal status (CONVERGED,
    # THRASHING, etc.) instead of stale RUNNING. ``metric_history`` is
    # updated here because thrash detection reads it before the persist.
    metric_value = (
        eval_metric(stamped, spec.convergence_metric)
        if spec.convergence_metric
        else 0.0
    )
    inst.metric_history.append(metric_value)

    # 1. Convergence — the happy path
    if eval_predicate(stamped, spec.convergence_predicate):
        _terminate(inst, LoopStatus.CONVERGED, parent_status=TaskStatus.COMPLETED)
        await _persist_iteration_metric(inst, iteration, metric_value)
        await _persist_instance(inst)
        log.info(
            "loop.converged",
            metadata={"loop_id": loop_id, "iteration": iteration, "metric": metric_value},
        )
        return inst

    # 2. Stop conditions — explicit operator-authored caps (diff size, cost)
    matched = _matched_stop_condition(spec.stop_conditions, stamped)
    if matched is not None:
        inst.stop_reason = matched.reason or matched.predicate
        _terminate(inst, LoopStatus.STOPPED_BY_CONDITION, parent_status=TaskStatus.FAILED)
        await _persist_iteration_metric(inst, iteration, metric_value)
        await _persist_instance(inst)
        log.info(
            "loop.stopped_by_condition",
            metadata={"loop_id": loop_id, "iteration": iteration, "reason": inst.stop_reason},
        )
        return inst

    # 3. Iteration cap — last line of defense against runaway
    if iteration >= spec.max_iterations:
        _terminate(inst, LoopStatus.MAX_ITER, parent_status=TaskStatus.FAILED)
        await _persist_iteration_metric(inst, iteration, metric_value)
        await _persist_instance(inst)
        log.info(
            "loop.max_iter",
            metadata={"loop_id": loop_id, "iteration": iteration, "cap": spec.max_iterations},
        )
        return inst

    # 4. Thrash — non-decreasing metric for two consecutive iterations.
    # Needs at least 3 datapoints to make a non-decreasing claim (i-2, i-1, i).
    # Cheap heuristic that catches 80% of "agent is editing without converging."
    if _is_thrashing(inst.metric_history):
        _terminate(inst, LoopStatus.THRASHING, parent_status=TaskStatus.FAILED)
        await _persist_iteration_metric(inst, iteration, metric_value)
        await _persist_instance(inst)
        log.info(
            "loop.thrashing",
            metadata={"loop_id": loop_id, "iteration": iteration, "history": inst.metric_history},
        )
        return inst

    # Non-terminal: write the iteration row with state RUNNING before
    # enqueueing the next child.
    await _persist_iteration_metric(inst, iteration, metric_value)

    # 5. Continue — enqueue iteration N+1
    next_iter = iteration + 1
    # Old child mapping no longer needed; child task already COMPLETED.
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
            "metric": metric_value,
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
    """Enqueue the child task for one iteration of the loop body.

    A3b simplification: runs the FIRST node only. Flowchart traversal (edges,
    fan-out/fan-in) is a follow-up. The prompt is the node's prompt with
    {{iteration}} substituted; full envelope-driven templating lands when the
    real review-driven loop template is authored.
    """
    from . import router

    spec = inst.spec_snapshot
    first_node = spec.body.nodes[0]
    # Prefer agent_id if set; fall back to role (which becomes the agent_name)
    agent_name = first_node.agent_id or first_node.role
    if not agent_name:
        raise ValueError(
            f"loop {inst.id}: first node {first_node.id!r} has no agent_id or role"
        )

    description = (
        f"loop {inst.id[:8]} iter {iteration}: "
        f"{first_node.id} ({first_node.kind.value})"
    )

    task = await router.submit_task(
        description=description,
        agent_name=agent_name,
        workspace_profile=inst.workspace_profile,
        workspace_home=workspace_home,
        job_id=inst.parent_task_id,
        loop_id=inst.id,
        loop_iteration=iteration,
        permission_tier=spec.permissions.value if spec.permissions else None,
        node_requires=list(first_node.requires),
        model_target=first_node.model_target,
    )
    _child_to_loop[task.id] = inst.id
    return task.id


def _matched_stop_condition(
    conditions: list[StopCondition],
    envelope: HandoffEnvelope,
) -> StopCondition | None:
    for cond in conditions:
        if eval_predicate(envelope, cond.predicate):
            return cond
    return None


def _is_thrashing(history: list[float]) -> bool:
    """Two consecutive non-decreasing metric values = thrashing.

    Needs at least three datapoints: with history [a, b, c], we check whether
    b >= a AND c >= b. A single flat or rising step is normal noise; two in a
    row is the agent failing to converge.
    """
    if len(history) < 3:
        return False
    a, b, c = history[-3], history[-2], history[-1]
    return b >= a and c >= b


def _terminate(
    inst: LoopInstance,
    loop_status: LoopStatus,
    *,
    parent_status: TaskStatus,
    error: str | None = None,
) -> None:
    """Move the LoopInstance to a terminal state and transition the parent
    task accordingly. Cleans up the child→loop reverse mapping.

    Direct task-state writes (bypassing complete_task / fail_task) are
    intentional: complete_task requires status=RUNNING-with-session-name,
    which the loop parent doesn't have. The parent is a marker task, not a
    real dispatch.
    """
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


# ---------------------------------------------------------------------------
# Failure handling — child task FAILED → loop FAILED
# ---------------------------------------------------------------------------


async def cancel_loop(loop_id: str, reason: str = "operator cancelled") -> LoopInstance:
    """Operator-initiated termination of an in-flight loop.

    Transitions the LoopInstance to CANCELLED, marks the parent task
    CANCELLED, and best-effort cancels the current iteration child task.
    Idempotent: cancelling an already-terminal loop returns it unchanged.

    Child cancellation is best-effort because a running brainbox session
    may not respond synchronously to a queue-level cancel — recycling
    happens via router._finalize_task, which is async and can fail if
    the runner is gone. We accept partial cleanup here; the bridge's
    failure path will catch any orphaned state when the child eventually
    completes or fails.
    """
    from . import router

    inst = _instances.get(loop_id)
    if inst is None:
        raise ValueError(f"Loop '{loop_id}' not found")
    if inst.status not in (LoopStatus.RUNNING, LoopStatus.PENDING):
        return inst  # already terminal — no-op

    # Cancel the in-flight child first so it doesn't fire a stale
    # task.completed event after the loop is marked cancelled.
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
    """Called when an iteration child task transitions to FAILED.

    The loop fails too — no automatic retry at the loop layer because the
    router already retried per the task's max_attempts policy. If retry is
    desired, the operator restarts the whole loop.
    """
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
    """Persist on every state transition (start, advance, terminate).
    Catches and logs errors so a transient DB problem doesn't lose the
    in-memory loop. The in-memory state is the source of truth between ticks.
    """
    from . import store

    try:
        await store.async_upsert_loop_instance(inst)
    except Exception as exc:
        log.warning(
            "loop.persist_instance_failed",
            metadata={"loop_id": inst.id, "reason": str(exc)},
        )


async def _persist_iteration_metric(
    inst: LoopInstance,
    iteration: int,
    metric_value: float,
) -> None:
    """Write one iteration row. UPSERT on (loop_id, iteration) so re-running
    an iteration during restart-recovery overwrites rather than duplicates.
    """
    from . import store

    try:
        await store.async_insert_loop_iteration_metric(
            loop_id=inst.id,
            iteration=iteration,
            convergence_metric_value=metric_value,
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

    Called from hub.init on daemon startup. Rebuilds the child_to_loop reverse
    index too so a newly-arriving task.completed event finds its loop.
    Returns the number of instances rehydrated.
    """
    from . import store

    actives = await store.async_load_active_loop_instances()
    for inst in actives:
        _instances[inst.id] = inst
        if inst.current_child_id is not None:
            _child_to_loop[inst.current_child_id] = inst.id
    log.info("loop.rehydrated", metadata={"count": len(actives)})
    return len(actives)


# ---------------------------------------------------------------------------
# Router event bridge — task.completed / task.failed → advance / fail
# ---------------------------------------------------------------------------


_listener_registered = False

# Pending bridge tasks — populated by _on_router_event when it schedules
# advance_loop/on_iteration_failed. Tests await wait_for_bridges() between
# emitting a router event and asserting the loop state; production callers
# never need to look at this list.
_pending_bridges: list[asyncio.Task] = []


def _envelope_from_task(task: Task) -> HandoffEnvelope:
    """Extract the iteration envelope from the completed task's result.

    Four shapes the task.result can carry, in order of preference:
      - a HandoffEnvelope instance — direct typed return (rare today;
        becomes common once dispatch grows native envelope support)
      - a dict that validates as an envelope — direct hub message with a
        structured ``result`` payload
      - a string that parses as JSON to a dict that validates — the
        canonical path while agents call the existing ``complete.sh``
        with ``$(cat /tmp/loop-envelope.json)`` as the result arg
      - anything else — empty envelope; the runner falls into the next
        predicate evaluation, blockers count is 0, and convergence
        either fires (zero-blocker template) or doesn't (review template
        with CI gate). Either way, no exception bubbles up.

    Malformed JSON strings, non-dict JSON values, and dicts that fail
    HandoffEnvelope validation all silently fall through to the empty
    envelope. The bridge runs inside a router event listener — raising
    here would drop a loop, which is the failure mode we wrote
    _on_router_event's done_callback to surface, but is worse than
    silently treating "no findings" as "no progress."
    """
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
    """Sync router-event listener; schedules async advance/fail on the loop.

    Routes:
      - ``task.completed`` for a known iteration child → ``advance_loop``
      - ``task.failed``     for a known iteration child → ``on_iteration_failed``
      - anything else                                    → no-op

    Errors in the scheduled coroutine are caught + logged via the
    done-callback so a failing advance doesn't quietly drop a loop.
    """
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
        # Sync context (tests / non-daemon callers). Tests call advance_loop
        # directly; production always has a running loop because the daemon
        # wires this listener inside the FastAPI event loop.
        log.debug(
            "loop.bridge_no_running_loop",
            metadata={"event": event, "task_id": task.id},
        )
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
    """Await every in-flight bridge task. Test helper.

    Use after emitting a router event so the scheduled advance_loop /
    on_iteration_failed coroutines complete before the test asserts
    against loop state. Production code never needs this — events flow
    inside the daemon's event loop and the bridge is fire-and-forget.
    """
    tasks = list(_pending_bridges)
    if not tasks:
        return
    await asyncio.gather(*tasks, return_exceptions=True)


def start() -> None:
    """Register the router event listener. Called from hub.init.

    Idempotent — calling twice does not double-register because the router's
    listener list is identity-based.
    """
    from . import router

    global _listener_registered
    if _listener_registered:
        return
    router.on_event(_on_router_event)
    _listener_registered = True
    log.info("loop.bridge_started")


def reset_for_tests() -> None:  # type: ignore[no-redef]
    """Override of the earlier reset — also clears the listener-registered flag.

    The conftest fixture runs this between tests, so any test that explicitly
    calls start() gets a fresh registration the next time.
    """
    global _listener_registered
    _instances.clear()
    _child_to_loop.clear()
    _pending_bridges.clear()
    _listener_registered = False
