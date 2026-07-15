"""Tests for POST /api/ratchet — the thin ci-ratchet convenience endpoint.

The endpoint is a semantic alias over submit_task(agent_name="worker"): it
queues a single autonomous worker that clones repo_url (via BRAINBOX_REPO_URL),
opens a PR, watches CI, fixes until green, and stops with the PR open. No
auto-merge, no daemon-side clone.
"""

from __future__ import annotations

import brainbox.registry as reg_module
import brainbox.router as router_module
from brainbox.models import AgentDefinition, TaskStatus

REPO = "git@github.com:org/repo"


def _register_worker() -> None:
    """Register a worker agent so submit_task's get_agent() resolves."""
    reg_module._agents["worker"] = AgentDefinition(
        name="worker",
        image="test-image",
        capabilities=["shell_exec", "read_code", "write_code", "hub_messaging", "task_submit"],
    )


class TestRatchet:
    async def test_queues_pending_worker_task(self, client):
        _register_worker()
        r = await client.post("/api/ratchet", json={"repo_url": REPO, "task": "Fix failing tests"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["success"] is True
        assert body["repo_url"] == REPO

        task = router_module._tasks[body["task_id"]]
        assert task.agent_name == "worker"
        assert task.repo_url == REPO
        assert task.status == TaskStatus.PENDING
        assert task.description.startswith("Fix failing tests")
        # job root: a standalone ratchet is its own job
        assert body["job_id"] == task.job_id == task.id

    async def test_branch_hint_folded_into_description(self, client):
        _register_worker()
        r = await client.post(
            "/api/ratchet",
            json={"repo_url": "https://github.com/org/repo", "task": "Add feature", "branch": "work/feat-x"},
        )
        assert r.status_code == 201, r.text
        task = router_module._tasks[r.json()["task_id"]]
        assert "work/feat-x" in task.description

    async def test_forwards_backend_and_model_target(self, client):
        _register_worker()
        r = await client.post(
            "/api/ratchet",
            json={
                "repo_url": REPO,
                "task": "Do work",
                "backend": "docker",
                "model_target": {"provider": "claude", "model": "claude-opus-4-8"},
            },
        )
        assert r.status_code == 201, r.text
        task = router_module._tasks[r.json()["task_id"]]
        assert task.backend == "docker"
        assert task.model_target is not None
        assert task.model_target.model == "claude-opus-4-8"

    async def test_empty_repo_url_rejected(self, client):
        _register_worker()
        r = await client.post("/api/ratchet", json={"repo_url": "   ", "task": "x"})
        assert r.status_code == 400

    async def test_empty_task_rejected(self, client):
        _register_worker()
        r = await client.post("/api/ratchet", json={"repo_url": REPO, "task": "   "})
        assert r.status_code == 400

    async def test_missing_fields_422(self, client):
        _register_worker()
        r = await client.post("/api/ratchet", json={"repo_url": REPO})
        assert r.status_code == 422
