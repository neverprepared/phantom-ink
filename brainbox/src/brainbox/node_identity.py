"""Node identity and time-sortable IDs for local-first / P2P store operation.

Slice 1 of dropping the centralized Postgres: every row needs a globally unique
identity that will not collide across independently-writing nodes, and (later)
deletes need to become tombstones so a removal survives a merge instead of being
resurrected by a node that never saw it. This module provides the two primitives
that requires:

- ``node_id()`` — a stable, per-machine identifier. Resolved once (env override →
  persisted file → generated-and-persisted) so a given node always stamps its
  rows with the same owner id across restarts. Used to (a) partition ownership of
  owner-keyed tables and (b) break ties in time-ordered ids.
- ``ulid()`` — a 26-char Crockford-base32 ULID: 48 bits of millisecond time +
  80 bits of randomness. Lexicographically sortable by creation time, so an
  append-only log of ULIDs recovers the rough ordering a centralized
  autoincrement ``seq`` used to give us — WITHOUT a global counter only a single
  writer can own.

No third-party dependency: matches the repo convention of stdlib ``uuid`` /
``secrets`` for id generation. Time-sortability (which ``uuid4`` lacks) is the
whole reason we hand-roll rather than reuse ``uuid4`` here.
"""

from __future__ import annotations

import functools
import os
import time
from pathlib import Path

# Crockford base32: no I, L, O, U (avoids transcription ambiguity). Same
# alphabet the ULID spec uses, so these ids are interoperable with any ULID lib.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    """Encode ``value`` as ``length`` Crockford-base32 chars, most-significant
    first (so lexicographic order matches numeric order)."""
    out = bytearray(length)
    for i in range(length - 1, -1, -1):
        out[i] = ord(_CROCKFORD[value & 0x1F])
        value >>= 5
    return out.decode("ascii")


def ulid(ts_ms: int | None = None) -> str:
    """A 26-char time-sortable ULID (10 chars ms-time + 16 chars randomness).

    Pass ``ts_ms`` to stamp with a specific event time (e.g. backfilling an
    existing row from its stored ``ts``) so historical order is preserved;
    otherwise the current wall clock is used.
    """
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits
    return _encode(ts_ms & ((1 << 48) - 1), 10) + _encode(rand, 16)


def _node_id_path() -> Path:
    base = os.environ.get("CL_STATE_DIR")
    root = Path(base) if base else Path.home() / ".config" / "phantom-ink"
    return root / "node_id"


@functools.lru_cache(maxsize=1)
def node_id() -> str:
    """This machine's stable node id.

    Resolution order: ``CL_NODE_ID`` env override → persisted file → generate an
    8-char id and persist it. Cached for the process; if the file cannot be
    written (read-only FS) the generated id is still stable within the process,
    which is enough for a single run and gets re-derived (differently) next boot
    only in that degraded case.
    """
    override = os.environ.get("CL_NODE_ID")
    if override and override.strip():
        return override.strip()

    path = _node_id_path()
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass

    new = _encode(int.from_bytes(os.urandom(5), "big"), 8)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
    except OSError:
        pass
    return new
