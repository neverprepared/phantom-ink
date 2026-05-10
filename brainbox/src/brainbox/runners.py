"""Brainbox runner registry + work queue.

A runner is a remote process that connects out to the API, registers what it
can execute (docker, utm), and pulls work via long-poll. The central API
treats runners as named compute targets — sessions created with a runner
field are dispatched to that runner instead of executed in-process.

In-memory only (same shape as credentials/queue.py). Persistence of runner
identity belongs to hub_state.json later; the work queue is intentionally
ephemeral so a restart drains in-flight work cleanly.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunnerInfo:
    name: str
    capabilities: dict[str, bool]
    tags: list[str]
    version: str
    registered_at: int
    last_seen: int


@dataclass
class WorkItem:
    id: str
    runner: str
    kind: str
    payload: dict[str, Any]
    created_at: float
    fut: asyncio.Future[dict[str, Any]] = field(repr=False)


class RunnerRegistry:
    """Tracks live runners. Each runner has its own pending work list and a
    condition variable so long-poll wakes immediately when work arrives."""

    def __init__(self) -> None:
        self._runners: dict[str, RunnerInfo] = {}
        self._pending: dict[str, list[WorkItem]] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._by_work_id: dict[str, WorkItem] = {}
        self._lock = asyncio.Lock()

    # -- runner lifecycle -----------------------------------------------------

    async def register(
        self,
        *,
        name: str,
        capabilities: dict[str, bool],
        tags: list[str] | None = None,
        version: str = "",
    ) -> RunnerInfo:
        now = int(time.time() * 1000)
        info = RunnerInfo(
            name=name,
            capabilities=capabilities,
            tags=tags or [],
            version=version,
            registered_at=now,
            last_seen=now,
        )
        async with self._lock:
            self._runners[name] = info
            self._pending.setdefault(name, [])
            self._events.setdefault(name, asyncio.Event())
        return info

    async def touch(self, name: str) -> None:
        async with self._lock:
            r = self._runners.get(name)
            if r is not None:
                r.last_seen = int(time.time() * 1000)

    async def list_runners(self) -> list[RunnerInfo]:
        async with self._lock:
            return list(self._runners.values())

    async def get(self, name: str) -> RunnerInfo | None:
        async with self._lock:
            return self._runners.get(name)

    # -- work dispatch --------------------------------------------------------

    async def enqueue(
        self, *, runner: str, kind: str, payload: dict[str, Any]
    ) -> WorkItem:
        if not await self.get(runner):
            raise RuntimeError(f"runner {runner!r} is not registered")
        loop = asyncio.get_running_loop()
        item = WorkItem(
            id=secrets.token_hex(12),
            runner=runner,
            kind=kind,
            payload=payload,
            created_at=time.time(),
            fut=loop.create_future(),
        )
        async with self._lock:
            self._pending.setdefault(runner, []).append(item)
            self._by_work_id[item.id] = item
            ev = self._events.setdefault(runner, asyncio.Event())
        ev.set()
        return item

    async def next_pending(self, runner: str, *, timeout: float = 30.0) -> WorkItem | None:
        # Make sure runner has an event even if it was registered after this call started.
        async with self._lock:
            ev = self._events.setdefault(runner, asyncio.Event())
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        async with self._lock:
            pending = self._pending.setdefault(runner, [])
            if not pending:
                ev.clear()
                return None
            item = pending.pop(0)
            if not pending:
                ev.clear()
            r = self._runners.get(runner)
            if r is not None:
                r.last_seen = int(time.time() * 1000)
            return item

    async def fulfill(self, work_id: str, result: dict[str, Any]) -> bool:
        async with self._lock:
            item = self._by_work_id.pop(work_id, None)
        if item is None or item.fut.done():
            return False
        item.fut.set_result(result)
        return True

    async def cancel(self, work_id: str, reason: str) -> bool:
        async with self._lock:
            item = self._by_work_id.pop(work_id, None)
        if item is None or item.fut.done():
            return False
        item.fut.set_exception(RuntimeError(reason))
        return True

    @property
    def runner_count(self) -> int:
        return len(self._runners)


_singleton: RunnerRegistry | None = None


def get_registry() -> RunnerRegistry:
    global _singleton
    if _singleton is None:
        _singleton = RunnerRegistry()
    return _singleton


def reset_registry_for_tests() -> None:
    global _singleton
    _singleton = None
