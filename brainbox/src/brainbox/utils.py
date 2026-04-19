"""Shared utility helpers."""
from __future__ import annotations
import time


def now_ms() -> int:
    """Current UTC time as Unix milliseconds."""
    return int(time.time() * 1000)


def iso_now() -> str:
    """Current UTC time as ISO-8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
