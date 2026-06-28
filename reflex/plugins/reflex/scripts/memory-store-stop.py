#!/usr/bin/env python3
"""
Stop-hook closeout enforcement for the "store what you learned" guardrail.

If the session has unstored web research (web events not yet ingested
via brain_perceive / brain_learn), warn or block and tell Claude to
ingest it before stopping.

State file:   ${TMPDIR:-/tmp}/reflex-memory-state/{session_id}.json

Toggle: REFLEX_MEMORY_ENFORCE = hard | soft | off (default soft).
        Set to "off" → shell wrapper exits 0 before invoking python.

Fail open on any error.
"""

import json
import os
import sys
from pathlib import Path


def state_path(session_id: str) -> Path:
    d = Path(os.environ.get("TMPDIR", "/tmp")) / "reflex-memory-state"
    d.mkdir(exist_ok=True)
    return d / f"{session_id}.json"


def emit(payload: dict) -> None:
    print(json.dumps(payload))


def deny(message: str) -> None:
    # Stop hook output schema differs from PreToolUse:
    # there's no hookSpecificOutput variant for Stop. Use top-level
    # decision: "block" + reason to prevent session-end continuation.
    emit({
        "decision": "block",
        "reason": message,
    })


def warn(message: str) -> None:
    emit({"systemMessage": message})


def main() -> None:
    try:
        raw = sys.stdin.read().strip()
        data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, OSError):
        sys.exit(0)  # fail open

    session_id = data.get("session_id") or "default"
    fp = state_path(session_id)

    if not fp.exists():
        sys.exit(0)

    try:
        state = json.loads(fp.read_text())
    except (OSError, json.JSONDecodeError):
        sys.exit(0)

    if not state.get("pending"):
        sys.exit(0)

    snippet = state.get("last_query") or state.get("last_url") or "(no details)"
    evidence = f"- {snippet}"

    msg = (
        "🧠 Memory-store guardrail: this session researched something the long-term\n"
        "memory doesn't know about yet. Ingest it before stopping.\n\n"
        f"Unstored web activity:\n{evidence}\n\n"
        "Ingest the findings into phantom-brain (synthesis runs automatically daemon-side):\n"
        "  - Gathered web content:\n"
        "      mcp__phantom-brain__brain_perceive\n"
        '        title: "<short title>"\n'
        '        body:  "<synthesized findings>"\n'
        '        url:   "<source>"\n\n'
        "  - Or, for content you curated/trust:\n"
        "      mcp__phantom-brain__brain_learn\n\n"
        "Toggle: set REFLEX_MEMORY_ENFORCE=soft for warnings only, or =off to disable."
    )

    mode = os.environ.get("REFLEX_MEMORY_ENFORCE", "soft").lower()
    if mode == "hard":
        deny(msg)
    else:
        warn(msg)


if __name__ == "__main__":
    main()
