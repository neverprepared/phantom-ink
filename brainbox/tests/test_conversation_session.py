"""Promote-to-session: persona → real work → results back (PR4, spec §6/§10).

Three things are proved here, and the session backend is mocked throughout —
nothing in this file starts a container:

1. **The round trip.** Promoting a message submits ONE hub task, joins the
   session to the room as a ``kind='session'`` participant, announces it, and
   then relays that task's lifecycle back in as ``kind='session'`` messages.
2. **The repointed ``channel_*`` tools.** join / read / send / complete now hit
   ``/api/conversations/...``; a session authenticated with its own task token
   is identified server-side, and cannot post under someone else's name.
3. **Dismissal.** Removing the participant drops the task↔room link, so a later
   lifecycle event does not resurrect it.

``submit_task`` is patched at the ``router`` module (which is what
``conversation_session`` imports), so the scheduler never runs and no Docker
call is made — the task record is real, its execution is not.
"""

from __future__ import annotations

import time
import uuid

import pytest

import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox import conversation_session as cse
from brainbox import conversation_store as cs
from brainbox.models import AgentDefinition, Task, TaskStatus, Token
from brainbox.models_api import PromoteMessageRequest

PROFILE = "personal"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_submit(monkeypatch):
    """Stand in for the hub's task submission — the "session backend".

    Records every call and returns a real ``Task`` registered in the router's
    table, so everything downstream (the link, the lifecycle bridge, the
    session's own token → task → profile derivation) behaves exactly as it does
    in production, minus the container.
    """
    calls: list[dict] = []

    async def _submit(description, agent_name, **kwargs):
        now = int(time.time() * 1000)
        task = Task(
            id=str(uuid.uuid4()),
            description=description,
            agent_name=agent_name,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
            repo_url=kwargs.get("repo_url"),
            workspace_profile=kwargs.get("workspace_profile"),
        )
        router_module._tasks[task.id] = task
        calls.append({"description": description, "agent_name": agent_name, **kwargs})
        return task

    monkeypatch.setattr(router_module, "submit_task", _submit)
    return calls


@pytest.fixture
def refusing_submit(monkeypatch):
    """A hub that refuses the submission (unknown agent / policy denial)."""

    async def _submit(description, agent_name, **kwargs):
        raise ValueError(f"Agent '{agent_name}' not found")

    monkeypatch.setattr(router_module, "submit_task", _submit)


@pytest.fixture
def real_conversation_auth():
    """Drop conftest's blanket override so the real capability dependency — and
    with it the session-token identity derivation — runs."""
    from brainbox.api import (
        app,
        _require_conversations_read,
        _require_conversations_write,
    )

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


def _room(title="planning", profile=PROFILE, participants=None):
    return cs.create_conversation(
        profile=profile, title=title, participants=participants or []
    )


def _msg(conv, author="sage", content="Bump the ratchet threshold to 90."):
    return cs.add_message(
        conversation_id=conv.id, profile=conv.profile, author=author, content=content
    )


def _request(**kwargs) -> PromoteMessageRequest:
    return PromoteMessageRequest(target="session", **kwargs)


def _session_token(agent_name: str, task_id: str, *, capabilities=("hub_messaging",)) -> Token:
    """A container session's own task token — the shape ``lifecycle`` injects
    as ``BRAINBOX_TOKEN_ID``. It carries NO workspace_profile; the profile is
    derived from the task."""
    reg_module._agents[agent_name] = AgentDefinition(
        name=agent_name, image="test-image", capabilities=list(capabilities)
    )
    now = int(time.time() * 1000)
    token = Token(
        token_id=str(uuid.uuid4()),
        agent_name=agent_name,
        task_id=task_id,
        capabilities=list(capabilities),
        issued=now,
        expiry=now + 3_600_000,
    )
    reg_module._tokens[token.token_id] = token
    return token


def _auth(token: Token) -> dict[str, str]:
    return {"authorization": f"Bearer {token.token_id}", "x-api-key": ""}


# ---------------------------------------------------------------------------
# The seed brief
# ---------------------------------------------------------------------------


