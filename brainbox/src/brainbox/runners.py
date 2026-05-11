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

    async def deregister(self, name: str) -> bool:
        """Remove a runner. Pending work for that runner is cancelled with a
        clear reason so producers stop waiting. Returns True if the runner
        existed and was removed."""
        async with self._lock:
            info = self._runners.pop(name, None)
            pending = self._pending.pop(name, [])
            self._events.pop(name, None)
        if info is None:
            return False
        for item in pending:
            if not item.fut.done():
                item.fut.set_exception(RuntimeError(f"runner {name!r} was removed"))
            self._by_work_id.pop(item.id, None)
        return True

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


# ---------------------------------------------------------------------------
# Pairing — one-time tokens that let a new runner claim an api_url + api_key
# without the operator pasting the key by hand. The Wails app calls /pair/start
# with the URL+key it already has, shows the resulting token as a QR / phrase,
# and the runner calls /pair/claim with that token.
# ---------------------------------------------------------------------------


@dataclass
class PairingTicket:
    token: str
    api_url: str
    api_key: str
    runner_name_suggestion: str
    expires_at: float  # epoch seconds
    consumed: bool = False


class PairingStore:
    """In-memory, single-use, TTL-bound pairing tickets. Stays small (a few
    tickets at a time) so no LRU eviction — expired entries are cleaned on
    every access."""

    def __init__(self) -> None:
        self._tickets: dict[str, PairingTicket] = {}
        self._lock = asyncio.Lock()

    async def issue(
        self,
        *,
        api_url: str,
        api_key: str,
        runner_name_suggestion: str = "",
        ttl_seconds: float = 300.0,
    ) -> PairingTicket:
        # Short, URL-safe, unambiguous-ish. 12 chars of secrets.token_urlsafe
        # gives ~9 bytes of entropy — plenty for a 5-minute single-use token.
        token = secrets.token_urlsafe(9)
        ticket = PairingTicket(
            token=token,
            api_url=api_url,
            api_key=api_key,
            runner_name_suggestion=runner_name_suggestion,
            expires_at=time.time() + ttl_seconds,
        )
        async with self._lock:
            self._sweep_expired_locked()
            self._tickets[token] = ticket
        return ticket

    async def claim(self, token: str) -> PairingTicket | None:
        async with self._lock:
            self._sweep_expired_locked()
            ticket = self._tickets.get(token)
            if ticket is None or ticket.consumed:
                return None
            ticket.consumed = True
            # Drop immediately on successful claim; no second look.
            self._tickets.pop(token, None)
            return ticket

    def _sweep_expired_locked(self) -> None:
        now = time.time()
        for tok in [t for t, tk in self._tickets.items() if tk.expires_at < now]:
            self._tickets.pop(tok, None)


_pairing_singleton: PairingStore | None = None


def get_pairing_store() -> PairingStore:
    global _pairing_singleton
    if _pairing_singleton is None:
        _pairing_singleton = PairingStore()
    return _pairing_singleton


def reset_pairing_store_for_tests() -> None:
    global _pairing_singleton
    _pairing_singleton = None
