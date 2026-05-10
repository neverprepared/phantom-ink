"""In-memory bundle-request queue — the bridge between the docker backend
(producer) and the laptop command-center daemon (consumer).

The producer awaits a future for the sealed ciphertext. The consumer pulls
one request at a time via long-poll, computes the seal, and posts the
ciphertext back. Same process — the API and the docker backend share this
queue via the module-level singleton.

Phase 5: this is the path used when the API host is not the laptop. The
daemon connects out to the API (across NAT, firewalls, etc.) and feeds
sealed bytes back through this queue.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field


@dataclass
class BundleRequest:
    id: str
    workspace_profile: str | None
    workspace_home: str | None
    recipient: str
    created_at: float
    fut: asyncio.Future[bytes] = field(repr=False)


class BundleRequestQueue:
    def __init__(self) -> None:
        self._pending: list[BundleRequest] = []
        self._by_id: dict[str, BundleRequest] = {}
        self._has_pending = asyncio.Event()
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        *,
        workspace_profile: str | None,
        workspace_home: str | None,
        recipient: str,
    ) -> BundleRequest:
        loop = asyncio.get_running_loop()
        req = BundleRequest(
            id=secrets.token_hex(12),
            workspace_profile=workspace_profile,
            workspace_home=workspace_home,
            recipient=recipient,
            created_at=time.time(),
            fut=loop.create_future(),
        )
        async with self._lock:
            self._pending.append(req)
            self._by_id[req.id] = req
            self._has_pending.set()
        return req

    async def next_pending(self, *, timeout: float = 30.0) -> BundleRequest | None:
        """Long-poll for the next request. Returns None if none arrives in time."""
        try:
            await asyncio.wait_for(self._has_pending.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        async with self._lock:
            if not self._pending:
                self._has_pending.clear()
                return None
            req = self._pending.pop(0)
            if not self._pending:
                self._has_pending.clear()
            return req

    async def fulfill(self, request_id: str, ciphertext: bytes) -> bool:
        """Resolve the awaiting producer with sealed bytes. Returns False if the
        request id is unknown or already fulfilled (idempotent failure)."""
        async with self._lock:
            req = self._by_id.pop(request_id, None)
        if req is None:
            return False
        if req.fut.done():
            return False
        req.fut.set_result(ciphertext)
        return True

    async def cancel(self, request_id: str, reason: str) -> bool:
        async with self._lock:
            req = self._by_id.pop(request_id, None)
        if req is None or req.fut.done():
            return False
        req.fut.set_exception(RuntimeError(reason))
        return True

    @property
    def pending_count(self) -> int:
        return len(self._pending)


_singleton: BundleRequestQueue | None = None


def get_queue() -> BundleRequestQueue:
    global _singleton
    if _singleton is None:
        _singleton = BundleRequestQueue()
    return _singleton


def reset_queue_for_tests() -> None:
    global _singleton
    _singleton = None
