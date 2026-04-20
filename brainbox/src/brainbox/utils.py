"""Shared utility functions."""

from __future__ import annotations

import time


def _now_ms() -> int:
    """Current epoch time in milliseconds."""
    return int(time.time() * 1000)
