#!/usr/bin/env python3
"""
PostToolUse session-state writer for the obsidian-second-brain task lifecycle.

Triggered by hooks.json with matcher
  mcp__obsidian-second-brain__task_start|task_update|task_complete

State file:   ${TMPDIR:-/tmp}/reflex-task-state/{session_id}.active_task.json
Schema:       { "task_id": str, "started_at": ISO, "dirty": bool, "last_updated_at": ISO }

Behavior:
  task_start    → parse task_id from response text, write fresh state with dirty=false
  task_update   → bump last_updated_at, set dirty=true
  task_complete → delete the state file (lifecycle closed)

Fail open on any error — never block the user's tool flow because of bookkeeping.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def state_dir() -> Path:
    d = Path(os.environ.get("TMPDIR", "/tmp")) / "reflex-task-state"
    d.mkdir(exist_ok=True)
    return d


def state_path(session_id: str) -> Path:
    return state_dir() / f"{session_id}.active_task.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def response_text(data: dict) -> str:
    """Extract the human-readable text from a tool response payload."""
    resp = data.get("tool_response") or {}
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        content = resp.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                return first.get("text", "") or ""
    return ""


# Task IDs from the MCP look like: task_<unix-seconds>_<6-char-suffix>
TASK_ID_RE = re.compile(r"task_\d+_[a-z0-9]+")


def parse_task_id(text: str) -> str | None:
    m = TASK_ID_RE.search(text)
    return m.group(0) if m else None


def write_state(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def handle_task_start(session_id: str, data: dict) -> None:
    text = response_text(data)
    task_id = parse_task_id(text)
    if not task_id:
        # Response shape unexpected — log and bail without writing state.
        sys.stderr.write("task-enforce-set: task_start response missing task_id\n")
        return
    now = now_iso()
    write_state(state_path(session_id), {
        "task_id": task_id,
        "started_at": now,
        "dirty": False,
        "last_updated_at": now,
    })


def handle_task_update(session_id: str) -> None:
    fp = state_path(session_id)
    if not fp.exists():
        # Update without a known task — possibly a stale session, possibly
        # task_start failed silently. Don't synthesize state from nothing.
        return
    try:
        state = json.loads(fp.read_text())
    except (OSError, json.JSONDecodeError):
        return
    state["dirty"] = True
    state["last_updated_at"] = now_iso()
    write_state(fp, state)


def handle_task_complete(session_id: str) -> None:
    fp = state_path(session_id)
    if fp.exists():
        try:
            fp.unlink()
        except OSError:
            pass


HANDLERS = {
    "mcp__obsidian-second-brain__task_start":    lambda sid, d: handle_task_start(sid, d),
    "mcp__obsidian-second-brain__task_update":   lambda sid, d: handle_task_update(sid),
    "mcp__obsidian-second-brain__task_complete": lambda sid, d: handle_task_complete(sid),
}


def main() -> None:
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            return
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return  # fail open

    tool_name = data.get("tool_name", "")
    handler = HANDLERS.get(tool_name)
    if not handler:
        return

    session_id = data.get("session_id") or "default"

    try:
        handler(session_id, data)
    except Exception as e:
        sys.stderr.write(f"task-enforce-set: {e}\n")  # never block


if __name__ == "__main__":
    main()
