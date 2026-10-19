"""Stage C -- applying a verdict. The MCP server and pbrainctl are both stubbed."""

from __future__ import annotations

import datetime as dt

import pytest

import targets as tg
from common import Config, SpoolRecord, load_config
from verdict import Verdict


@pytest.fixture
def cfg(tmp_path) -> Config:
    return load_config(
        "personal",
        env={
            "WORKSPACE_HOME": str(tmp_path),
            "CL_BRAIN_API_TOKEN": "brain-token",
            "CL_TODO_API_TOKEN": "todo-token",
        },
    )


def rec(queue="reminders") -> SpoolRecord:
    return SpoolRecord(
        item_id="aaaa0000",
        profile="personal",
        queue=queue,
        drive_file_id="fid",
        drive_name="note.md",
        mime="text/markdown",
        captured_at="2026-10-18T12:00:00Z",
        drained_at="2026-10-18T12:00:01Z",
        body="the capture text",
    )


def verdict(**over) -> Verdict:
    base = {
        "item_id": "aaaa0000",
        "title": "Do the thing",
        "due_at": dt.datetime(2026, 11, 14, 9, 0, tzinfo=dt.UTC),
        "priority": "high",
        "tags": ["infra"],
        "actionable": True,
        "summary": "A summary.",
    }
    base.update(over)
    return Verdict(**base)


class FakeClient:
    """Stands in for an McpStdioClient session."""

    def __init__(self, lists="• Reminders\n• INBOX\n", add_reply=None, fail=None):
        self.lists = lists
        self.add_reply = add_reply
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.fail is not None:
            raise self.fail
        if name == "reminders_list_lists":
            return self.lists
        if name == "reminders_create_list":
            self.lists += f"• {arguments['title']}\n"
            return f"Created list {arguments['title']}"
        if name == "reminders_add":
            if self.add_reply is not None:
                return self.add_reply
            return f'Created reminder "{arguments["title"]}" in list "{arguments["list"]}"'
        raise AssertionError(f"unexpected tool {name}")

    def __exit__(self, *_):
        return False

    def tools_called(self):
        return [name for name, _ in self.calls]


def wire(monkeypatch, client: FakeClient) -> None:
    monkeypatch.setattr(tg, "find_server", lambda *_a, **_k: "/fake/macos-mcp")
    monkeypatch.setattr(tg.Reminders, "_session", lambda self: client)


# -- the silent-fallback guard --------------------------------------------------


def test_creates_the_profile_list_when_missing(cfg, monkeypatch):
    """reminders_add silently uses the default list for an unknown name
    (EventKitManager.swift:145), so the list must be ensured up front."""
    client = FakeClient(lists="• Reminders\n• INBOX\n")
    wire(monkeypatch, client)
    with tg.Reminders(cfg) as r:
        r.add(rec(), verdict())
    assert "reminders_create_list" in client.tools_called()
    created = next(a for n, a in client.calls if n == "reminders_create_list")
    assert created["title"] == "phantom-personal"


def test_does_not_recreate_an_existing_list(cfg, monkeypatch):
    client = FakeClient(lists="• Reminders\n• phantom-personal\n")
    wire(monkeypatch, client)
    with tg.Reminders(cfg) as r:
        r.add(rec(), verdict())
    assert "reminders_create_list" not in client.tools_called()


def test_rejects_a_reminder_placed_in_the_wrong_list(cfg, monkeypatch):
    """The silent fallback must surface as an error, not a clean success."""
    client = FakeClient(
        lists="• phantom-personal\n",
        add_reply='Created reminder "Do the thing" in list "INBOX"',
    )
    wire(monkeypatch, client)
    with pytest.raises(tg.ApplyError, match="not placed in"), tg.Reminders(cfg) as r:
        r.add(rec(), verdict())


def test_accepts_a_reminder_in_the_right_list(cfg, monkeypatch):
    client = FakeClient(lists="• phantom-personal\n")
    wire(monkeypatch, client)
    with tg.Reminders(cfg) as r:
        out = r.add(rec(), verdict())
    assert out.target == "reminder"
    assert "phantom-personal" in out.result


def test_ensures_the_list_only_once_per_session(cfg, monkeypatch):
    client = FakeClient(lists="• phantom-personal\n")
    wire(monkeypatch, client)
    with tg.Reminders(cfg) as r:
        r.add(rec(), verdict())
        r.add(rec(), verdict(title="Another"))
    assert client.tools_called().count("reminders_list_lists") == 1


def test_mcp_failure_becomes_an_apply_error(cfg, monkeypatch):
    """A missing EventKit grant must be hard, never a silent no-op."""
    client = FakeClient(fail=tg.McpError("EventKit access denied"))
    wire(monkeypatch, client)
    with pytest.raises(tg.ApplyError, match="reminders_add failed"), tg.Reminders(cfg) as r:
        r.add(rec(), verdict())


def test_passes_due_date_and_priority_through(cfg, monkeypatch):
    client = FakeClient(lists="• phantom-personal\n")
    wire(monkeypatch, client)
    with tg.Reminders(cfg) as r:
        r.add(rec(), verdict())
    args = next(a for n, a in client.calls if n == "reminders_add")
    assert args["dueDate"].startswith("2026-11-14T09:00")
    assert args["priority"] == "high"


