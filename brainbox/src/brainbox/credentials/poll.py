"""Laptop command-center daemon (Phase 5) — polls a remote API for pending
bundle requests, seals them locally, posts ciphertext back.

The laptop only needs outbound HTTPS to the API; no inbound port required.
The plaintext credentials never leave this process.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..log import get_logger
from .build import build_sealed_bundle

log = get_logger()

PENDING_PATH = "/api/credentials/pending"


def _seal_one(api_url: str, headers: dict[str, str], req: dict[str, Any]) -> int:
    ciphertext = build_sealed_bundle(
        workspace_profile=req.get("workspace_profile"),
        workspace_home=req.get("workspace_home"),
        recipient=req["recipient"],
    )
    upload = httpx.post(
        f"{api_url}/api/credentials/{req['id']}/sealed",
        content=ciphertext,
        headers={**headers, "Content-Type": "application/octet-stream"},
        timeout=30.0,
    )
    upload.raise_for_status()
    return len(ciphertext)


def poll_loop(
    api_url: str,
    api_key: str,
    *,
    poll_timeout: float = 35.0,
    error_backoff: float = 2.0,
) -> None:
    """Run the poll/seal loop forever. Returns only on KeyboardInterrupt."""
    api_url = api_url.rstrip("/")
    headers = {"X-API-Key": api_key}
    log.info("cc_poll.start", metadata={"api_url": api_url})
    while True:
        try:
            resp = httpx.get(
                f"{api_url}{PENDING_PATH}", headers=headers, timeout=poll_timeout
            )
            if resp.status_code == 204:
                continue
            if resp.status_code == 401:
                log.error("cc_poll.unauthorized")
                time.sleep(error_backoff * 5)
                continue
            resp.raise_for_status()
            req = resp.json()
            log.info(
                "cc_poll.request_received",
                metadata={"id": req["id"], "profile": req.get("workspace_profile") or ""},
            )
            try:
                size = _seal_one(api_url, headers, req)
            except Exception as exc:
                log.error(
                    "cc_poll.seal_failed",
                    metadata={"id": req["id"], "reason": str(exc)},
                )
                # Best-effort cancel so the producer doesn't hang.
                try:
                    httpx.post(
                        f"{api_url}/api/credentials/{req['id']}/sealed",
                        content=b"",
                        headers={**headers, "Content-Type": "application/octet-stream"},
                        timeout=5.0,
                    )
                except Exception:
                    pass
                time.sleep(error_backoff)
                continue
            log.info("cc_poll.sealed", metadata={"id": req["id"], "bytes": size})
        except httpx.RequestError as exc:
            log.warning("cc_poll.request_error", metadata={"reason": str(exc)})
            time.sleep(error_backoff)
        except KeyboardInterrupt:
            log.info("cc_poll.stopped")
            return
        except Exception as exc:
            log.error("cc_poll.unexpected", metadata={"reason": str(exc)})
            time.sleep(error_backoff)
