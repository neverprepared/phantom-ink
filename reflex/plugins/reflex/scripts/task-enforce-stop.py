#!/usr/bin/env python3
"""
Stop-hook closeout enforcement for the obsidian-second-brain task lifecycle.

When Claude Code is about to end the session, check whether an active task
is still uncompleted. If so, the user's mental model says: storage is the
output of finished work. Block (or warn) the stop, surface the recent
WebSearch/WebFetch activity from memory.db as task evidence, and tell
Claude to attach those events as findings via task_update before
task_complete.

State file:   ${TMPDIR:-/tmp}/reflex-task-state/{session_id}.active_task.json
Activity log: $REFLEX_HOME/memory.db (queried via memory.py recent --hours N)

Toggle: REFLEX_TASK_ENFORCE = hard | soft | off (default hard).
        Set to "off" → shell wrapper exits 0 before invoking python.

Behavior:
  no state file       → exit 0 (no active task to close)
  state, dirty=False  → exit 0 (task started but no work logged; treat as
                                empty, let Claude decide whether to complete)
  state, dirty=True   → emit systemMessage with activity evidence;
                        in hard mode also deny the stop.

Fail open on any error.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


MAX_EVENTS_IN_REMINDER = 25
MEMORY_LOOKBACK_HOURS = 24


def state_path(session_id: str) -> Path:
    d = Path(os.environ.get("TMPDIR", "/tmp")) / "reflex-task-state"
    d.mkdir(exist_ok=True)
    return d / f"{session_id}.active_task.json"


def parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def memory_py_path() -> Path:
    """The activity-log CLI lives next to this script."""
    return Path(__file__).resolve().parent / "memory.py"


def query_recent_events(started_at: datetime) -> list[dict]:
    """
    Pull WebSearch / WebFetch events from memory.db that occurred during the
    task's lifetime. Uses memory.py's --hours window (rounded up) and then
    filters in-process by ts >= started_at.

    Returns [] on any failure — never block the stop because of a query error.
    """
    fp = memory_py_path()
    if not fp.exists():
        return []

    elapsed = datetime.now(timezone.utc) - started_at
    hours = max(1, int(elapsed.total_seconds() / 3600) + 1)
    if hours > MEMORY_LOOKBACK_HOURS:
        hours = MEMORY_LOOKBACK_HOURS

    try:
        result = subprocess.run(
            [sys.executable, str(fp), "recent", "--hours", str(hours)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    events: list[dict] = []
    started_iso = started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("ts", "") >= started_iso:
            events.append(row)
    return events


def format_event(row: dict) -> str:
    action = row.get("action_type", "?")
    if action == "web_search":
        q = row.get("query_text") or "(no query)"
        return f"- searched: {q}"
    if action == "web_fetch":
        label = row.get("title") or row.get("url") or "(no url)"
        return f"- fetched:  {label}"
    return f"- {action}: {row.get('query_text') or row.get('url') or ''}"


def emit(payload: dict) -> None:
    print(json.dumps(payload))


def deny(message: str) -> None:
    emit({
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "permissionDecision": "deny",
        },
        "systemMessage": message,
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

    if not state.get("dirty"):
        # Task started but nothing logged. Don't force a complete on emptiness.
        sys.exit(0)

    task_id = state.get("task_id", "<unknown>")
    started_at_str = state.get("started_at", "")
    started_at = parse_iso(started_at_str)

    events = query_recent_events(started_at) if started_at else []

    if events:
        bullets = [format_event(e) for e in events[:MAX_EVENTS_IN_REMINDER]]
        if len(events) > MAX_EVENTS_IN_REMINDER:
            bullets.append(f"  …and {len(events) - MAX_EVENTS_IN_REMINDER} more events")
        evidence = "\n".join(bullets)
    else:
        evidence = "  (no web activity logged for this task window)"

    msg = (
        f"🧠 Task `{task_id}` is unfinished — close it before stopping.\n\n"
        f"Started: {started_at_str}\n"
        f"Activity logged from this session:\n{evidence}\n\n"
        "Recommended next steps:\n"
        "  1. Call mcp__obsidian-second-brain__task_update with a finding for each\n"
        "     meaningful piece of work above (importance: medium or high so it\n"
        "     promotes to long-term memory).\n"
        "  2. Call mcp__obsidian-second-brain__task_complete to close the loop —\n"
        "     this auto-promotes medium/high findings to the second-brain vault.\n\n"
        "Toggle: set REFLEX_TASK_ENFORCE=soft for warnings only, or =off to disable."
    )

    mode = os.environ.get("REFLEX_TASK_ENFORCE", "hard").lower()
    if mode == "soft":
        warn(msg)
    else:
        deny(msg)


if __name__ == "__main__":
    main()