class TestSeedBrief:
    def test_carries_the_task_the_room_and_the_tools(self):
        conv = _room()
        msg = _msg(conv)
        other = cs.add_message(
            conversation_id=conv.id, profile=PROFILE, author="ada", content="agreed"
        )
        brief = cse.build_seed_brief(
            conv=conv, msg=msg, history=[msg, other], participant="worker-abc123"
        )
        assert msg.content in brief          # the task
        assert "[ada]: agreed" in brief      # the room
        assert conv.id in brief              # addressed at THIS conversation
        assert "channel_send" in brief       # how to talk back
        assert "worker-abc123" in brief      # who it is

    def test_a_note_is_appended_to_the_task_not_the_history(self):
        conv = _room()
        msg = _msg(conv)
        brief = cse.build_seed_brief(
            conv=conv, msg=msg, history=[msg], participant="w-1", note="use uv, not pip"
        )
        assert brief.index("use uv, not pip") < brief.index("## Conversation context")

    def test_non_conversational_kinds_are_left_out_of_the_context(self):
        conv = _room()
        msg = _msg(conv)
        joined = cs.add_message(
            conversation_id=conv.id, profile=PROFILE, author="w-1",
            content="w-1 joined the conversation.", kind="join",
        )
        brief = cse.build_seed_brief(
            conv=conv, msg=msg, history=[msg, joined], participant="w-1"
        )
        assert "joined the conversation" not in brief.split("## Conversation context")[1]

    def test_participant_names_are_unique_per_promotion(self):
        names = {cse.participant_name("worker") for _ in range(50)}
        assert len(names) == 50
        assert all(n.startswith("worker-") for n in names)


# ---------------------------------------------------------------------------
# The promotion itself
# ---------------------------------------------------------------------------


class TestPromoteToSession:
    async def test_round_trip_submits_joins_and_announces(self, fake_submit):
        conv = _room()
        msg = _msg(conv)

        link = await cse.promote_to_session(
            profile=PROFILE, conv=conv, msg=msg, body=_request(agent_name="worker")
        )

        # ONE task, seeded with the room and scoped to the profile.
        assert len(fake_submit) == 1
        assert fake_submit[0]["agent_name"] == "worker"
        assert fake_submit[0]["workspace_profile"] == PROFILE
        assert msg.content in fake_submit[0]["description"]

        # The session is a participant of the room, as kind='session'.
        roster = cs.get_conversation(conv.id, profile=PROFILE).participants
        joined = next(p for p in roster if p.name == link.participant)
        assert joined.kind == "session"

        # And the room says so, as a session message.
        msgs = cs.list_messages(conv.id, profile=PROFILE)
        assert msgs[-1].kind == "session"
        assert msgs[-1].author == link.participant
        assert link.task_id in msgs[-1].content

        # The link is what routes later lifecycle events home.
        assert cse.link_for_task(link.task_id).conversation_id == conv.id

    async def test_repo_url_reaches_the_submission(self, fake_submit):
        conv = _room()
        msg = _msg(conv)
        await cse.promote_to_session(
            profile=PROFILE, conv=conv, msg=msg,
            body=_request(agent_name="worker", repo_url="https://example.test/r.git"),
        )
        assert fake_submit[0]["repo_url"] == "https://example.test/r.git"

    async def test_a_refused_submission_surfaces_and_leaves_no_participant(
        self, refusing_submit
    ):
        """The whole point of explicit promotion: the failure is visible.

        The old engine bootstrapped a container per participant in a
        fire-and-forget task, so a broken Docker path produced a silent
        no-show. Here the error propagates and the room is untouched.
        """
        conv = _room()
        msg = _msg(conv)
        with pytest.raises(ValueError):
            await cse.promote_to_session(
                profile=PROFILE, conv=conv, msg=msg, body=_request(agent_name="ghost")
            )
        assert cs.get_conversation(conv.id, profile=PROFILE).participants == []
        assert cs.list_messages(conv.id, profile=PROFILE) == [msg]

    async def test_the_promoted_session_is_not_driven_by_the_orchestrator(
        self, fake_submit
    ):
        """A session participant is driven by its container, not by complete().

        The orchestrator only ever considers ``kind='persona'`` — asserted here
        because a session accidentally treated as a persona would burn tokens
        replying to itself.
        """
        from brainbox import conversation_orchestrator as orch

        conv = _room()
        msg = _msg(conv)
        await cse.promote_to_session(
            profile=PROFILE, conv=conv, msg=msg, body=_request(agent_name="worker")
        )
        conv = cs.get_conversation(conv.id, profile=PROFILE)
        assert orch.personas(conv) == []


