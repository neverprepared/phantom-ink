"""Peer-sync pull client (Slice 3b) — the transport half of local-first sync.

A background tick that, per configured peer, pulls that peer's exported
``agent_events`` (op-log, ULID cursor) and owner-keyed rows (currently
``runners``, ``updated_at`` cursor), merges them via :mod:`node_sync`, and
persists a per-peer, per-stream resume cursor in ``sync_cursors``.

OFF unless ``CL_SYNC__ENABLED`` and ``CL_SYNC__PEERS`` are set (the lifespan
only starts :func:`pull_loop` then). Transport is the curl subprocess
(:func:`ollama.acurl_request`), NOT httpx — the long-running daemon hits
spurious ``OSError 65`` on Python sockets to LAN peers on macOS/Py3.14 (see
CLAUDE.md and ``api._curl_http_request``). This is eventual-consistency state
gossip only: no work-claiming, no push, no exactly-once.

Peer config — ``CL_SYNC__PEERS`` is a JSON list of strings, each::

    <label>=<base_url>|<token>

e.g. ``m3=http://m3-64.neverprepared.com:8790|<bearer>``. ``label`` keys the
cursor; ``token`` is optional and defaults to this node's own API key (the
shared-key mesh). A peer whose export 404s (sync disabled there) or 401s (bad
token) is logged and skipped — one dead peer never stalls the others.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import urlencode

from . import node_sync, ollama, store
from .auth import get_api_key
from .config import settings
from .log import get_logger

log = get_logger()

# Per-request curl timeout for pulling a peer's export batch. Short: a slow or
# unreachable peer must not hold up the tick past the pull cadence.
_PULL_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Peer:
    label: str
    base_url: str
    token: str


def parse_peer(spec: str) -> Peer:
    """Parse one ``<label>=<base_url>|<token>`` spec.

    ``token`` is optional; when omitted it falls back to this node's own API key
    (the shared-key mesh case). Raises ``ValueError`` on a malformed spec.
    """
    if "=" not in spec:
        raise ValueError(f"peer spec missing '=': {spec!r}")
    label, _, rest = spec.partition("=")
    base_url, sep, token = rest.partition("|")
    label = label.strip()
    base_url = base_url.strip().rstrip("/")
    token = token.strip() if sep else ""
    if not label or not base_url:
        raise ValueError(f"peer spec needs label and base_url: {spec!r}")
    return Peer(label=label, base_url=base_url, token=token or get_api_key())


def parse_peers(specs: list[str]) -> list[Peer]:
    """Parse the configured peer specs, skipping (and logging) malformed ones."""
    peers: list[Peer] = []
    for spec in specs:
        try:
            peers.append(parse_peer(spec))
        except ValueError as e:
            log.warning("sync.bad_peer_spec", metadata={"error": str(e)})
    return peers


async def _get_rows(peer: Peer, path: str, params: dict, *, key: str) -> list[dict] | None:
    """GET a peer export endpoint and return its ``key`` array, or None on any
    failure (unreachable / disabled / auth / bad body) — the caller skips the
    stream this tick and retries next time from the same cursor.
    """
    query = urlencode({k: v for k, v in params.items() if v is not None})
    full = f"{path}?{query}" if query else path
    try:
        status, body = await ollama.acurl_request(
            "GET", peer.base_url, full,
            headers={"X-API-Key": peer.token},
            timeout=_PULL_TIMEOUT_S,
        )
    except OSError as e:
        log.warning("sync.peer_unreachable",
                    metadata={"peer": peer.label, "error": str(e)})
        return None
    if status == 404:
        # Peer has sync disabled — quietly skip (not an error on this side).
        return None
    if status != 200:
        log.warning("sync.peer_bad_status",
                    metadata={"peer": peer.label, "status": status})
        return None
    try:
        return json.loads(body).get(key, [])
    except (ValueError, AttributeError):
        log.warning("sync.peer_bad_body", metadata={"peer": peer.label})
        return None


async def _pull_events(peer: Peer, limit: int) -> int:
    """Pull + merge one agent_events batch from ``peer``; advance its cursor."""
    cursor = await asyncio.to_thread(store.get_sync_cursor, peer.label, "events")
    rows = await _get_rows(peer, "/api/sync/events", {"since": cursor, "limit": limit},
                           key="events")
    if not rows:
        return 0
    # sync_pull_events wants a fetch(since, limit); we've already fetched, so
    # hand it the batch and let it import + compute the new cursor. Runs in a
    # thread because the merge does blocking psycopg work.
    n, new_cursor = await asyncio.to_thread(
        node_sync.sync_pull_events, lambda _s, _l: rows, cursor, limit
    )
    if new_cursor and new_cursor != cursor:
        await asyncio.to_thread(store.set_sync_cursor, peer.label, "events", new_cursor)
    return n


async def _pull_owner_rows(peer: Peer, limit: int) -> int:
    """Pull + merge one owner-keyed batch from ``peer``; advance its cursor."""
    cur = await asyncio.to_thread(store.get_sync_cursor, peer.label, "owner_rows")
    since_ms = int(cur) if cur else 0
    items = await _get_rows(peer, "/api/sync/owner-rows",
                            {"since": since_ms, "limit": limit}, key="rows")
    if not items:
        return 0
    n, new_ms = await asyncio.to_thread(
        node_sync.sync_pull_owner_rows, lambda _s, _l: items, since_ms, limit
    )
    if new_ms is not None and str(new_ms) != (cur or ""):
        await asyncio.to_thread(store.set_sync_cursor, peer.label, "owner_rows", str(new_ms))
    return n


async def _pull_peer(peer: Peer) -> None:
    """Pull both streams from one peer. Isolated so a failing peer is contained."""
    try:
        events = await _pull_events(peer, settings.sync.batch_limit)
        owner = await _pull_owner_rows(peer, settings.sync.batch_limit)
        if events or owner:
            log.info("sync.pulled",
                     metadata={"peer": peer.label, "events": events, "owner_rows": owner})
    except Exception as e:  # never let one peer kill the tick
        log.warning("sync.peer_failed", metadata={"peer": peer.label, "error": str(e)})


async def pull_loop() -> None:
    """Background tick: every ``interval_secs`` pull each configured peer.

    Started by the lifespan only when sync is enabled with peers. Runs until the
    task is cancelled at shutdown.
    """
    peers = parse_peers(settings.sync.peers)
    if not peers:
        log.warning("sync.no_valid_peers")
        return
    log.info("sync.pull_loop_started",
             metadata={"peers": [p.label for p in peers],
                       "interval_s": settings.sync.interval_secs})
    while True:
        try:
            await asyncio.sleep(settings.sync.interval_secs)
            for peer in peers:
                await _pull_peer(peer)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # keep ticking through unexpected errors
            log.warning("sync.tick_error", metadata={"error": str(e)})
