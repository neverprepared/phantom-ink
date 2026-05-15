#!/usr/bin/env python3
"""
Stop-hook closeout enforcement for the "store what you learned" guardrail.

If the session has unstored web research (web events since the last
memory_store / memory_update / task_complete), block the stop, surface the
recent activity from memory.db as evidence, and tell Claude to promote
those findings before stopping.

State file:   ${TMPDIR:-/tmp}/reflex-memory-state/{session_id}.json
Activity log: $REFLEX_HOME/memory.db (queried via memory.py recent --hours N)

Toggle: REFLEX_MEMORY_ENFORCE = hard | soft | off (default soft).
        Set to "off" → shell wrapper exits 0 before invoking python.

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
    d = Path(os.environ.get("TMPDIR", "/tmp")) / "reflex-memory-state"
    d.mkdir(exist_ok=True)
    return d / f"{session_id}.json"


def parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def memory_py_path() -> Path:
    return Path(__file__).resolve().parent / "memory.py"


def query_recent_events(since: datetime | None) -> list[dict]:
    """Pull WebSearch / WebFetch events from memory.db since the given time.
    Returns [] on any failure — never block the stop because of a query error.
    """
    fp = memory_py_path()
    if not fp.exists():
        return []

    if since is not None:
        elapsed = datetime.now(timezone.utc) - since
        hours = max(1, int(elapsed.total_seconds() / 3600) + 1)
    else:
        hours = MEMORY_LOOKBACK_HOURS
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
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else ""
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not since_iso or row.get("ts", "") >= since_iso:
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

    last_web_at = parse_iso(state.get("last_web_at", ""))
    events = query_recent_events(last_web_at)

    if events:
        bullets = [format_event(e) for e in events[:MAX_EVENTS_IN_REMINDER]]
        if len(events) > MAX_EVENTS_IN_REMINDER:
            bullets.append(f"  …and {len(events) - MAX_EVENTS_IN_REMINDER} more events")
        evidence = "\n".join(bullets)
    else:
        # State says we have pending web work but the activity log is empty —
        # could be a memory.db / hook ordering issue. Fall back to last_query/url.
        snippet = state.get("last_query") or state.get("last_url") or "(no details)"
        evidence = f"- {snippet}"

    msg = (
        "🧠 Memory-store guardrail: this session researched something the long-term\n"
        "memory doesn't know about yet. Promote it before stopping.\n\n"
        f"Unstored web activity:\n{evidence}\n\n"
        "Pick one path:\n"
        "  - Synthesize and store as a curated memory:\n"
        "      mcp__obsidian-second-brain__memory_store\n"
        '        title:  "<descriptive title>"\n'
        '        content: "<synthesized findings + source links>"\n'
        '        para:   "resources"\n'
        '        tags:   [...]\n\n'
        "  - Or, if a task is open, complete it (auto-promotes findings):\n"
        "      mcp__obsidian-second-brain__task_complete\n\n"
        "Toggle: set REFLEX_MEMORY_ENFORCE=soft for warnings only, or =off to disable."
    )

    mode = os.environ.get("REFLEX_MEMORY_ENFORCE", "soft").lower()
    if mode == "hard":
        deny(msg)
    else:
        warn(msg)


if __name__ == "__main__":
    main()
