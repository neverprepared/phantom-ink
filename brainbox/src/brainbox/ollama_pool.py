"""Ollama instance pool — routes requests across all healthy Ollama backends.

Instances are sourced from two places:
  1. The configured fallback (settings.ollama.host) — always present, plain
     HTTP, no auth.
  2. Runners that register with capabilities["ollama"] = True and an
     ``ollama_proxy_port`` field. These are reached over HTTPS at
     ``https://{host}:{ollama_proxy_port}`` and authenticated with the
     brainbox API key (``X-API-Key`` header). The runner's self-signed cert
     is not verified — auth is gated by the API key.

The pool background task health-checks every instance every 30 seconds and
marks them healthy/unhealthy. pick() returns the healthy instance with the
lowest in-flight request count, falling back to the configured host if the
runner pool is empty or all unhealthy.

Transport: the health check uses :func:`brainbox.ollama.acurl_request`
(curl subprocess) instead of httpx — see ``ollama.py`` docstring for the
Python 3.14 + macOS EHOSTUNREACH gotcha.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .config import settings
from .log import get_logger

log = get_logger()

_HEALTH_INTERVAL = 30  # seconds between sweeps
_FALLBACK_NAME = "__fallback__"


@dataclass
class OllamaInstance:
    runner_name: str
    url: str           # e.g. "https://192.168.1.10:11435" (runner proxy)
    healthy: bool = False
    models: list[str] = field(default_factory=list)
    last_checked: float = 0.0
    in_flight: int = 0
    # Auth header value sent on every request — empty for the fallback.
    api_key: str = ""
    # Whether to verify the upstream TLS cert. False for self-signed
    # runner proxies; True (default) for everything else.
    verify_tls: bool = True

    def request_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}


class OllamaPool:
    def __init__(self) -> None:
        self._instances: dict[str, OllamaInstance] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._health_loop())
        # Always ensure the fallback instance is registered
        fallback_url = settings.ollama.host
        if _FALLBACK_NAME not in self._instances:
            self._instances[_FALLBACK_NAME] = OllamaInstance(
                runner_name=_FALLBACK_NAME,
                url=fallback_url,
            )

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    # ------------------------------------------------------------------ #
    # Runner integration                                                   #
    # ------------------------------------------------------------------ #

    async def add_runner(
        self,
        runner_name: str,
        host: str,
        port: int,
        *,
        api_key: str = "",
        scheme: str = "https",
        verify_tls: bool = False,
    ) -> None:
        url = f"{scheme}://{host}:{port}"
        async with self._lock:
            existing = self._instances.get(runner_name)
            if existing and existing.url == url and existing.api_key == api_key:
                return  # no change
            self._instances[runner_name] = OllamaInstance(
                runner_name=runner_name,
                url=url,
                api_key=api_key,
                verify_tls=verify_tls,
            )
        log.info("ollama_pool.runner_added", metadata={"runner": runner_name, "url": url})
        # Eagerly health-check the new instance
        asyncio.create_task(self._check_one(runner_name))

    async def remove_runner(self, runner_name: str) -> None:
        async with self._lock:
            removed = self._instances.pop(runner_name, None)
        if removed:
            log.info("ollama_pool.runner_removed", metadata={"runner": runner_name})

    # ------------------------------------------------------------------ #
    # Selection                                                            #
    # ------------------------------------------------------------------ #

    def pick(self, *, runner_name: str | None = None) -> OllamaInstance | None:
        """Return the best healthy instance.

        If runner_name is given, return that specific instance (or None if
        it's not registered or unhealthy). Otherwise pick the healthy instance
        with the lowest in_flight count.
        """
        if runner_name:
            inst = self._instances.get(runner_name)
            return inst if inst and inst.healthy else None

        candidates = [i for i in self._instances.values() if i.healthy]
        if not candidates:
            # Nothing healthy — return fallback anyway so callers get a 502
            # from the real error rather than a confusing "no instances" message
            return self._instances.get(_FALLBACK_NAME)
        return min(candidates, key=lambda i: i.in_flight)

    def all_instances(self) -> list[OllamaInstance]:
        return list(self._instances.values())

    # ------------------------------------------------------------------ #
    # In-flight tracking                                                   #
    # ------------------------------------------------------------------ #

    def acquire(self, instance: OllamaInstance) -> None:
        instance.in_flight = max(0, instance.in_flight) + 1

    def release(self, instance: OllamaInstance) -> None:
        instance.in_flight = max(0, instance.in_flight - 1)

    # ------------------------------------------------------------------ #
    # Health checking                                                      #
    # ------------------------------------------------------------------ #

    async def _health_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_HEALTH_INTERVAL)
                names = list(self._instances.keys())
                await asyncio.gather(*[self._check_one(n) for n in names], return_exceptions=True)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("ollama_pool.health_loop_error", metadata={"reason": str(exc)})

    async def _check_one(self, runner_name: str) -> None:
        inst = self._instances.get(runner_name)
        if not inst:
            return
        try:
            from .ollama import acurl_request
            status, body = await acurl_request(
                "GET", inst.url, "/api/tags",
                headers=inst.request_headers(),
                verify=inst.verify_tls,
                timeout=5,
            )
            if status == 200:
                import json as _json
                data = _json.loads(body)
                inst.models = [m["name"] for m in data.get("models", [])]
                inst.healthy = True
            else:
                inst.healthy = False
                inst.models = []
                log.warning("ollama_pool.health_non200", metadata={
                    "runner": runner_name, "url": inst.url,
                    "status": status, "body": body[:200],
                })
        except Exception as exc:
            inst.healthy = False
            inst.models = []
            log.warning("ollama_pool.health_exception", metadata={
                "runner": runner_name, "url": inst.url,
                "reason": f"{type(exc).__name__}: {exc}",
            })
        inst.last_checked = time.time()
        log.debug(
            "ollama_pool.health_checked",
            metadata={"runner": runner_name, "healthy": inst.healthy, "models": len(inst.models)},
        )


# Module-level singleton
_pool: OllamaPool | None = None


def get_pool() -> OllamaPool:
    global _pool
    if _pool is None:
        _pool = OllamaPool()
    return _pool
