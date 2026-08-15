"""playbooks._run_task now routes through the brainbox.llm seam instead of the
inline create→query→stop session dance. These tests fake the seam's complete()
and assert the task lifecycle + that the right routing context is passed.
"""

from __future__ import annotations

import pytest

from brainbox import playbooks
from brainbox.llm import Completion
from brainbox.models import Playbook, PlaybookTask


def _pb_and_task(content="do the thing"):
    task = PlaybookTask(index=0, content=content)
    pb = Playbook(name="pb", markdown="- " + content, tasks=[task])
    return pb, task


async def test_run_task_routes_through_seam(monkeypatch):
    captured = {}

    async def fake_complete(prompt, *, profile, target=None, policy=None, ctx=None):
        captured.update(prompt=prompt, profile=profile, target=target, policy=policy, ctx=ctx)
        return Completion(text="the answer", backend="claude_oauth", model="claude-code-oauth")

    monkeypatch.setattr(playbooks, "llm_complete", fake_complete)

    pb, task = _pb_and_task("summarize the repo")
    await playbooks._run_task(pb, task, run_profile="personal", run_runner="mac-1")

    # task lifecycle
    assert task.status == "completed"
    assert task.output == "the answer"
    assert task.session_name == f"pb-{pb.id[:6]}-t0"  # stable, human-readable
    assert task.finished_at is not None

    # routing context: session-backed claude, allow_paid off (→ claude_oauth), and
    # the stable session name + runner + profile threaded through.
    assert captured["prompt"] == "summarize the repo"
    assert captured["profile"] == "personal"
    assert captured["target"].provider == "claude"
    assert captured["policy"].quality == "high" and captured["policy"].allow_paid is False
    ctx = captured["ctx"]
    assert ctx.caller == "playbooks"
    assert ctx.session_name == f"pb-{pb.id[:6]}-t0"
    assert ctx.runner == "mac-1"


async def test_run_task_global_profile_has_no_workspace_home(monkeypatch):
    captured = {}

    async def fake_complete(prompt, *, profile, target=None, policy=None, ctx=None):
        captured["ctx"] = ctx
        return Completion(text="x", backend="claude_oauth", model="m")

    monkeypatch.setattr(playbooks, "llm_complete", fake_complete)

    pb, task = _pb_and_task()
    await playbooks._run_task(pb, task, run_profile="global")

    assert captured["ctx"].workspace_home is None  # global → no per-profile mount


async def test_run_task_failure_marks_task_failed(monkeypatch):
    async def boom(prompt, *, profile, target=None, policy=None, ctx=None):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(playbooks, "llm_complete", boom)

    events = []
    playbooks.on_event(lambda ev, data: events.append(ev))

    pb, task = _pb_and_task()
    await playbooks._run_task(pb, task, run_profile="personal")

    assert task.status == "failed"
    assert "upstream exploded" in (task.error or "")
    assert task.finished_at is not None
    assert "playbook.task_done" in events


async def test_run_task_cancelled_propagates(monkeypatch):
    import asyncio

    async def cancel(prompt, *, profile, target=None, policy=None, ctx=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(playbooks, "llm_complete", cancel)

    pb, task = _pb_and_task()
    with pytest.raises(asyncio.CancelledError):
        await playbooks._run_task(pb, task, run_profile="personal")
    assert task.status == "failed" and task.error == "Cancelled"
