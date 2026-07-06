"""Tests for the /t/{session}/ terminal HTTP proxy.

The proxy must use stdlib http.client (not httpx): the long-running daemon on
macOS/Python 3.14 hits spurious OSError 65 on httpx connections to LAN
destinations (the ollama.py known issue), which blanked runner-hosted session
terminals. These tests run the proxy against a real local HTTP server.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from brainbox.models import SessionContext


@pytest.fixture
def ttyd_stub():
    """A real HTTP server standing in for ttyd (serves under /t/<name>/)."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/t/term-test/"):
                body = b"<html>ttyd-index</html>" if self.path.endswith("/") else b"tok-123"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):  # silence
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()


@pytest.fixture
def term_session(ttyd_stub):
    """Register an in-memory session whose ttyd is the stub server."""
    from brainbox import lifecycle

    ctx = SessionContext(
        session_name="term-test",
        container_name="developer-term-test",
        port=ttyd_stub,
        created_at=0,
        ttl=0,
    )
    lifecycle._sessions["term-test"] = ctx
    yield ctx
    lifecycle._sessions.pop("term-test", None)


async def test_proxies_index_and_assets(client, term_session):
    async with client as c:
        r = await c.get("/t/term-test/")
        assert r.status_code == 200
        assert b"ttyd-index" in r.content
        r = await c.get("/t/term-test/token")
        assert r.status_code == 200
        assert r.content == b"tok-123"


async def test_unknown_session_404s(client):
    async with client as c:
        r = await c.get("/t/nope/token")
        assert r.status_code == 404


async def test_unreachable_returns_visible_html_not_blank(client):
    """When ttyd is unreachable the iframe must show an error page, not a
    blank JSON body (the white-page symptom)."""
    from brainbox import lifecycle

    ctx = SessionContext(
        session_name="term-dead",
        container_name="developer-term-dead",
        port=1,  # nothing listens on port 1
        created_at=0,
        ttl=0,
    )
    lifecycle._sessions["term-dead"] = ctx
    try:
        async with client as c:
            r = await c.get("/t/term-dead/token")  # non-empty path → single attempt
            assert r.status_code == 502
            assert "text/html" in r.headers["content-type"]
            assert b"not reachable" in r.content
    finally:
        lifecycle._sessions.pop("term-dead", None)
