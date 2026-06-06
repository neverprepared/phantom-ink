"""Brainbox runner registry + work queue.

A runner is a remote process that connects out to the API, registers what it
can execute (docker, utm), and pulls work via long-poll. The central API
treats runners as named compute targets — sessions created with a runner
field are dispatched to that runner instead of executed in-process.

Runner identity is persisted to hub_state.json so runners appear as offline
after a restart until they heartbeat back. The work queue is intentionally
ephemeral — a restart drains in-flight work cleanly.
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
    # Advertised host/IP the runner is reachable at from the API server's
    # network. Used to build the ttyd URL for remote sessions.
    host: str | None = None
    # Stable UUID from the runner's UserDefaults — survives name changes.
    machine_id: str | None = None
    # Ollama port if this runner advertises ollama capability (default 11434).
    ollama_port: int | None = None
    # Load metrics — reported by the runner on register / heartbeat.
    queue_depth: int = 0     # items queued but not yet picked up
    in_flight: int = 0       # sessions currently provisioning on this runner
    max_concurrent: int = 4  # runner's self-reported concurrency limit


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

    async def deregister_by_machine_id(self, machine_id: str, *, except_name: str) -> None:
        """Remove any runner with the given machine_id whose name differs from
        except_name. Called on register so renames don't leave ghost entries."""
        async with self._lock:
            stale = [
                n for n, info in self._runners.items()
                if info.machine_id == machine_id and n != except_name
            ]
        for name in stale:
            await self.deregister(name)

    async def register(
        self,
        *,
        name: str,
        capabilities: dict[str, bool],
        tags: list[str] | None = None,
        version: str = "",
        host: str | None = None,
        in_flight: int = 0,
        max_concurrent: int = 4,
        machine_id: str | None = None,
        ollama_port: int | None = None,
    ) -> RunnerInfo:
        now = int(time.time() * 1000)
        info = RunnerInfo(
            name=name,
            capabilities=capabilities,
            tags=tags or [],
            version=version,
            host=host,
            registered_at=now,
            last_seen=now,
            in_flight=max(0, in_flight),
            max_concurrent=max(1, max_concurrent),
            machine_id=machine_id,
            ollama_port=ollama_port,
        )
        async with self._lock:
            self._runners[name] = info
            self._pending.setdefault(name, [])
            self._events.setdefault(name, asyncio.Event())
        from .store import async_upsert_runner
        await async_upsert_runner(info)
        return info

    async def touch(self, name: str) -> None:
        async with self._lock:
            r = self._runners.get(name)
            if r is not None:
                r.last_seen = int(time.time() * 1000)

    async def update_load(
        self,
        name: str,
        *,
        in_flight: int | None = None,
        max_concurrent: int | None = None,
    ) -> bool:
        """Update runner-reported load metrics. Returns False if runner not found."""
        async with self._lock:
            r = self._runners.get(name)
            if r is None:
                return False
            r.last_seen = int(time.time() * 1000)
            if in_flight is not None:
                r.in_flight = max(0, in_flight)
            if max_concurrent is not None and max_concurrent > 0:
                r.max_concurrent = max_concurrent
            return True

    async def list_runners(self) -> list[RunnerInfo]:
        async with self._lock:
            return list(self._runners.values())

    async def select_runner(
        self,
        *,
        backend: str = "docker",
        preferred_tags: list[str] | None = None,
    ) -> str | None:
        """Return the name of the best available runner, or None if none eligible.

        Eligibility: runner must advertise the backend capability, be online
        (last_seen within 90s), and have headroom (in_flight < max_concurrent).
        Among eligible runners: most headroom first, then shortest queue, then
        highest tag overlap, then alphabetical tiebreak.
        """
        now_ms = int(time.time() * 1000)
        async with self._lock:
            runners = list(self._runners.values())

        def _is_eligible(r: RunnerInfo) -> bool:
            return (
                bool(r.capabilities.get(backend))
                and (now_ms - r.last_seen) < 90_000
                and r.in_flight < r.max_concurrent
            )

        def _sort_key(r: RunnerInfo) -> tuple:
            headroom = max(0, r.max_concurrent - r.in_flight)
            tag_score = sum(1 for t in (preferred_tags or []) if t in r.tags)
            return (-headroom, r.queue_depth, -tag_score, r.name)

        eligible = [r for r in runners if _is_eligible(r)]
        if not eligible:
            return None
        return min(eligible, key=_sort_key).name

    async def get(self, name: str) -> RunnerInfo | None:
        async with self._lock:
            return self._runners.get(name)

    async def deregister(self, name: str) -> bool:
        """Remove a runner. Pending work is rerouted to another eligible runner
        if one exists; otherwise cancelled. Returns True if the runner existed."""
        async with self._lock:
            info = self._runners.pop(name, None)
            pending = self._pending.pop(name, [])
            self._events.pop(name, None)
        if info is None:
            return False
        from .store import async_delete_runner
        await async_delete_runner(name)
        for item in pending:
            if item.fut.done():
                self._by_work_id.pop(item.id, None)
                continue
            backend = item.payload.get("backend") or "docker"
            tags = item.payload.get("runner_tags") or []
            alt = await self.select_runner(backend=backend, preferred_tags=tags)
            if alt:
                item.runner = alt
                async with self._lock:
                    self._pending.setdefault(alt, []).append(item)
                    ev = self._events.get(alt)
                    r = self._runners.get(alt)
                    if r is not None:
                        r.queue_depth = len(self._pending[alt])
                if ev:
                    ev.set()
            else:
                item.fut.set_exception(
                    RuntimeError(f"runner {name!r} was removed and no eligible replacement found")
                )
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
            r = self._runners.get(runner)
            if r is not None:
                r.queue_depth = len(self._pending[runner])
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
                r.queue_depth = len(pending)
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
# Hub state persistence
# ---------------------------------------------------------------------------


def get_state() -> dict:
    """Serialize registered runners for hub_state.json. Work queues are not
    persisted — they are intentionally ephemeral."""
    reg = get_registry()
    return {
        name: {
            "name": info.name,
            "capabilities": info.capabilities,
            "tags": info.tags,
            "version": info.version,
            "host": info.host,
            "registered_at": info.registered_at,
            "last_seen": 0,  # force offline on restore; runner must heartbeat to go live
            "max_concurrent": info.max_concurrent,
        }
        for name, info in reg._runners.items()
    }


def restore_state(data: dict | None) -> None:
    """Restore runner registrations on startup. Tries DB first; falls back to
    hub_state.json blob for the first boot after Phase 2 is deployed.
    All runners are marked offline (last_seen=0) until they heartbeat."""
    from .store import load_all_runners
    reg = get_registry()

    db_rows = load_all_runners()
    if db_rows:
        for entry in db_rows:
            try:
                info = RunnerInfo(**entry)
                reg._runners[info.name] = info
                reg._pending.setdefault(info.name, [])
                reg._events.setdefault(info.name, asyncio.Event())
            except Exception:
                pass
        return

    # DB empty — first boot with Phase 2. Fall back to JSON blob so we don't
    # lose the runner list on the transition restart.
    if not data:
        return
    for entry in data.values():
        try:
            info = RunnerInfo(**entry)
            reg._runners[info.name] = info
            reg._pending.setdefault(info.name, [])
            reg._events.setdefault(info.name, asyncio.Event())
        except Exception:
            pass


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
