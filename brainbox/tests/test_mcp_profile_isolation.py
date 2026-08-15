"""The phantom-api MCP pins every op to CL_WORKSPACE_PROFILE when bound.

Guards against cross-profile reach: in bound mode an agent cannot create,
list, or drive another profile's sessions/tasks/playbooks by argument or by
session name.
"""

from __future__ import annotations

import pytest

from brainbox import mcp_server as m


@pytest.fixture
def capture(monkeypatch):
    """Replace _request with a recorder that also serves a canned session list."""
    calls: list[tuple[str, str, dict | None]] = []
    sessions = [
        {"session_name": "mine", "name": "developer-mine", "workspace_profile": "personal"},
        {"session_name": "theirs", "name": "developer-theirs", "workspace_profile": "work"},
        {"session_name": "orphan", "name": "developer-orphan", "workspace_profile": ""},
    ]

    def fake_request(method, path, body=None, timeout=30):
        calls.append((method, path, body))
        if method == "GET" and path == "/api/sessions":
            return sessions
        return {"ok": True}

    monkeypatch.setattr(m, "_request", fake_request)
    return calls


def _bind(monkeypatch, profile):
    monkeypatch.setenv("CL_WORKSPACE_PROFILE", profile)


def _unbind(monkeypatch):
    monkeypatch.delenv("CL_WORKSPACE_PROFILE", raising=False)


# --- create / submit force the bound profile into the body ------------------

def test_create_session_forces_profile(monkeypatch, capture):
    _bind(monkeypatch, "personal")
    m.create_session(name="x")
    method, path, body = capture[-1]
    assert (method, path) == ("POST", "/api/create")
    assert body["workspace_profile"] == "personal"


def test_create_session_unbound_omits_profile(monkeypatch, capture):
    _unbind(monkeypatch)
    m.create_session(name="x")
    _, _, body = capture[-1]
    assert "workspace_profile" not in body


def test_submit_task_forces_profile(monkeypatch, capture):
    _bind(monkeypatch, "personal")
    m.submit_task(description="do it")
    method, path, body = capture[-1]
    assert (method, path) == ("POST", "/api/hub/tasks")
    assert body["workspace_profile"] == "personal"


# --- list paths filter to the bound profile ---------------------------------

def test_list_sessions_filters(monkeypatch, capture):
    _bind(monkeypatch, "personal")
    m.list_sessions()
    _, path, _ = capture[-1]
    assert path == "/api/sessions?workspace_profile=personal"


def test_list_tasks_filters(monkeypatch, capture):
    _bind(monkeypatch, "personal")
    m.list_tasks()
    _, path, _ = capture[-1]
    assert "workspace_profile=personal" in path


# --- by-name guard: refuse foreign, allow own / orphan / unbound ------------

def test_exec_foreign_session_refused(monkeypatch, capture):
    _bind(monkeypatch, "personal")
    result = m.exec_session(name="theirs", command="whoami")
    assert result["status"] == 403
    assert all(not p.endswith("/exec") for _, p, _ in capture)


def test_exec_own_session_allowed(monkeypatch, capture):
    _bind(monkeypatch, "personal")
    m.exec_session(name="mine", command="whoami")
    assert capture[-1][1] == "/api/sessions/mine/exec"


def test_exec_orphan_session_allowed(monkeypatch, capture):
    _bind(monkeypatch, "personal")
    m.exec_session(name="orphan", command="whoami")
    assert capture[-1][1] == "/api/sessions/orphan/exec"


def test_delete_by_container_name_refused(monkeypatch, capture):
    _bind(monkeypatch, "personal")
    result = m.delete_session(name="developer-theirs")
    assert result["status"] == 403


def test_unbound_never_guards(monkeypatch, capture):
    _unbind(monkeypatch)
    m.exec_session(name="theirs", command="whoami")
    assert capture[-1][1] == "/api/sessions/theirs/exec"
    assert all(p != "/api/sessions" for _, p, _ in capture)