# ---------------------------------------------------------------------------
# Results coming back
# ---------------------------------------------------------------------------


class TestResultsComeBack:
    async def _promoted(self, fake_submit):
        conv = _room()
        msg = _msg(conv)
        link = await cse.promote_to_session(
            profile=PROFILE, conv=conv, msg=msg, body=_request(agent_name="worker")
        )
        return conv, link

    async def test_completion_lands_in_the_room(self, fake_submit):
        conv, link = await self._promoted(fake_submit)
        task = router_module.get_task(link.task_id)
        task.result = "PR #41 opened, CI green"

        await cse.on_task_event_async("task.completed", task)

        last = cs.list_messages(conv.id, profile=PROFILE)[-1]
        assert last.kind == "session"
        assert last.author == link.participant
        assert "PR #41" in last.content

    async def test_failure_carries_the_error(self, fake_submit):
        conv, link = await self._promoted(fake_submit)
        task = router_module.get_task(link.task_id)
        task.error = "container exited 127"

        await cse.on_task_event_async("task.failed", task)

        assert "container exited 127" in cs.list_messages(conv.id, profile=PROFILE)[-1].content

    async def test_a_terminal_event_closes_the_link(self, fake_submit):
        _conv, link = await self._promoted(fake_submit)
        task = router_module.get_task(link.task_id)
        await cse.on_task_event_async("task.completed", task)
        assert cse.link_for_task(link.task_id) is None

    async def test_unlinked_tasks_are_ignored(self, fake_submit):
        """Every other task on the hub costs one dict lookup and nothing else."""
        conv = _room()
        now = int(time.time() * 1000)
        stranger = Task(
            id="not-linked", description="x", agent_name="worker",
            status=TaskStatus.COMPLETED, created_at=now, updated_at=now,
        )
        await cse.on_task_event_async("task.completed", stranger)
        assert cs.list_messages(conv.id, profile=PROFILE) == []

    async def test_queued_is_not_relayed(self, fake_submit):
        """The promotion already announced the session; a 'queued' line the
        same second is noise."""
        conv, link = await self._promoted(fake_submit)
        before = len(cs.list_messages(conv.id, profile=PROFILE))
        await cse.on_task_event_async("task.queued", router_module.get_task(link.task_id))
        assert len(cs.list_messages(conv.id, profile=PROFILE)) == before

    async def test_a_vanished_room_does_not_raise(self, fake_submit):
        _conv, link = await self._promoted(fake_submit)
        cs.delete_conversation(link.conversation_id, profile=PROFILE)
        task = router_module.get_task(link.task_id)
        assert await cse.on_task_event_async("task.completed", task) is None

    def test_the_sync_listener_is_a_no_op_without_a_loop(self):
        """``router._emit`` is synchronous and is also called from sync code —
        it must never blow up because there is no event loop to post on."""
        now = int(time.time() * 1000)
        task = Task(
            id="t-x", description="x", agent_name="worker",
            status=TaskStatus.COMPLETED, created_at=now, updated_at=now,
        )
        cse.register_link(
            cse.SessionLink(
                task_id="t-x", conversation_id="c-x", profile=PROFILE,
                participant="worker-1", agent_name="worker",
            )
        )
        cse.on_task_event("task.completed", task)  # no raise


# ---------------------------------------------------------------------------
# Dismissal
# ---------------------------------------------------------------------------


