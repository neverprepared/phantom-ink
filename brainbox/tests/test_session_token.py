"""Every spawned session gets a real, role-scoped token (not a stub) so a
containerized agent can call the hub/A2A API under its own identity."""

from __future__ import annotations

import brainbox.registry as reg
from brainbox.config import settings
from brainbox.models import AgentDefinition


def _register(name: str, caps: list[str], *, persistent: bool = False):
    reg._agents[name] = AgentDefinition(
        name=name, image="t", capabilities=caps, persistent=persistent
    )


def test_dispatcher_role_gets_task_submit_token():
    _register("supervisor", ["shell_exec", "task_submit"], persistent=True)
    tok = reg.issue_session_token("supervisor", "sess-1")
    assert tok is not None
    assert "task_submit" in tok.capabilities  # can dispatch A2A
    assert tok.expiry - tok.issued == settings.hub.persistent_token_ttl * 1000  # persistent TTL


def test_non_dispatcher_role_has_no_task_submit():
    _register("assistant", ["shell_exec", "read_code"])  # no task_submit
    tok = reg.issue_session_token("assistant", "sess-2")
    assert tok is not None
    assert "task_submit" not in tok.capabilities  # cannot dispatch A2A
    assert tok.expiry - tok.issued == settings.hub.token_ttl * 1000  # transient TTL


def test_unregistered_role_returns_none():
    assert reg.issue_session_token("developer", "sess-3") is None  # caller falls back to stub


def test_token_validates_immediately():
    _register("worker", ["task_submit"])
    tok = reg.issue_session_token("worker", "sess-4")
    assert reg.validate_token(tok.token_id) is not None
