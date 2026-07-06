"""Tests for the WebSocket-over-nc relay (terminal_relay.NcWebSocket).

Runs a real websockets server on loopback and relays through a real nc
subprocess — exercising the handshake, masked client frames, server frame
parsing, ping/pong, and close. Skipped when nc isn't available.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest
import websockets

from brainbox.terminal_relay import OP_BINARY, OP_TEXT, NcWebSocket, RelayError

NC = "/usr/bin/nc" if shutil.which("/usr/bin/nc") else shutil.which("nc")

pytestmark = pytest.mark.skipif(NC is None, reason="nc not available")


@pytest.fixture
async def echo_server():
    """A real WS server: echoes data frames; sends one ping mid-stream."""

    async def handler(ws):
        try:
            first = True
            async for msg in ws:
                if first:
                    await ws.ping(b"beat")  # exercise the relay's pong reply
                    first = False
                await ws.send(msg)
        except websockets.ConnectionClosed:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0, subprotocols=["tty"])
    port = server.sockets[0].getsockname()[1]
    yield port
    server.close()
    await server.wait_closed()


async def test_handshake_and_text_echo(echo_server):
    ws = await NcWebSocket.connect("127.0.0.1", echo_server, "/", subprotocols=["tty"], nc_path=NC)
    try:
        assert ws.subprotocol == "tty"
        await ws.send_text('{"AuthToken":"","columns":80,"rows":24}')
        frame = await asyncio.wait_for(ws.recv(), timeout=5)
        assert frame is not None
        opcode, payload = frame
        assert opcode == OP_TEXT
        assert payload == b'{"AuthToken":"","columns":80,"rows":24}'
    finally:
        await ws.close()


async def test_binary_echo_and_ping_survival(echo_server):
    ws = await NcWebSocket.connect("127.0.0.1", echo_server, "/", subprotocols=["tty"], nc_path=NC)
    try:
        blob = bytes(range(256)) * 300  # 76.8 KB — exercises 16-bit length frames
        await ws.send_bytes(blob)
        frame = await asyncio.wait_for(ws.recv(), timeout=5)
        assert frame is not None
        opcode, payload = frame
        assert opcode == OP_BINARY
        assert payload == blob
        # ping arrived before the echo; recv() must have answered it without
        # surfacing it — a second round-trip proves the stream stayed healthy
        await ws.send_text("still-alive")
        frame2 = await asyncio.wait_for(ws.recv(), timeout=5)
        assert frame2 is not None and frame2[1] == b"still-alive"
    finally:
        await ws.close()


async def test_server_close_yields_none(echo_server):
    async def closer(ws):
        await ws.close(code=1000)

    server = await websockets.serve(closer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        ws = await NcWebSocket.connect("127.0.0.1", port, "/", nc_path=NC)
        frame = await asyncio.wait_for(ws.recv(), timeout=5)
        assert frame is None
        await ws.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_handshake_rejection_raises():
    # A plain HTTP server rejects the upgrade → RelayError, not a hang
    import http.server
    import threading

    class NoUpgrade(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), NoUpgrade)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with pytest.raises(RelayError):
            await NcWebSocket.connect("127.0.0.1", srv.server_address[1], "/ws", nc_path=NC)
    finally:
        srv.shutdown()
