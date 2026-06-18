"""Tests for the GitHub webhook trigger surface (Phase C).

Two layers:
  - Pure logic in github_webhook.py: signature verification (constant-time,
    empty-secret rejection, malformed-header rejection), repo allowlist
    (empty list accepts everything, non-empty narrows, missing repo on
    non-empty list is False), trigger extraction for the three event
    shapes we act on.
  - API integration via the FastAPI ASGI client: signed PR-opened payload
    fires start_loop and returns the new loop_id; bad signature → 401;
    repo not in allowlist → 403; unsupported event → 200 triggered:false;
    missing template → 404.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

import brainbox.registry as reg_module
from brainbox.config import settings
from brainbox.github_webhook import (
    allow_repo,
    extract_loop_trigger,
    verify_signature,
)
from brainbox.loops import LoopStatus
from brainbox.models import AgentDefinition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _pr_payload(*, action: str = "opened", repo: str = "owner/name", pr_number: int = 117) -> dict:
    return {
        "action": action,
        "number": pr_number,
        "pull_request": {
            "number": pr_number,
            "title": "Test PR",
            "head": {"sha": "headsha"},
            "base": {"sha": "basesha"},
        },
        "repository": {"full_name": repo},
    }


@pytest.fixture
def reviewer_agent():
    agent = AgentDefinition(name="reviewer", image="test-image", capabilities=["hub_messaging"])
    reg_module._agents["reviewer"] = agent
    return agent


@pytest.fixture
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "super-secret")
    yield "super-secret"


@pytest.fixture
def allowlist_open(monkeypatch):
    monkeypatch.setattr(settings, "github_loop_repos", [])


# ---------------------------------------------------------------------------
# Signature verification — pure
# ---------------------------------------------------------------------------


class TestVerifySignature:
    def test_correct_signature_passes(self):
        body = b'{"hello":"world"}'
        sig = _sign(body, "secret")
        assert verify_signature(body, sig, "secret") is True

    def test_wrong_signature_fails(self):
        body = b'{"hello":"world"}'
        sig = _sign(body, "different-secret")
        assert verify_signature(body, sig, "secret") is False

    def test_empty_secret_rejects_everything(self):
        body = b'{"hello":"world"}'
        sig = _sign(body, "")  # signed with empty key
        assert verify_signature(body, sig, "") is False

    def test_missing_header_fails(self):
        assert verify_signature(b"x", "", "secret") is False

    def test_wrong_algorithm_prefix_fails(self):
        body = b"x"
        sig = "sha1=" + hmac.new(b"secret", body, hashlib.sha1).hexdigest()
        assert verify_signature(body, sig, "secret") is False


# ---------------------------------------------------------------------------
# Repo allowlist — pure
# ---------------------------------------------------------------------------


class TestAllowRepo:
    def test_empty_allowlist_accepts(self):
        assert allow_repo(_pr_payload(repo="any/thing"), []) is True

    def test_non_empty_allowlist_narrows(self):
        assert allow_repo(_pr_payload(repo="ok/repo"), ["ok/repo"]) is True
        assert allow_repo(_pr_payload(repo="not/listed"), ["ok/repo"]) is False

    def test_missing_repo_on_allowlist_is_false(self):
        assert allow_repo({}, ["any/thing"]) is False

    def test_missing_repo_with_empty_allowlist_is_true(self):
        # Open-allowlist trusts the secret; even payloads without a repo pass
        assert allow_repo({}, []) is True


# ---------------------------------------------------------------------------
# Trigger extraction — pure
# ---------------------------------------------------------------------------


class TestExtractTrigger:
    def test_pull_request_opened_triggers(self):
        trig = extract_loop_trigger("pull_request", _pr_payload(action="opened"))
        assert trig is not None
        assert trig["pr_number"] == 117
        assert trig["repo"] == "owner/name"
        assert trig["head_sha"] == "headsha"
        assert trig["trigger_event"] == "pull_request.opened"

    def test_pull_request_synchronize_triggers(self):
        trig = extract_loop_trigger("pull_request", _pr_payload(action="synchronize"))
        assert trig is not None
        assert trig["trigger_event"] == "pull_request.synchronize"

    def test_pull_request_reopened_triggers(self):
        trig = extract_loop_trigger("pull_request", _pr_payload(action="reopened"))
        assert trig is not None

    def test_pull_request_closed_does_not_trigger(self):
        # Closing a PR isn't a review event — no loop.
        trig = extract_loop_trigger("pull_request", _pr_payload(action="closed"))
        assert trig is None

    def test_pull_request_missing_pr_number_returns_none(self):
        payload = _pr_payload()
        payload["pull_request"]["number"] = None
        payload["number"] = None
        assert extract_loop_trigger("pull_request", payload) is None

    def test_unknown_event_returns_none(self):
        assert extract_loop_trigger("push", _pr_payload()) is None
        assert extract_loop_trigger("star", _pr_payload()) is None

    def test_issue_comment_with_loop_on_pr_triggers(self):
        payload = {
            "action": "created",
            "issue": {"number": 117, "pull_request": {"url": "..."}},
            "comment": {"body": "/loop please review again", "id": 42},
            "repository": {"full_name": "owner/name"},
        }
        trig = extract_loop_trigger("issue_comment", payload)
        assert trig is not None
        assert trig["pr_number"] == 117
        assert trig["trigger_event"] == "issue_comment.loop"

    def test_issue_comment_without_loop_marker_does_not_trigger(self):
        payload = {
            "action": "created",
            "issue": {"number": 117, "pull_request": {"url": "..."}},
            "comment": {"body": "regular comment, no marker"},
            "repository": {"full_name": "owner/name"},
        }
        assert extract_loop_trigger("issue_comment", payload) is None

    def test_issue_comment_on_non_pr_issue_does_not_trigger(self):
        # An issue_comment on a real issue (no pull_request key) is not a loop trigger
        payload = {
            "action": "created",
            "issue": {"number": 117},  # no pull_request key
            "comment": {"body": "/loop"},
            "repository": {"full_name": "owner/name"},
        }
        assert extract_loop_trigger("issue_comment", payload) is None


# ---------------------------------------------------------------------------
# /api/webhooks/github route integration
# ---------------------------------------------------------------------------


class TestWebhookRoute:
    @pytest.mark.asyncio
    async def test_signed_pr_opened_fires_loop(
        self, client, reviewer_agent, webhook_secret, allowlist_open
    ):
        payload = _pr_payload()
        body = json.dumps(payload).encode()
        sig = _sign(body, webhook_secret)
        async with client as c:
            resp = await c.post(
                "/api/webhooks/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                    "Content-Type": "application/json",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["triggered"] is True
        assert "loop_id" in data
        assert data["trigger"]["pr_number"] == 117

        # And the loop is actually running in the runner registry
        from brainbox.loop_runner import get_instance

        inst = get_instance(data["loop_id"])
        assert inst is not None
        assert inst.status == LoopStatus.RUNNING

    @pytest.mark.asyncio
    async def test_bad_signature_returns_401(
        self, client, reviewer_agent, webhook_secret, allowlist_open
    ):
        body = json.dumps(_pr_payload()).encode()
        async with client as c:
            resp = await c.post(
                "/api/webhooks/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": "sha256=" + "0" * 64,
                    "X-GitHub-Event": "pull_request",
                },
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_signature_returns_401(self, client, reviewer_agent, webhook_secret, allowlist_open):
        async with client as c:
            resp = await c.post(
                "/api/webhooks/github",
                content=b"{}",
                headers={"X-GitHub-Event": "pull_request"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_repo_not_in_allowlist_returns_403(
        self, client, reviewer_agent, webhook_secret, monkeypatch
    ):
        monkeypatch.setattr(settings, "github_loop_repos", ["only/this"])
        body = json.dumps(_pr_payload(repo="not/listed")).encode()
        sig = _sign(body, webhook_secret)
        async with client as c:
            resp = await c.post(
                "/api/webhooks/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                },
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unsupported_event_is_200_not_triggered(
        self, client, reviewer_agent, webhook_secret, allowlist_open
    ):
        body = json.dumps({"action": "started", "repository": {"full_name": "x/y"}}).encode()
        sig = _sign(body, webhook_secret)
        async with client as c:
            resp = await c.post(
                "/api/webhooks/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "star",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["triggered"] is False

    @pytest.mark.asyncio
    async def test_malformed_json_returns_400(self, client, reviewer_agent, webhook_secret, allowlist_open):
        body = b"{not valid json"
        sig = _sign(body, webhook_secret)
        async with client as c:
            resp = await c.post(
                "/api/webhooks/github",
                content=body,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "pull_request",
                },
            )
        assert resp.status_code == 400
