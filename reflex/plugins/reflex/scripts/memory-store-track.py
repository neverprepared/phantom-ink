#!/usr/bin/env python3
"""
PostToolUse session-state writer for the "store what you learned" guardrail.

The premise (from the user's mental model of memory):
  unknown → recall → search → immediate → task done → long-term

The "task done → long-term" arrow is automatic if Claude calls memory_store /
memory_update / task_complete. Without one of those, web research is captured
in the activity log but never promoted to curated memory. This hook tracks
whether the session has unstored web research so the Stop hook can gate
session end on it.

State file:   ${TMPDIR:-/tmp}/reflex-memory-state/{session_id}.json
Schema:       {
                "pending": bool,         # web activity since last store/update/complete
                "last_web_at": "<ISO>",
                "last_query": "<str>",   # for the Stop hook's reminder context
                "last_url": "<str>"
              }

Behavior:
  WebSearch / WebFetch → set pending=true, capture query/url
  memory_store / memory_update / task_complete → clear pending

Fail open on any error — never block tool flow because of bookkeeping.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


CLEAR_TOOLS = {
    "mcp__obsidian-second-brain__memory_store",
    "mcp__obsidian-second-brain__memory_update",
    "mcp__obsidian-second-brain__task_complete",
}


def state_dir() -> Path:
    d = Path(os.environ.get("TMPDIR", "/tmp")) / "reflex-memory-state"
    d.mkdir(exist_ok=True)
    return d


def state_path(session_id: str) -> Path:
    return state_dir() / f"{session_id}.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_state(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def handle_web_event(session_id: str, data: dict) -> None:
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    state = read_state(state_path(session_id))
    state["pending"] = True
    state["last_web_at"] = now_iso()
    if tool_name == "WebSearch":
        q = tool_input.get("query")
        if q:
            state["last_query"] = q
    elif tool_name == "WebFetch":
        u = tool_input.get("url")
        if u:
            state["last_url"] = u
    write_state(state_path(session_id), state)


def handle_clear_event(session_id: str) -> None:
    state = read_state(state_path(session_id))
    if not state:
        return
    state["pending"] = False
    write_state(state_path(session_id), state)


def main() -> None:
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            return
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return  # fail open

    tool_name = data.get("tool_name", "")
    session_id = data.get("session_id") or "default"

    try:
        if tool_name in ("WebSearch", "WebFetch"):
            handle_web_event(session_id, data)
        elif tool_name in CLEAR_TOOLS:
            handle_clear_event(session_id)
    except Exception as e:
        sys.stderr.write(f"memory-store-track: {e}\n")  # never block


if __name__ == "__main__":
    main()
