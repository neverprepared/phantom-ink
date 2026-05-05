#!/usr/bin/env python3
"""
PreToolUse enforcement for the obsidian-second-brain task lifecycle.

Reads the session-state file written by task-enforce-set, and either:
  - allows the call (active task exists, recent enough)
  - emits a soft systemMessage reminder (active but stale, soft mode)
  - denies the call with permissionDecision: deny (no active task, hard mode)

Toggle: REFLEX_TASK_ENFORCE = hard | soft | off (default hard).
        Set to "off" → shell wrapper exits 0 before invoking python.

Exempt tools (always allowed, never trigger enforcement):
  - mcp__obsidian-second-brain__task_*    (the lifecycle tools themselves)
  - mcp__obsidian-second-brain__memory_search / memory_recall
    (Loop A's "recall existing knowledge first" path needs to run unblocked)

Stale threshold: 30 minutes since last task_update. Past that, the hook
emits a soft reminder to call task_update — never blocks (the task IS active).

Fail open on any error — never block due to bookkeeping mistakes.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


STALE_AFTER_SECONDS = 30 * 60  # 30 minutes

EXEMPT_TOOLS = {
    "mcp__obsidian-second-brain__task_start",
    "mcp__obsidian-second-brain__task_update",
    "mcp__obsidian-second-brain__task_complete",
    "mcp__obsidian-second-brain__task_get",
    "mcp__obsidian-second-brain__memory_search",
    "mcp__obsidian-second-brain__memory_recall",
}


def state_path(session_id: str) -> Path:
    d = Path(os.environ.get("TMPDIR", "/tmp")) / "reflex-task-state"
    d.mkdir(exist_ok=True)
    return d / f"{session_id}.active_task.json"


def parse_iso(ts: str) -> float | None:
    try:
        # Z-suffixed ISO 8601 (what task-enforce-set writes)
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def emit(payload: dict) -> None:
    print(json.dumps(payload))


def deny(message: str) -> None:
    emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
        },
        "systemMessage": message,
    })


def warn(message: str) -> None:
    # No permissionDecision = allow; systemMessage gets injected.
    emit({"systemMessage": message})


def main() -> None:
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            sys.exit(0)
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        sys.exit(0)  # fail open

    tool_name = data.get("tool_name", "")
    if tool_name in EXEMPT_TOOLS:
        sys.exit(0)  # exempt — let it through

    session_id = data.get("session_id") or "default"
    fp = state_path(session_id)
    mode = os.environ.get("REFLEX_TASK_ENFORCE", "hard").lower()

    if not fp.exists():
        # No active task. Hard mode blocks; soft mode warns.
        msg = (
            "🧠 Task-loop guardrail: no active second-brain task for this session.\n\n"
            f"Before calling `{tool_name}`, define what you're working on so the\n"
            "session's findings can be promoted to long-term memory at the end:\n\n"
            "  mcp__obsidian-second-brain__task_start\n"
            '    goal: "<one-line goal of this work>"\n\n'
            "Exempt: memory_search, memory_recall, and the task_* tools themselves.\n"
            "Toggle: set REFLEX_TASK_ENFORCE=soft for warnings only, or =off to disable."
        )
        if mode == "soft":
            warn(msg)
        else:
            deny(msg)
        sys.exit(0)

    # Active task exists — check freshness.
    try:
        state = json.loads(fp.read_text())
    except (OSError, json.JSONDecodeError):
        sys.exit(0)  # fail open

    last_updated = parse_iso(state.get("last_updated_at", "")) or 0
    age = time.time() - last_updated

    if age > STALE_AFTER_SECONDS:
        task_id = state.get("task_id", "<unknown>")
        minutes = int(age / 60)
        warn(
            f"🧠 Task `{task_id}` hasn't been updated in {minutes} minutes.\n"
            "Consider calling mcp__obsidian-second-brain__task_update with a finding\n"
            "so the session's working memory stays current.\n"
            "(This is a reminder, not a block.)"
        )
        sys.exit(0)

    # Fresh active task — allow with no message.
    sys.exit(0)


if __name__ == "__main__":
    main()
