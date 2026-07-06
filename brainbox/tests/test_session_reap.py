"""Tests for orphaned-session reaping (local-process runner / #164 flip side).

Two guarantees:
  1. recycle() marks the session inactive + pops it from _sessions even when the
     ctx can't be resolved or the backend errors — so a cancelled/failed task
     never leaks an active=1 row.
  2. load_runner_sessions_from_db() self-heals: a LOCAL docker session whose
     container is confirmed gone is marked inactive instead of resurrected as a
     phantom "running" session; live and remote sessions are untouched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from docker.errors import NotFound

from brainbox.lifecycle import (
    _local_container_missing,
    load_runner_sessions_from_db,
    recycle,
)
from brainbox.models import SessionContext


def _ctx(name="task-x", *, backend="docker", docker_host="", container=None, runner_name=None):
    return SessionContext(
        session_name=name,
        container_name=container or name,
        port=7681,
        created_at=0,
        ttl=0,
        backend=backend,
        docker_host=docker_host,
        runner_name=runner_name,
    )


# ---------------------------------------------------------------------------
# recycle() bookkeeping guarantee
# ---------------------------------------------------------------------------


class TestRecycleGuarantees:
    async def test_marks_inactive_when_ctx_unresolvable(self):
        mark = AsyncMock()
        with patch("brainbox.lifecycle._sessions", {}), patch(
            "brainbox.store.async_mark_session_inactive", mark
        ), patch("brainbox.store.async_insert_session_history", AsyncMock()), patch(
            "brainbox.monitor.stop_monitoring", MagicMock()
        ):
            result = await recycle("task-orphan", reason="task_cancelled")

        assert result is None  # nothing to resolve
        mark.assert_awaited_once()
        assert mark.await_args.args[0] == "task-orphan"

    async def test_marks_inactive_and_pops_when_backend_raises(self):
        ctx = _ctx("task-boom")
        sessions = {ctx.session_name: ctx}
        mark = AsyncMock()
        bad = MagicMock()
        bad.stop = AsyncMock(side_effect=RuntimeError("stop boom"))
        bad.remove = AsyncMock(side_effect=RuntimeError("remove boom"))
        with patch("brainbox.lifecycle._sessions", sessions), patch(
            "brainbox.backends.create_backend", return_value=bad
        ), patch("brainbox.store.async_mark_session_inactive", mark), patch(
            "brainbox.store.async_insert_session_history", AsyncMock()
        ), patch("brainbox.monitor.stop_monitoring", MagicMock()), patch(
            "brainbox.lifecycle._remove_host_worktree", AsyncMock()
        ):
            await recycle(ctx.session_name)

        mark.assert_awaited_once()
        assert mark.await_args.args[0] == "task-boom"
        assert "task-boom" not in sessions  # popped despite backend failure


# ---------------------------------------------------------------------------
# load_runner_sessions_from_db() self-healing
# ---------------------------------------------------------------------------


class TestLoadReconcile:
    def test_reaps_missing_local_container(self):
        row = _ctx("task-dead").model_dump()
        sess: dict = {}
        mark = MagicMock()
        with patch("brainbox.lifecycle._sessions", sess), patch(
            "brainbox.store.load_active_runner_sessions", return_value=[row]
        ), patch("brainbox.store.mark_session_inactive", mark), patch(
            "brainbox.lifecycle._local_container_missing", return_value=True
        ):
            loaded = load_runner_sessions_from_db()

        assert loaded == 0
        assert "task-dead" not in sess
        mark.assert_called_once()
        assert mark.call_args.args[0] == "task-dead"

    def test_restores_live_session(self):
        row = _ctx("task-live").model_dump()
        sess: dict = {}
        mark = MagicMock()
        with patch("brainbox.lifecycle._sessions", sess), patch(
            "brainbox.store.load_active_runner_sessions", return_value=[row]
        ), patch("brainbox.store.mark_session_inactive", mark), patch(
            "brainbox.lifecycle._local_container_missing", return_value=False
        ):
            loaded = load_runner_sessions_from_db()

        assert loaded == 1
        assert "task-live" in sess
        mark.assert_not_called()


# ---------------------------------------------------------------------------
# _local_container_missing() — only confirms death for a LOCAL container
# ---------------------------------------------------------------------------


class TestLocalContainerMissing:
    def test_false_for_non_docker_backend(self):
        assert _local_container_missing(_ctx(backend="utm")) is False

    def test_false_for_remote_docker_host(self):
        # a remote runner's docker daemon can't be checked here → never reap
        assert _local_container_missing(_ctx(docker_host="tcp://10.0.0.5:2375")) is False

    def test_false_for_runner_session(self):
        # Runner sessions have backend='docker' + empty docker_host but their
        # container lives on the RUNNER's Docker — local NotFound proves
        # nothing. Regression: startup reconcile evicted live runner sessions.
        client = MagicMock()
        client.containers.get.side_effect = NotFound("not in local docker")
        with patch("brainbox.backends.docker._docker", return_value=client):
            assert _local_container_missing(_ctx(runner_name="control")) is False

    def test_true_on_confirmed_notfound(self):
        client = MagicMock()
        client.containers.get.side_effect = NotFound("gone")
        with patch("brainbox.backends.docker._docker", return_value=client):
            assert _local_container_missing(_ctx()) is True

    def test_false_when_container_exists(self):
        client = MagicMock()
        client.containers.get.return_value = MagicMock()
        with patch("brainbox.backends.docker._docker", return_value=client):
            assert _local_container_missing(_ctx()) is False

    def test_false_on_daemon_error(self):
        # transient docker error must not evict a session we can't prove is dead
        client = MagicMock()
        client.containers.get.side_effect = RuntimeError("daemon down")
        with patch("brainbox.backends.docker._docker", return_value=client):
            assert _local_container_missing(_ctx()) is False
