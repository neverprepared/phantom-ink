"""Supervisor SDK — synchronous wrapper around the brainbox hub API.

Shaped to be A2A-adjacent: list_available_agents returns capability descriptors
(Agent Cards in A2A terms), spawn_worker produces a Task object with lifecycle,
message_agent is the coordination primitive. A future adapter can map these to
the A2A spec without changing call sites.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass
class AgentDefinition:
    name: str
    description: str
    category: str
    spawn_mode: str          # "container" | "subagent"
    capabilities: list[str] = field(default_factory=list)
    hardened: bool = False
    persistent: bool = False


@dataclass
class TaskHandle:
    task_id: str
    status: str              # "pending" | "running" | "completed" | "failed" | "cancelled"
    result: Any = None
    error: str | None = None


class SupervisorError(Exception):
    pass


class Supervisor:
    """Synchronous agent-to-agent coordination via the brainbox hub API.

    Args:
        hub_url: Base URL for the brainbox API, e.g. ``http://localhost:9999``.
        agent_token: Bearer token for hub authentication.
    """

    def __init__(self, hub_url: str, agent_token: str) -> None:
        self._base = hub_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {agent_token}",
            "Content-Type": "application/json",
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get(self, path: str) -> Any:
        r = requests.get(f"{self._base}{path}", headers=self._headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> Any:
        r = requests.post(
            f"{self._base}{path}", json=payload, headers=self._headers, timeout=30
        )
        r.raise_for_status()
        return r.json()

    # ── Public API ─────────────────────────────────────────────────────────────

    def list_available_agents(self) -> list[AgentDefinition]:
        """A2A-style capability discovery — wraps GET /api/hub/agents."""
        raw = self._get("/api/hub/agents")
        return [
            AgentDefinition(
                name=a.get("name", ""),
                description=a.get("description", ""),
                category=a.get("category", ""),
                spawn_mode=a.get("spawn_mode", "container"),
                capabilities=a.get("capabilities") or [],
                hardened=bool(a.get("hardened")),
                persistent=bool(a.get("persistent")),
            )
            for a in (raw if isinstance(raw, list) else [])
        ]

    def spawn_worker(
        self,
        description: str,
        agent: str = "worker",
        repo_url: str | None = None,
        workspace_profile: str | None = None,
        workspace_home: str | None = None,
        wait: bool = False,
        timeout_sec: int = 300,
    ) -> TaskHandle:
        """A2A-style task delegation.

        Args:
            description: Task description sent to the worker.
            agent: Agent name (defaults to ``"worker"``).
            repo_url: Optional repository URL for the worker to clone.
            workspace_profile: Profile to run under.
            workspace_home: Workspace home directory.
            wait: When ``True``, block until the task reaches a terminal state.
            timeout_sec: Maximum seconds to wait when ``wait=True``.

        Returns:
            :class:`TaskHandle` describing the submitted (or completed) task.
        """
        payload: dict[str, Any] = {
            "description": description,
            "agent_name": agent,
        }
        if repo_url:
            payload["repo_url"] = repo_url
        if workspace_profile:
            payload["workspace_profile"] = workspace_profile
        if workspace_home:
            payload["workspace_home"] = workspace_home

        raw = self._post("/api/hub/tasks", payload)
        handle = TaskHandle(task_id=raw.get("id", ""), status=raw.get("status", "pending"))

        if wait:
            return self.wait_for_task(handle.task_id, timeout_sec=timeout_sec)
        return handle

    def wait_for_task(self, task_id: str, timeout_sec: int = 300) -> TaskHandle:
        """Block until the task reaches a terminal state or the timeout expires.

        Args:
            task_id: Hub task ID returned by :meth:`spawn_worker`.
            timeout_sec: Maximum seconds to wait before returning with
                ``status="timeout"``.

        Returns:
            :class:`TaskHandle` with the final status and result/error populated.
        """
        deadline = time.monotonic() + timeout_sec
        while True:
            try:
                raw = self._get(f"/api/hub/tasks/{task_id}")
            except Exception:
                # Swallow transient errors and keep polling.
                raw = {}

            status = raw.get("status", "")
            if status == "completed":
                return TaskHandle(task_id=task_id, status="completed", result=raw.get("result"))
            if status in ("failed", "cancelled"):
                err = raw.get("error")
                if isinstance(err, dict):
                    err = err.get("message") or str(err)
                return TaskHandle(task_id=task_id, status="failed", error=str(err or "unknown error"))

            if time.monotonic() >= deadline:
                return TaskHandle(task_id=task_id, status="timeout")

            time.sleep(3)

    def message_agent(
        self,
        recipient: str,
        body: str,
        payload: dict | None = None,
    ) -> dict:
        """Send a directed agent-to-agent message via POST /api/hub/messages.

        Args:
            recipient: Name of the target session or agent role.
            body: Human-readable message body.
            payload: Optional extra fields merged into the message payload.

        Returns:
            The raw message dict from the hub API.
        """
        msg_payload: dict[str, Any] = {"body": body}
        if payload:
            msg_payload.update(payload)
        return self._post(
            "/api/hub/messages",
            {"recipient": recipient, "type": "text", "payload": msg_payload},
        )
