#!/usr/bin/env python3
"""
Memory-first PreToolUse hook for Codex.

Blocks web_search/web_fetch if the obsidian-second-brain memory hasn't been
checked recently in this session. Codex sees the block reason, calls
memory_search, then retries the web search.

Companion hook (memory-first-flag.py) sets the session flag after
memory_search completes, allowing the next web search through.

Exit codes:
  0 = Allow (no output needed)
  2 = Deny (Codex convention for blocking a tool call)

Flag TTL: 5 minutes. One web search allowed per memory check.
"""

import json
import os
import sys
import time
from pathlib import Path

FLAG_TTL = 300  # seconds


def flag_path(session_id: str) -> Path:
    d = Path(os.environ.get("TMPDIR", "/tmp")) / "codex-memory-flags"
    d.mkdir(exist_ok=True)
    return d / f"{session_id}.memory_checked"


def main():
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            sys.exit(0)
        data = json.loads(raw)
    except Exception:
        sys.exit(0)  # fail open

    session_id = data.get("session_id") or "default"
    tool_input = data.get("tool_input") or {}
    query = tool_input.get("query") or tool_input.get("url") or "this topic"

    fp = flag_path(session_id)

    # If flag exists and is fresh, allow the search and consume the flag
    if fp.exists():
        try:
            age = time.time() - fp.stat().st_mtime
            if age < FLAG_TTL:
                fp.unlink()
                sys.exit(0)  # allow
        except OSError:
            sys.exit(0)  # fail open

    # No valid flag — block and tell Codex to check memory first
    output = {
        "decision": "deny",
        "systemMessage": "\n".join([
            "Memory-first: check Obsidian second brain before searching the web.",
            "",
            f'Call mcp__obsidian-second-brain__memory_search with query="{query}"',
            "If no relevant memories exist, you may then proceed with the web search.",
        ]),
    }
    print(json.dumps(output))
    sys.exit(2)  # Codex deny exit code


if __name__ == "__main__":
    main()
