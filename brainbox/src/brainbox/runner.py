"""Brainbox runner — connects out to a central API, registers what it can
execute, long-polls for work items, and runs them via the local lifecycle.

The runner is a brainbox process operating in "execute only" mode: it has
the same backend code (docker, utm) as the API but no FastAPI server. It
never holds plaintext credentials — credential sealing during a session
create still flows back to the laptop's cc poll daemon, just via the
central API as the relay.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from typing import Any

import httpx

from .log import get_logger

log = get_logger()


def detect_capabilities() -> dict[str, bool]:
    """Detect what backends this host can execute. Conservative: only mark
    a backend supported if we can prove it."""
    caps = {"docker": False, "utm": False}

    try:
        import docker as docker_sdk

        client = docker_sdk.from_env()
        client.ping()
        caps["docker"] = True
    except Exception:
        pass

    if sys.platform == "darwin" and shutil.which("osascript"):
        # UTM uses AppleScript automation via mcp-utm. We don't probe UTM.app
        # presence — too brittle. AppleScript availability is the signal.
        caps["utm"] = True

    return caps


class Runner:
    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        name: str,
        capabilities: dict[str, bool],
        tags: list[str] | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.name = name
        self.capabilities = capabilities
        self.tags = tags or []
        # httpx client must outlive the loop; timeouts vary per call.
        self._client: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"X-API-Key": self.api_key},
                timeout=None,
            )
        return self._client

    async def register(self) -> None:
        resp = await self._http.post(
            f"{self.api_url}/api/runners/register",
            json={
                "name": self.name,
                "capabilities": self.capabilities,
                "tags": self.tags,
                "version": "0.1.0",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        log.info(
            "runner.registered",
            metadata={
                "name": self.name,
                "capabilities": self.capabilities,
                "tags": self.tags,
            },
        )

    async def run_forever(self) -> None:
        await self.register()
        # Make seal-via-central-api available to the docker backend when it
        # encounters delivery=bundle. The backend reads these env vars.
        os.environ["BRAINBOX_CC_API_URL"] = self.api_url
        os.environ["BRAINBOX_CC_API_KEY"] = self.api_key

        while True:
            try:
                resp = await self._http.get(
                    f"{self.api_url}/api/runners/{self.name}/pending",
                    timeout=35.0,
                )
                if resp.status_code == 204:
                    continue
                if resp.status_code == 401:
                    log.error("runner.unauthorized")
                    await asyncio.sleep(10)
                    continue
                if resp.status_code == 404:
                    # Server forgot us (restart?). Re-register.
                    log.warning("runner.unknown_to_server")
                    await self.register()
                    continue
                resp.raise_for_status()
                work = resp.json()
                log.info(
                    "runner.work_received",
                    metadata={"id": work["id"], "kind": work["kind"]},
                )
                result = await self._execute(work)
                up = await self._http.post(
                    f"{self.api_url}/api/runners/{self.name}/result/{work['id']}",
                    json=result,
                    timeout=30.0,
                )
                if up.status_code != 200:
                    log.warning(
                        "runner.result_post_failed",
                        metadata={
                            "id": work["id"],
                            "status": up.status_code,
                            "body": up.text[:200],
                        },
                    )
                else:
                    log.info(
                        "runner.work_completed",
                        metadata={"id": work["id"], "ok": result.get("ok", False)},
                    )
            except httpx.RequestError as exc:
                log.warning("runner.request_error", metadata={"reason": str(exc)})
                await asyncio.sleep(2.0)
            except KeyboardInterrupt:
                log.info("runner.stopped")
                return
            except Exception as exc:
                log.error("runner.unexpected", metadata={"reason": str(exc)})
                await asyncio.sleep(2.0)

    async def _execute(self, work: dict[str, Any]) -> dict[str, Any]:
        kind = work.get("kind", "")
        payload = work.get("payload", {})
        if kind == "session.create":
            return await self._do_session_create(payload)
        return {"ok": False, "error": f"unsupported work kind: {kind!r}"}

    async def _do_session_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        # The runner runs the same provision code as the API host, but with
        # runner=None so it executes in-process instead of re-dispatching.
        # delivery=bundle is the default for runner-dispatched sessions —
        # the API host has no cred filesystem, and the runner can't ship its
        # creds either.
        from .lifecycle import run_pipeline

        payload = {**payload}
        payload.pop("runner", None)
        if not payload.get("delivery"):
            payload["delivery"] = "bundle"

        # repo is a Pydantic model on the wire as a dict; lifecycle handles None.
        if payload.get("repo") and isinstance(payload["repo"], dict):
            from .models_api import RepoConfig

            try:
                payload["repo"] = RepoConfig(**payload["repo"])
            except Exception as exc:
                return {"ok": False, "error": f"invalid repo config: {exc}"}
        # token comes through as None or a dict; rebuild if dict.
        if payload.get("token") and isinstance(payload["token"], dict):
            from .models import Token

            try:
                payload["token"] = Token(**payload["token"])
            except Exception as exc:
                return {"ok": False, "error": f"invalid token: {exc}"}

        try:
            ctx = await run_pipeline(**payload)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        # Mark the session as runner-owned so the API can route follow-up ops.
        ctx.runner_name = self.name
        return {"ok": True, "data": ctx.model_dump(mode="json")}


def serve(
    *,
    api_url: str,
    api_key: str,
    name: str,
    tags: list[str] | None = None,
) -> None:
    """Entry point for `brainbox runner …`."""
    if not api_url:
        raise RuntimeError("--api required")
    if not api_key:
        raise RuntimeError("--api-key or $CL_API_KEY required")
    if not name:
        raise RuntimeError("--name required")

    caps = detect_capabilities()
    if not any(caps.values()):
        raise RuntimeError(
            "no backends detected — install docker and/or be on macOS for utm"
        )
    runner = Runner(
        api_url=api_url,
        api_key=api_key,
        name=name,
        capabilities=caps,
        tags=tags,
    )
    log.info("runner.starting", metadata={"name": name, "api_url": api_url})
    asyncio.run(runner.run_forever())
