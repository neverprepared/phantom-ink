"""Server-side profile derivation on the conversation routes (PR3, spec §8).

PR1 already made profile scoping a *store* invariant: every conversation read
and write puts the profile in the SQL, so the API cannot forget to filter (see
``test_conversations_api.TestProfileScopingOverHttp`` for that half). What PR3
adds is where the profile *comes from*: a Bearer-token caller is pinned to the
profile its token was minted for, so it cannot reach another profile's rooms by
naming one in the query string.

The shared API key remains full trust (it is the operator's own key), so on
that path the request-supplied profile is still the source — there is no
narrower caller identity to derive one from. These tests assert both halves.
"""

from __future__ import annotations

import pytest

import brainbox.registry as reg_module
from brainbox import conversation_store as cs
from brainbox.api import (
    app,
    _require_conversations_read,
    _require_conversations_write,
    _scoped_profile,
)
from brainbox.models import Token


@pytest.fixture
def real_conversation_auth():
    """Drop conftest's blanket override so the real capability dependency (and
    therefore the real profile derivation) runs."""
    guards = (_require_conversations_read, _require_conversations_write)
    saved = {g: app.dependency_overrides.pop(g, None) for g in guards}
    yield
    for g, prev in saved.items():
        if prev is not None:
            app.dependency_overrides[g] = prev


@pytest.fixture
async def c(client):
    async with client as opened:
        yield opened


def _token(profile: str, caps: list[str]) -> tuple[str, Token]:
    return reg_module.issue_profile_token(profile, caps, label="test")


# ---------------------------------------------------------------------------
# The derivation itself
# ---------------------------------------------------------------------------


class TestScopedProfile:
    def test_token_profile_wins_over_an_absent_request_profile(self):
        token = Token(
            token_id="t", agent_name="profile", task_id="", capabilities=[],
            issued=0, expiry=0, workspace_profile="work",
        )
        assert _scoped_profile(token, None) == "work"
        assert _scoped_profile(token, "") == "work"

    def test_matching_request_profile_is_accepted(self):
        token = Token(
            token_id="t", agent_name="profile", task_id="", capabilities=[],
            issued=0, expiry=0, workspace_profile="work",
        )
        assert _scoped_profile(token, "work") == "work"

    def test_mismatched_request_profile_is_refused(self):
        from fastapi import HTTPException

        token = Token(
            token_id="t", agent_name="profile", task_id="", capabilities=[],
            issued=0, expiry=0, workspace_profile="work",
        )
        with pytest.raises(HTTPException) as exc:
            _scoped_profile(token, "personal")
        assert exc.value.status_code == 403
        assert "work" in exc.value.detail

    def test_api_key_path_still_uses_the_request_profile(self):
        # token=None is what require_capability returns for the shared API key.
        assert _scoped_profile(None, "personal") == "personal"

    def test_api_key_path_still_requires_a_profile(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _scoped_profile(None, "   ")
        assert exc.value.status_code == 400

    def test_a_token_without_a_profile_falls_back_to_the_request(self):
        """A session token (agent_name/task-bound, no workspace_profile) carries
        no profile to derive from, so the request's own value stands."""
        token = Token(
            token_id="t", agent_name="worker", task_id="task-1", capabilities=[],
            issued=0, expiry=0,
        )
        assert _scoped_profile(token, "personal") == "personal"


# ---------------------------------------------------------------------------
# Over HTTP, with a real minted token
# ---------------------------------------------------------------------------


class TestTokenScopedRoutes:
    async def test_token_reads_only_its_own_profile(self, c, real_conversation_auth):
        cs.create_conversation(profile="work", title="work room")
        cs.create_conversation(profile="personal", title="personal room")
        raw, _ = _token("work", ["conversations:read"])

        resp = await c.get(
            "/api/conversations",
            params={"profile": "work"},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200
        assert [r["title"] for r in resp.json()] == ["work room"]

    async def test_token_cannot_name_another_profile(self, c, real_conversation_auth):
        conv = cs.create_conversation(profile="personal", title="personal room")
        raw, _ = _token("work", ["conversations:read"])

        listed = await c.get(
            "/api/conversations",
            params={"profile": "personal"},
            headers={"Authorization": f"Bearer {raw}"},
        )
        got = await c.get(
            f"/api/conversations/{conv.id}",
            params={"profile": "personal"},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert listed.status_code == 403
        assert got.status_code == 403

    async def test_write_is_scoped_to_the_token_not_the_body(self, c, real_conversation_auth):
        """A create names 'personal' in the BODY; the token says 'work'. The
        record must land in the token's profile, not the body's."""
        raw, _ = _token("work", ["conversations:write"])
        resp = await c.post(
            "/api/conversations",
            json={"title": "smuggled", "profile": "work", "participants": []},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200
        assert resp.json()["profile"] == "work"
        assert cs.list_conversations(profile="personal") == []

    async def test_post_message_is_refused_across_profiles(self, c, real_conversation_auth):
        conv = cs.create_conversation(profile="personal", title="personal room")
        raw, _ = _token("work", ["conversations:write"])
        resp = await c.post(
            f"/api/conversations/{conv.id}/messages",
            params={"profile": "personal"},
            json={"author": "intruder", "content": "hi"},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 403
        assert cs.list_messages(conv.id, profile="personal") == []

    async def test_token_in_its_own_profile_reads_a_room_it_owns(
        self, c, real_conversation_auth
    ):
        conv = cs.create_conversation(profile="work", title="work room")
        raw, _ = _token("work", ["conversations:read"])
        resp = await c.get(
            f"/api/conversations/{conv.id}",
            headers={"Authorization": f"Bearer {raw}"},
            params={"profile": "work"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == conv.id

    async def test_token_without_the_capability_is_403(self, c, real_conversation_auth):
        raw, _ = _token("work", ["conversations:read"])
        resp = await c.post(
            "/api/conversations",
            json={"title": "nope", "profile": "work", "participants": []},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 403

    async def test_no_auth_at_all_is_401(self, c, real_conversation_auth):
        resp = await c.get("/api/conversations", params={"profile": "work"})
        assert resp.status_code == 401

    async def test_capabilities_are_mintable(self):
        """The route names are only enforceable if the catalog knows them."""
        assert "conversations:read" in reg_module.CAPABILITY_CATALOG
        assert "conversations:write" in reg_module.CAPABILITY_CATALOG
