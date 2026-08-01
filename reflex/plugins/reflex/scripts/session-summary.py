#!/usr/bin/env python3
"""SessionEnd emitter — post a `session.summary` event describing what the agent
did this session.

Three homes, per the contract (phantom-events:session-summary/v1):
  - NARRATIVE  → envelope title/description, taken from the agent's own final
                 message (it's the authority on what it did).
  - FACTS      → metadata, MACHINE-extracted (transcript usage/tools, git diff),
                 never agent-claimed, so consumers can trust and filter them.
  - EVIDENCE   → the transcript + diff, uploaded as artifacts and referenced by
                 handle (see maybe_upload_artifacts); never inlined.

Posts to the router's `/api/agent_events` (which forwards to the bus). Fails open
on ANY error — a summary must never break session teardown. Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime

TIMEOUT = 4.0  # keep total well under the hook's wall-clock budget
_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "on", "yes")


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def _run(args: list[str], cwd: str) -> str:
    try:
        out = subprocess.run(
            args, cwd=cwd or None, capture_output=True, text=True, timeout=4.0
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _parse_ts(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def read_transcript(path: str) -> dict:
    """Pull narrative + machine facts from the Claude Code transcript JSONL."""
    facts: dict = {"tools_used": [], "tokens": 0}
    narrative = ""
    tools: set[str] = set()
    files: set[str] = set()
    model = ""
    tokens = 0
    first_ts = last_ts = None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(ev.get("timestamp", ""))
                if ts:
                    first_ts = first_ts or ts
                    last_ts = ts
                msg = ev.get("message") or {}
                if ev.get("type") == "assistant" and isinstance(msg, dict):
                    model = msg.get("model") or model
                    usage = msg.get("usage") or {}
                    tokens += (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
                    for block in msg.get("content") or []:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and block.get("text", "").strip():
                            narrative = block["text"].strip()  # keep the LAST one
                        elif block.get("type") == "tool_use":
                            name = block.get("name", "")
                            if name:
                                tools.add(name)
                            if name in _EDIT_TOOLS:
                                fp = (block.get("input") or {}).get("file_path")
                                if fp:
                                    files.add(fp)
    except OSError:
        pass

    if tools:
        facts["tools_used"] = sorted(tools)
    if files:
        facts["files_changed"] = len(files)
    if model:
        facts["model"] = model
    if tokens:
        facts["tokens"] = tokens
    if first_ts and last_ts:
        facts["duration_ms"] = max(0, int((last_ts - first_ts).total_seconds() * 1000))
    return {"narrative": narrative, "facts": facts}


def git_facts(cwd: str) -> dict:
    """Machine facts from git (working-tree delta + branch/commits). Approximate
    session scope — line counts are the uncommitted diff; files_changed from the
    transcript is the accurate per-session count."""
    facts: dict = {}
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if not branch:
        return facts  # not a repo
    facts["branch"] = branch
    short = _run(["git", "diff", "--shortstat"], cwd)
    if short:
        import re
        add = re.search(r"(\d+) insertion", short)
        dele = re.search(r"(\d+) deletion", short)
        if add:
            facts["additions"] = int(add.group(1))
        if dele:
            facts["deletions"] = int(dele.group(1))
    log = _run(["git", "log", "--oneline", "-n", "5", "--no-merges"], cwd)
    if log:
        commits = []
        for ln in log.splitlines():
            sha, _, subject = ln.partition(" ")
            commits.append({"sha": sha, "subject": subject})
        facts["commits"] = commits
    return facts


def post_event(api: str, key: str, envelope: dict) -> bool:
    body = json.dumps({"events": [envelope]}).encode()
    req = urllib.request.Request(
        api.rstrip("/") + "/api/agent_events",
        data=body,
        headers={"Content-Type": "application/json", "X-API-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        print(f"session-summary: post failed: {exc}", file=sys.stderr)
        return False


def main() -> None:
    try:
        raw = sys.stdin.read().strip()
        data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, OSError):
        return  # fail open

    api = _env("PHANTOM_API_URL", "CL_PUBLIC_URL", default="http://127.0.0.1:9910")
    key = _env("PHANTOM_API_KEY", "CL_API_KEY")
    if not key:
        return  # nothing to auth with — silent no-op

    session_id = data.get("session_id") or "unknown"
    transcript_path = data.get("transcript_path") or ""
    cwd = data.get("cwd") or os.getcwd()
    profile = _env("WORKSPACE_PROFILE", "CL_WORKSPACE_PROFILE", default="default")

    parsed = read_transcript(transcript_path) if transcript_path else {"narrative": "", "facts": {}}
    facts = {**parsed["facts"], **git_facts(cwd)}
    narrative = parsed["narrative"] or "(no final summary captured)"

    # If BOTH the narrative and every fact came up empty, the transcript format
    # likely drifted (Claude Code's transcript shape is internal/versioned) — make
    # that visible rather than silently posting a hollow summary.
    if not parsed["narrative"] and not parsed["facts"]:
        print("session-summary: transcript parse yielded nothing — "
              f"format drift? (path={transcript_path})", file=sys.stderr)

    # Evidence upload (Phase 3) is OPT-IN: full transcripts and diffs can contain
    # secrets, so uploading them to the object store is off unless the operator
    # explicitly accepts that (REFLEX_SESSION_EVIDENCE=on). The summary event
    # (narrative + machine facts) always posts regardless.
    if _truthy(os.environ.get("REFLEX_SESSION_EVIDENCE", "off")):
        try:
            from session_artifacts import maybe_upload_artifacts

            arts = maybe_upload_artifacts(api, key, profile, session_id, transcript_path, cwd)
            if arts:
                facts["artifacts"] = arts
        except Exception as exc:
            print(f"session-summary: artifact upload skipped: {exc}", file=sys.stderr)

    title = narrative.splitlines()[0][:120] if narrative else "Session ended"
    envelope = {
        "id": f"session:{session_id}:summary",
        "kind": "event",
        "type": "session.summary",
        "title": title,
        "description": narrative[:4000],
        "status": "done",
        "workspace": profile,
        "parent_id": f"session:{session_id}",
        "tags": ["session", "summary"],
        "metadata": facts,
        "outcome": {"ok": True, "actor": "system:session"},
    }
    post_event(api, key, envelope)


if __name__ == "__main__":
    main()
