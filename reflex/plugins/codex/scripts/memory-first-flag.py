#!/usr/bin/env python3
"""
PostToolUse hook for Codex — sets the memory-checked flag after
mcp__obsidian-second-brain__memory_search completes.

This unlocks the next web_search/web_fetch for this session.
"""

import json
import os
import sys
from pathlib import Path


def main():
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            sys.exit(0)
        data = json.loads(raw)
    except Exception:
        sys.exit(0)

    session_id = data.get("session_id") or "default"

    flag_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "codex-memory-flags"
    flag_dir.mkdir(exist_ok=True)
    (flag_dir / f"{session_id}.memory_checked").touch()

    sys.exit(0)


if __name__ == "__main__":
    main()