class TestDismiss:
    async def test_removing_the_participant_drops_the_link(self, c, fake_submit):
        conv = _room()
        msg = _msg(conv)
        link = await cse.promote_to_session(
            profile=PROFILE, conv=conv, msg=msg, body=_request(agent_name="worker")
        )

        resp = await c.delete(
            f"/api/conversations/{conv.id}/participants/{link.participant}",
            params={"profile": PROFILE},
        )
        assert resp.status_code == 200
        assert link.participant not in [p["name"] for p in resp.json()["participants"]]
        assert cse.link_for_task(link.task_id) is None

    async def test_a_dismissed_session_stops_reporting(self, c, fake_submit):
        conv = _room()
        msg = _msg(conv)
        link = await cse.promote_to_session(
            profile=PROFILE, conv=conv, msg=msg, body=_request(agent_name="worker")
        )
        await c.delete(
            f"/api/conversations/{conv.id}/participants/{link.participant}",
            params={"profile": PROFILE},
        )
        before = len(cs.list_messages(conv.id, profile=PROFILE))

        await cse.on_task_event_async(
            "task.completed", router_module.get_task(link.task_id)
        )
        assert len(cs.list_messages(conv.id, profile=PROFILE)) == before

    async def test_dismissal_keeps_the_history(self, c, fake_submit):
        """A departed session's turns are still part of what happened."""
        conv = _room()
        msg = _msg(conv)
        link = await cse.promote_to_session(
            profile=PROFILE, conv=conv, msg=msg, body=_request(agent_name="worker")
        )
        await c.delete(
            f"/api/conversations/{conv.id}/participants/{link.participant}",
            params={"profile": PROFILE},
        )
        authors = [m.author for m in cs.list_messages(conv.id, profile=PROFILE)]
        assert link.participant in authors


# ---------------------------------------------------------------------------
# Over HTTP: the promote route's session target
# ---------------------------------------------------------------------------


