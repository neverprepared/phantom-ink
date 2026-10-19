"""Stage C -- apply a validated verdict to a system of record.

Nothing here reads the original capture prose for control purposes: the title,
date, priority and tags all come from a Verdict that verdict.py already
range-checked. The body is carried through as note text only.

Three targets, one per queue:
  reminders -> Apple Reminders, via the macos-mcp MCP server
  todo      -> the todo vault (verbatim, kind=todo)
  ideas     -> the memory vault, plus a thin todo POINTER when actionable
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Self

from common import KIND_IDEA, KIND_REMINDER, KIND_TODO, Config, SpoolRecord, log
from mcp_stdio import McpError, McpStdioClient, find_server
from verdict import Verdict

# `pbrainctl client learn` prints: learned "<title>" - sha <64 hex>
SHA_RE = re.compile(r"\bsha\s+([0-9a-f]{64})\b")

PBRAINCTL_TIMEOUT_S = 20


class ApplyError(RuntimeError):
    """The target refused the write. The item stays claimed and is retried."""


@dataclass
class Applied:
    """What stage C did, for the ledger and the timeline."""

    target: str
    result: str
    extra: str = ""


# -- brain vaults ---------------------------------------------------------------


def pbrain_learn(
    cfg: Config, *, token: str, title: str, body: str, tags: list[str], source_url: str = ""
) -> str:
    """Store a note in a brain vault and return its sha.

    The token selects the vault -- CL_TODO_API_TOKEN writes the todo vault,
    CL_BRAIN_API_TOKEN the memory vault. There is deliberately no fallback
    between them: a missing token must fail, never silently write to the wrong
    vault.
    """
    if not token:
        raise ApplyError("no vault token in the environment for this target")
    argv = [
        "pbrainctl",
        "client",
        "learn",
        "--title",
        title,
        "--body-file",
        "-",
        "--token",
        token,
    ]
    if cfg.brain_api:
        argv += ["--api", cfg.brain_api]
    if tags:
        argv += ["--tags", ",".join(tags)]
    if source_url:
        argv += ["--source-url", source_url]

    try:
        proc = subprocess.run(
            argv,
            input=body,
            capture_output=True,
            text=True,
            timeout=PBRAINCTL_TIMEOUT_S,
            check=False,  # returncode handled below, to keep the token out of the error
        )
    except FileNotFoundError as exc:
        raise ApplyError("pbrainctl not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ApplyError(f"pbrainctl learn timed out after {PBRAINCTL_TIMEOUT_S}s") from exc

    if proc.returncode != 0:
        # stderr may echo the argv, which contains the token. Report the exit
        # code and the last line of stdout only.
        tail = (proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
        raise ApplyError(f"pbrainctl learn exited {proc.returncode}: {tail[0][:200]}")

    m = SHA_RE.search(proc.stdout or "")
    if not m:
        raise ApplyError(f"could not parse sha from learn output: {(proc.stdout or '')[:160]!r}")
    return m.group(1)


def _note_body(rec: SpoolRecord, v: Verdict) -> str:
    """The stored note: the capture text, plus provenance as a footer."""
    lines = [rec.body.strip(), "", "---", f"captured: {rec.captured_at or rec.drained_at}"]
    if v.due_iso():
        lines.append(f"due: {v.due_iso()}")
    if v.priority:
        lines.append(f"priority: {v.priority}")
    lines.append(f"source: gdrive inbox ({rec.drive_name})")
    lines.append(f"drive_file_id: {rec.drive_file_id}")
    return "\n".join(lines) + "\n"


def drive_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


# -- the three targets ----------------------------------------------------------


class Reminders:
    """One MCP session for a whole apply run, with the target list guaranteed.

    Two reasons this is a session rather than a call per item. First, starting
    the Swift server costs seconds, and the apply job runs under a 60s timeout.

    Second, and the reason the list is ensured UP FRONT: reminders_add does not
    fail on an unknown list name -- EventKitManager.swift:145 silently falls
    back to defaultCalendarForNewReminders. A reactive "create the list when the
    add errors" approach therefore never fires, and every reminder lands in
    whatever list the user happens to have as default, breaking per-profile
    isolation while reporting success. So: list the lists, create ours if it is
    missing, and verify the name in the confirmation the server returns.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client: McpStdioClient | None = None
        self._ready = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.__exit__()
            self._client = None
            self._ready = False

    def _session(self) -> McpStdioClient:
        if self._client is None:
            client = McpStdioClient(find_server())
            client.__enter__()
            self._client = client
        return self._client

    def _ensure_list(self) -> None:
        """Create the per-profile list unless it already exists."""
        if self._ready:
            return
        want = self.cfg.reminders_list
        client = self._session()
        existing = client.call_tool("reminders_list_lists", {})
        # The tool renders one bullet per list; a name match on a line is enough
        # to avoid a redundant create, and a redundant create is harmless anyway.
        have = any(line.strip().lstrip("•").strip() == want for line in existing.splitlines())
        if not have:
            log(f"creating reminders list {want!r}")
            client.call_tool("reminders_create_list", {"title": want})
        self._ready = True

    def add(self, rec: SpoolRecord, v: Verdict) -> Applied:
        args: dict[str, object] = {
            "title": v.title,
            "list": self.cfg.reminders_list,
            "notes": _note_body(rec, v),
            "url": drive_url(rec.drive_file_id),
        }
        due = v.due_iso()
        if due:
            args["dueDate"] = due
        if v.priority:
            args["priority"] = v.priority

        try:
            self._ensure_list()
            out = self._session().call_tool("reminders_add", args)
        except McpError as exc:
            # Includes a missing EventKit grant, which must be a hard error: a
            # silent no-op there would look exactly like success.
            raise ApplyError(f"reminders_add failed: {exc}") from exc

        text = out.strip()
        if self.cfg.reminders_list not in text:
            # The server reports the list it actually used. If that is not ours,
            # the silent fallback happened and the reminder is in the wrong
            # place -- surface it rather than recording a clean success.
            raise ApplyError(
                f"reminder was not placed in {self.cfg.reminders_list!r}; server said: {text[:160]}"
            )
        return Applied(target="reminder", result=text[:200] or "created")


