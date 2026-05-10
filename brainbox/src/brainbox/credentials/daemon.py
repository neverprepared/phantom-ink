"""Command-center daemon — local sealing service for the bundle delivery flow.

Runs on the laptop next to (or instead of) the inline-on-API path. Exposes a
single endpoint, POST /seal, which takes a profile + recipient pubkey and
returns the sealed bundle bytes. The daemon is the only process that reads
plaintext credentials from disk; the API process can be elsewhere and never
touches the unsealed bytes.

Auth uses the existing brainbox API-key model: callers must present
X-API-Key matching the daemon's configured key.

Phase 4 keeps this local-only — daemon binds to 127.0.0.1 by default. Phase 5
will invert the topology so the daemon polls a remote API instead of being
called over the network.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from ..log import get_logger
from .build import build_sealed_bundle

log = get_logger()


class SealRequest(BaseModel):
    workspace_profile: str | None = None
    workspace_home: str | None = None
    recipient: str


def _expected_key() -> str:
    key = os.environ.get("BRAINBOX_CC_API_KEY") or os.environ.get("CL_API_KEY") or ""
    if not key:
        raise RuntimeError(
            "command-center daemon requires BRAINBOX_CC_API_KEY (or CL_API_KEY) to be set"
        )
    return key


def _require_api_key(x_api_key: str = Header(default="")) -> None:
    expected = _expected_key()
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid api key")


def create_app() -> FastAPI:
    app = FastAPI(title="brainbox command-center", version="1")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True}

    @app.post("/seal", response_class=Response)
    def seal_endpoint(
        body: SealRequest, _auth: None = Depends(_require_api_key)
    ) -> Response:
        if not body.recipient.startswith("age1"):
            raise HTTPException(status_code=400, detail="recipient must be an age1... pubkey")
        try:
            sealed = build_sealed_bundle(
                workspace_profile=body.workspace_profile,
                workspace_home=body.workspace_home,
                recipient=body.recipient,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        log.info(
            "cc.sealed",
            metadata={
                "profile": body.workspace_profile or "",
                "bytes": len(sealed),
            },
        )
        return Response(
            content=sealed,
            media_type="application/octet-stream",
            headers={"X-Bundle-Bytes": str(len(sealed))},
        )

    return app


def serve(host: str = "127.0.0.1", port: int = 9888) -> None:
    """Run the daemon under uvicorn (blocking)."""
    import uvicorn

    _expected_key()  # fail fast if API key isn't configured
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