class TestPromoteRouteSessionTarget:
    async def _room_with_message(self, c, profile=PROFILE):
        conv = _room(profile=profile)
        msg = _msg(conv)
        return conv, msg

    async def test_promote_to_session_over_http(self, c, fake_submit):
        conv, msg = await self._room_with_message(c)
        resp = await c.post(
            f"/api/conversations/{conv.id}/messages/{msg.id}/promote",
            params={"profile": PROFILE},
            json={"target": "session", "agent_name": "worker"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True and body["target"] == "session"
        assert body["task_id"] in router_module._tasks
        assert body["participant"].startswith("worker-")
        assert cse.link_for_task(body["task_id"]).participant == body["participant"]

    async def test_the_promotion_lands_on_the_conversation_timeline_entity(
        self, c, fake_submit
    ):
        from brainbox import agent_store
        from brainbox import conversation_runtime as runtime

        conv, msg = await self._room_with_message(c)
        await c.post(
            f"/api/conversations/{conv.id}/messages/{msg.id}/promote",
            params={"profile": PROFILE},
            json={"target": "session", "agent_name": "worker"},
        )
        state = agent_store.get_state(f"conversation:{conv.id}")
        assert state["type"] == runtime.BUS_PROMOTED
        assert state["metadata"]["promote_target"] == "session"

    async def test_a_refused_submission_is_a_400_not_a_500(self, c, refusing_submit):
        conv, msg = await self._room_with_message(c)
        resp = await c.post(
            f"/api/conversations/{conv.id}/messages/{msg.id}/promote",
            params={"profile": PROFILE},
            json={"target": "session", "agent_name": "ghost"},
        )
        assert resp.status_code == 400
        assert "ghost" in resp.json()["detail"]

    async def test_cross_profile_promote_to_session_is_404_and_submits_nothing(
        self, c, fake_submit
    ):
        conv, msg = await self._room_with_message(c, profile="personal")
        resp = await c.post(
            f"/api/conversations/{conv.id}/messages/{msg.id}/promote",
            params={"profile": "work"},
            json={"target": "session"},
        )
        assert resp.status_code == 404
        assert fake_submit == []


# ---------------------------------------------------------------------------
# The repointed channel_* tools, against the new store
# ---------------------------------------------------------------------------


class TestRepointedChannelTools:
    """The tool NAMES survived; the engine underneath did not.

    Each test drives the REST call the corresponding MCP tool now makes, with a
    session's own task token — which is the only way the server can tell which
    participant is calling.
    """

    async def _session(self, *, profile=PROFILE, joined=True):
        conv = _room(profile=profile)
        msg = _msg(conv)
        link = await cse.promote_to_session(
            profile=profile, conv=conv, msg=msg, body=_request(agent_name="worker")
        )
        token = _session_token("worker", link.task_id)
        if not joined:
            cs.remove_participant(conv.id, profile=profile, name=link.participant)
        return conv, link, token

    # -- channel_join ------------------------------------------------------

    async def test_join_registers_the_session_and_announces_it(
        self, c, fake_submit, real_conversation_auth
    ):
        conv, link, token = await self._session(joined=False)
        resp = await c.post(f"/api/conversations/{conv.id}/join", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        joined = next(
            p for p in resp.json()["participants"] if p["name"] == link.participant
        )
        assert joined["kind"] == "session"
        assert "joined the conversation" in cs.list_messages(conv.id, profile=PROFILE)[-1].content

    async def test_join_is_idempotent(self, c, fake_submit, real_conversation_auth):
        conv, link, token = await self._session()
        before = len(cs.list_messages(conv.id, profile=PROFILE))
        for _ in range(3):
            resp = await c.post(f"/api/conversations/{conv.id}/join", headers=_auth(token))
            assert resp.status_code == 200
        names = [p["name"] for p in resp.json()["participants"]]
        assert names.count(link.participant) == 1
        assert len(cs.list_messages(conv.id, profile=PROFILE)) == before

    async def test_join_needs_the_capability(self, c, fake_submit, real_conversation_auth):
        conv, link, _ = await self._session()
        weak = _session_token("worker", link.task_id, capabilities=[])
        resp = await c.post(f"/api/conversations/{conv.id}/join", headers=_auth(weak))
        assert resp.status_code == 403

    async def test_join_cannot_reach_another_profiles_room(
        self, c, fake_submit, real_conversation_auth
    ):
        """The session's profile comes from its TASK, so a room in another
        profile is a 404 — the same answer a genuine miss gives."""
        _conv, link, token = await self._session(profile=PROFILE)
        elsewhere = _room(profile="work")
        resp = await c.post(
            f"/api/conversations/{elsewhere.id}/join", headers=_auth(token)
        )
        assert resp.status_code == 404

    async def test_join_refuses_an_archived_room(
        self, c, fake_submit, real_conversation_auth
    ):
        conv, link, token = await self._session(joined=False)
        cs.archive_conversation(conv.id, profile=PROFILE)
        resp = await c.post(f"/api/conversations/{conv.id}/join", headers=_auth(token))
        assert resp.status_code == 400

    # -- channel_read ------------------------------------------------------

    async def test_read_returns_the_room_and_since_id_pages_it(
        self, c, fake_submit, real_conversation_auth
    ):
        conv, _link, token = await self._session()
        resp = await c.get(
            f"/api/conversations/{conv.id}/messages", headers=_auth(token)
        )
        assert resp.status_code == 200, resp.text
        msgs = resp.json()
        assert len(msgs) >= 2

        resp = await c.get(
            f"/api/conversations/{conv.id}/messages",
            params={"since_id": msgs[-1]["id"]},
            headers=_auth(token),
        )
        assert resp.json() == []

    async def test_read_derives_the_profile_from_the_token(
        self, c, fake_submit, real_conversation_auth
    ):
        """No profile in the query at all — exactly what the repointed tool
        sends from inside a container, where no profile is known."""
        conv, _link, token = await self._session()
        resp = await c.get(
            f"/api/conversations/{conv.id}/messages", headers=_auth(token)
        )
        assert resp.status_code == 200
        assert all(m["profile"] == PROFILE for m in resp.json())

    async def test_read_refuses_a_mismatched_profile(
        self, c, fake_submit, real_conversation_auth
    ):
        conv, _link, token = await self._session()
        resp = await c.get(
            f"/api/conversations/{conv.id}/messages",
            params={"profile": "work"},
            headers=_auth(token),
        )
        assert resp.status_code == 403

    # -- channel_send ------------------------------------------------------

    async def test_send_posts_as_the_session(self, c, fake_submit, real_conversation_auth):
        conv, link, token = await self._session()
        resp = await c.post(
            f"/api/conversations/{conv.id}/messages",
            json={"author": link.participant, "content": "found the bug"},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["kind"] == "session"
        assert resp.json()["author"] == link.participant

    async def test_send_cannot_impersonate_another_participant(
        self, c, fake_submit, real_conversation_auth
    ):
        conv, link, token = await self._session()
        cs.add_participant(
            conv.id, profile=PROFILE, participant={"name": "sage", "kind": "persona"}
        )
        resp = await c.post(
            f"/api/conversations/{conv.id}/messages",
            json={"author": "sage", "content": "not sage"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["author"] == link.participant

    async def test_send_from_a_non_member_session_is_refused(
        self, c, fake_submit, real_conversation_auth
    ):
        conv, _link, token = await self._session(joined=False)
        resp = await c.post(
            f"/api/conversations/{conv.id}/messages",
            json={"author": "whoever", "content": "hi"},
            headers=_auth(token),
        )
        assert resp.status_code == 403

    # -- channel_complete --------------------------------------------------

    async def test_complete_archives_the_room_with_a_closing_note(
        self, c, fake_submit, real_conversation_auth
    ):
        conv, link, token = await self._session()
        resp = await c.post(
            f"/api/conversations/{conv.id}/archive",
            json={"by": link.participant, "reason": "shipped in PR #41"},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "archived"

        msgs = cs.list_messages(conv.id, profile=PROFILE)
        assert msgs[-1].content == "shipped in PR #41"
        assert msgs[-1].kind == "session"
        assert msgs[-1].author == link.participant

    async def test_archive_without_a_body_still_works(self, c, fake_submit):
        """The desktop app's archive button sends no body — that path must not
        regress now that the route accepts one."""
        conv = _room()
        resp = await c.post(
            f"/api/conversations/{conv.id}/archive", params={"profile": PROFILE}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"
        assert cs.list_messages(conv.id, profile=PROFILE) == []

    async def test_completing_twice_does_not_append_a_second_closing_note(
        self, c, fake_submit, real_conversation_auth
    ):
        """Re-archiving is idempotent; a second "we're done" is not."""
        conv, link, token = await self._session()
        for _ in range(2):
            resp = await c.post(
                f"/api/conversations/{conv.id}/archive",
                json={"by": link.participant, "reason": "done"},
                headers=_auth(token),
            )
            assert resp.status_code == 200
        notes = [m for m in cs.list_messages(conv.id, profile=PROFILE) if m.content == "done"]
        assert len(notes) == 1

    async def test_an_archived_room_takes_no_more_messages(
        self, c, fake_submit, real_conversation_auth
    ):
        conv, link, token = await self._session()
        await c.post(
            f"/api/conversations/{conv.id}/archive",
            json={"by": link.participant, "reason": "done"},
            headers=_auth(token),
        )
        resp = await c.post(
            f"/api/conversations/{conv.id}/messages",
            json={"author": link.participant, "content": "one more thing"},
            headers=_auth(token),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# The old engine is gone
# ---------------------------------------------------------------------------


class TestChannelsEngineIsRetired:
    def test_the_module_no_longer_exists(self):
        with pytest.raises(ModuleNotFoundError):
            __import__("brainbox.channels")

    def test_nothing_in_the_package_imports_it(self):
        """A dangling import would only surface at runtime, on the one code
        path that touches it — so it is asserted statically here."""
        import pathlib
        import re

        import brainbox

        root = pathlib.Path(brainbox.__file__).parent
        importers = re.compile(r"^\s*(from\s+\.channels\s+import|import\s+brainbox\.channels|from\s+brainbox\.channels\s+import|from\s+\.\s+import\s+.*\bchannels\b)", re.M)
        offenders = [
            str(f.relative_to(root))
            for f in root.rglob("*.py")
            if importers.search(f.read_text())
        ]
        assert offenders == []

    async def test_the_old_routes_are_gone(self, c):
        from brainbox.api import app

        paths = {r.path for r in app.routes}
        assert not any(p.startswith("/api/hub/channels") for p in paths)