def apply_reminder(cfg: Config, rec: SpoolRecord, v: Verdict) -> Applied:
    """Create a single Apple reminder in its own session (one-off / tests)."""
    with Reminders(cfg) as reminders:
        return reminders.add(rec, v)


def apply_todo(cfg: Config, rec: SpoolRecord, v: Verdict) -> Applied:
    """Store a todo in the todo vault.

    The vault has no due-date or priority field (app/app_agent_vault.go:21-30),
    so a due date is carried in the note body rather than being dropped.
    """
    sha = pbrain_learn(
        cfg,
        token=cfg.todo_token,
        title=v.title,
        body=_note_body(rec, v),
        tags=sorted({*v.tags, "inbox"}),
        source_url=drive_url(rec.drive_file_id),
    )
    return Applied(target="todo", result=sha)


def apply_idea(cfg: Config, rec: SpoolRecord, v: Verdict) -> Applied:
    """Store an idea as a memory-vault note, with a todo pointer if actionable.

    The note holds the content; the todo is a pointer by sha. Duplicating the
    body into the todo vault would give one item two sources of truth, and
    filling the todo list with un-actionable ideas is how a todo list dies -- so
    the pointer is created only when the item is actually something to do.
    """
    sha = pbrain_learn(
        cfg,
        token=cfg.brain_token,
        title=v.title,
        body=_note_body(rec, v),
        tags=sorted({*v.tags, "idea", "inbox"}),
        source_url=drive_url(rec.drive_file_id),
    )
    if not v.actionable:
        return Applied(target="idea", result=sha)

    pointer_body = "\n".join(
        [
            f"Review the captured idea: {v.title}",
            "",
            v.summary or rec.body.strip()[:300],
            "",
            "---",
            f"idea note: {sha}",
            f"recall: pbrainctl client fetch {sha}",
            f"source: gdrive inbox ({rec.drive_name})",
        ]
    )
    try:
        pointer = pbrain_learn(
            cfg,
            token=cfg.todo_token,
            title=f"Review idea: {v.title}"[:200],
            body=pointer_body + "\n",
            tags=sorted({*v.tags, "idea", "inbox"}),
        )
    except ApplyError as exc:
        # The idea itself is already stored, which is the valuable half. Report
        # the partial outcome rather than failing and re-applying the note.
        return Applied(target="idea", result=sha, extra=f"pointer failed: {exc}")
    return Applied(target="idea", result=sha, extra=f"pointer {pointer}")


APPLIERS = {
    KIND_TODO: apply_todo,
    KIND_IDEA: apply_idea,
}


def apply(
    cfg: Config, rec: SpoolRecord, v: Verdict, *, reminders: Reminders | None = None
) -> Applied:
    """Dispatch a verdict to the target for its KIND.

    The kind comes from the verdict, not from where the file sat: there is one
    flat inbox, and the kind was settled during extraction (or declared by the
    author) and validated against the closed set.

    `reminders` lets the caller share one MCP session across a run; without it,
    a reminder gets its own short-lived session.
    """
    if v.kind == KIND_REMINDER:
        if reminders is not None:
            return reminders.add(rec, v)
        return apply_reminder(cfg, rec, v)
    fn = APPLIERS.get(v.kind)
    if fn is None:
        raise ApplyError(f"no applier for kind {v.kind!r}")
    return fn(cfg, rec, v)