def test_omits_due_date_when_absent(cfg, monkeypatch):
    client = FakeClient(lists="• phantom-personal\n")
    wire(monkeypatch, client)
    with tg.Reminders(cfg) as r:
        r.add(rec(), verdict(due_at=None, priority=None))
    args = next(a for n, a in client.calls if n == "reminders_add")
    assert "dueDate" not in args and "priority" not in args


# -- vault targets --------------------------------------------------------------


def stub_learn(monkeypatch, shas: list[str]):
    """Return successive shas, recording each learn call."""
    seen: list[dict] = []
    pool = list(shas)

    def fake(cfg, *, token, title, body, tags, source_url=""):
        seen.append({"token": token, "title": title, "body": body, "tags": tags, "url": source_url})
        return pool.pop(0)

    monkeypatch.setattr(tg, "pbrain_learn", fake)
    return seen


def test_todo_writes_the_todo_vault(cfg, monkeypatch):
    seen = stub_learn(monkeypatch, ["a" * 64])
    out = tg.apply_todo(cfg, rec("todo"), verdict())
    assert out.target == "todo"
    assert seen[0]["token"] == "todo-token"
    assert "inbox" in seen[0]["tags"]


def test_todo_body_carries_the_due_date_the_vault_cannot_hold(cfg, monkeypatch):
    """The todo vault has no due-date field, so it goes in the body, not nowhere."""
    seen = stub_learn(monkeypatch, ["a" * 64])
    tg.apply_todo(cfg, rec("todo"), verdict())
    assert "due: 2026-11-14T09:00" in seen[0]["body"]


def test_idea_writes_memory_then_a_pointer_when_actionable(cfg, monkeypatch):
    seen = stub_learn(monkeypatch, ["a" * 64, "b" * 64])
    out = tg.apply_idea(cfg, rec("ideas"), verdict(actionable=True))
    assert len(seen) == 2
    assert seen[0]["token"] == "brain-token"
    assert seen[1]["token"] == "todo-token"
    assert seen[1]["title"].startswith("Review idea:")
    # The pointer references the note, rather than duplicating its body.
    assert "a" * 64 in seen[1]["body"]
    assert "pointer" in out.extra


def test_idea_writes_no_pointer_when_not_actionable(cfg, monkeypatch):
    """Un-actionable ideas must not accumulate in the todo list."""
    seen = stub_learn(monkeypatch, ["a" * 64])
    out = tg.apply_idea(cfg, rec("ideas"), verdict(actionable=False))
    assert len(seen) == 1
    assert seen[0]["token"] == "brain-token"
    assert out.extra == ""


def test_idea_survives_a_failed_pointer(cfg, monkeypatch):
    """The note is the valuable half; a pointer failure must not re-apply it."""
    calls = {"n": 0}

    def fake(cfg_, *, token, title, body, tags, source_url=""):
        calls["n"] += 1
        if calls["n"] == 1:
            return "a" * 64
        raise tg.ApplyError("daemon down")

    monkeypatch.setattr(tg, "pbrain_learn", fake)
    out = tg.apply_idea(cfg, rec("ideas"), verdict(actionable=True))
    assert out.result == "a" * 64
    assert "pointer failed" in out.extra


def test_idea_is_tagged_idea_and_inbox(cfg, monkeypatch):
    seen = stub_learn(monkeypatch, ["a" * 64])
    tg.apply_idea(cfg, rec("ideas"), verdict(actionable=False))
    assert "idea" in seen[0]["tags"] and "inbox" in seen[0]["tags"]


def test_learn_requires_a_token(cfg, monkeypatch):
    """No cross-vault fallback: a missing token must fail, not write elsewhere."""
    with pytest.raises(tg.ApplyError, match="no vault token"):
        tg.pbrain_learn(cfg, token="", title="t", body="b", tags=[])


def test_apply_routes_by_queue(cfg, monkeypatch):
    stub_learn(monkeypatch, ["a" * 64])
    assert tg.apply(cfg, rec("todo"), verdict()).target == "todo"


def test_apply_rejects_an_unknown_queue(cfg):
    bad = rec("todo")
    bad.queue = "nonsense"
    with pytest.raises(tg.ApplyError, match="no applier"):
        tg.apply(cfg, bad, verdict())


def test_apply_uses_a_shared_reminders_session(cfg, monkeypatch):
    client = FakeClient(lists="• phantom-personal\n")
    wire(monkeypatch, client)
    shared = tg.Reminders(cfg)
    tg.apply(cfg, rec("reminders"), verdict(), reminders=shared)
    tg.apply(cfg, rec("reminders"), verdict(title="Second"), reminders=shared)
    assert client.tools_called().count("reminders_list_lists") == 1


def test_drive_url_shape():
    assert tg.drive_url("abc") == "https://drive.google.com/file/d/abc/view"


def test_sha_regex_parses_learn_output():
    sha = "c" * 64
    assert tg.SHA_RE.search(f'learned "X" — sha {sha}').group(1) == sha
