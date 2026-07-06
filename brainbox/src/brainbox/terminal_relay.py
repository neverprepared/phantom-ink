"""WebSocket-over-nc relay for the terminal proxy.

Why this exists: on macOS 26 the daemon's Python process is denied Local
Network access (TCC), so EVERY Python-created socket to a LAN destination —
httpx, stdlib http.client, raw socket.create_connection, websockets — gets a
spurious ``OSError 65 (No route to host)``. Apple-signed binaries are exempt,
which is why curl subprocesses work (ollama.py, the terminal HTTP proxy) while
in-process sockets fail. Live-verified against a runner at 192.168.87.101.

For WebSockets there is no curl equivalent, so this module speaks the
WebSocket client protocol (RFC 6455) over a raw TCP pipe provided by
``/usr/bin/nc`` (Apple-signed → exempt). The framing needed for ttyd is small:
handshake, masked client frames, unmasked server frames, ping/pong/close.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import struct

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Opcodes
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class RelayError(Exception):
    """Handshake or transport failure in the nc relay."""


def _encode_frame(opcode: int, payload: bytes) -> bytes:
    """One masked client→server frame (client frames MUST be masked)."""
    header = bytearray([0x80 | opcode])  # FIN + opcode
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    mask = os.urandom(4)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes(header) + masked


class NcWebSocket:
    """A WebSocket client connection tunnelled through an ``nc`` subprocess."""

    def __init__(self, proc: asyncio.subprocess.Process, subprotocol: str | None):
        self._proc = proc
        self.subprotocol = subprotocol

    @classmethod
    async def connect(
        cls,
        host: str,
        port: int,
        path: str,
        *,
        subprotocols: list[str] | None = None,
        timeout: float = 8.0,
        nc_path: str = "/usr/bin/nc",
    ) -> "NcWebSocket":
        proc = await asyncio.create_subprocess_exec(
            nc_path, host, str(port),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            lines = [
                f"GET {path} HTTP/1.1",
                f"Host: {host}:{port}",
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Key: {key}",
                "Sec-WebSocket-Version: 13",
            ]
            if subprotocols:
                lines.append(f"Sec-WebSocket-Protocol: {', '.join(subprotocols)}")
            proc.stdin.write(("\r\n".join(lines) + "\r\n\r\n").encode())
            await proc.stdin.drain()

            # Read the handshake response head.
            head = await asyncio.wait_for(proc.stdout.readuntil(b"\r\n\r\n"), timeout)
            status_line, *header_lines = head.decode("latin-1").split("\r\n")
            if " 101 " not in status_line + " ":
                raise RelayError(f"handshake rejected: {status_line.strip()[:120]}")
            headers = {}
            for line in header_lines:
                name, sep, value = line.partition(":")
                if sep:
                    headers[name.strip().lower()] = value.strip()
            expect = base64.b64encode(
                hashlib.sha1((key + _WS_GUID).encode()).digest()
            ).decode()
            if headers.get("sec-websocket-accept") != expect:
                raise RelayError("handshake accept-key mismatch")
            return cls(proc, headers.get("sec-websocket-protocol"))
        except (Exception, asyncio.TimeoutError) as exc:
            proc.kill()
            await proc.wait()
            if isinstance(exc, RelayError):
                raise
            raise RelayError(str(exc) or type(exc).__name__) from exc

    async def _read_exact(self, n: int) -> bytes:
        data = await self._proc.stdout.readexactly(n)
        return data

    async def send_text(self, text: str) -> None:
        self._proc.stdin.write(_encode_frame(OP_TEXT, text.encode()))
        await self._proc.stdin.drain()

    async def send_bytes(self, data: bytes) -> None:
        self._proc.stdin.write(_encode_frame(OP_BINARY, data))
        await self._proc.stdin.drain()

    async def recv(self) -> tuple[int, bytes] | None:
        """Next data frame as (opcode, payload); None once the peer closes.

        Ping is answered with pong internally; pong frames are swallowed.
        Fragmented messages are reassembled (ttyd doesn't fragment, but be
        correct anyway).
        """
        message_op: int | None = None
        buffer = b""
        while True:
            try:
                b1, b2 = await self._read_exact(2)
            except (asyncio.IncompleteReadError, ConnectionError):
                return None
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                (length,) = struct.unpack(">H", await self._read_exact(2))
            elif length == 127:
                (length,) = struct.unpack(">Q", await self._read_exact(8))
            mask = await self._read_exact(4) if masked else b""
            payload = await self._read_exact(length) if length else b""
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

            if opcode == OP_CLOSE:
                # Echo the close and report EOF.
                try:
                    self._proc.stdin.write(_encode_frame(OP_CLOSE, payload[:2]))
                    await self._proc.stdin.drain()
                except Exception:
                    pass
                return None
            if opcode == OP_PING:
                self._proc.stdin.write(_encode_frame(OP_PONG, payload))
                await self._proc.stdin.drain()
                continue
            if opcode == OP_PONG:
                continue

            if opcode in (OP_TEXT, OP_BINARY):
                message_op = opcode
                buffer = payload
            elif opcode == 0x0 and message_op is not None:  # continuation
                buffer += payload
            else:
                continue  # unknown frame — skip

            if fin:
                op, out = message_op, buffer
                return (op, out)

    async def close(self) -> None:
        try:
            self._proc.stdin.write(_encode_frame(OP_CLOSE, b"\x03\xe8"))  # 1000
            await self._proc.stdin.drain()
        except Exception:
            pass
        try:
            self._proc.kill()
        except ProcessLookupError:
            pass
        await self._proc.wait()
