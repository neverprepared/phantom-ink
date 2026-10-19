"""Minimal MCP stdio client -- enough to call one tool and read the result.

`macos-mcp` is stdio-only (macos-ecosystem-mcp Sources/macos-mcp/App.swift:36):
there is no CLI mode, so reaching Apple Reminders from a scheduled shell job
means speaking JSON-RPC to it directly.

Hand-rolled on purpose rather than depending on the `mcp` package: this repo
already carries a hard `mcp<2` pin because 2.0 dropped `Server.list_tools` and
broke the gateway on rebuild. A ~100-line client over line-delimited JSON has no
such exposure, and the subset of the protocol used here (initialize ->
notifications/initialized -> tools/call) is stable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from typing import Any, Self

PROTOCOL_VERSION = "2024-11-05"

# EventKit permission is requested at server startup, and a cold start on a
# machine that has to resolve TCC is not instant.
DEFAULT_TIMEOUT_S = 25.0


class McpError(RuntimeError):
    """The server refused, errored, or could not be reached."""


class ToolError(McpError):
    """The tool ran and reported failure (isError), as opposed to a transport fault."""


def find_server(explicit: str | None = None) -> str:
    """Locate the macos-mcp binary."""
    for cand in (explicit, os.environ.get("CL_INBOX_MACOS_MCP")):
        if cand:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
            raise McpError(f"macos-mcp not executable at {cand}")
    found = shutil.which("macos-mcp")
    if found:
        return found
    raise McpError("macos-mcp not found on PATH. Install it (brew) or set CL_INBOX_MACOS_MCP.")


class McpStdioClient:
    """One short-lived MCP session over a subprocess's stdio.

    Used as a context manager; the server is started on enter and reaped on
    exit, including on error.
    """

    def __init__(self, command: str, *, timeout: float = DEFAULT_TIMEOUT_S):
        self.command = command
        self.timeout = timeout
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 0
        self._stderr: list[str] = []

    # -- lifecycle -------------------------------------------------------------

    def __enter__(self) -> Self:
        self._proc = subprocess.Popen(
            [self.command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        # The server logs to stderr; drain it on a thread so a chatty startup
        # can never fill the pipe and deadlock the handshake.
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._initialize()
        return self

    def __exit__(self, *_exc: object) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        self._proc = None

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                if len(self._stderr) < 200:
                    self._stderr.append(line.rstrip())
        except Exception:
            pass

    @property
    def server_log(self) -> str:
        return "\n".join(self._stderr[-20:])

    # -- transport -------------------------------------------------------------

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpError("server not running")
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except BrokenPipeError as exc:
            raise McpError(f"server closed stdin; log: {self.server_log}") from exc

    def _read_result(self, want_id: int) -> dict[str, Any]:
        """Read until the response with want_id arrives.

        Notifications and responses to other ids are skipped rather than
        treated as errors, so an unsolicited server message cannot desync us.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise McpError("server not running")

        box: dict[str, Any] = {}

        def pump() -> None:
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("id") != want_id:
                        continue
                    box["msg"] = msg
                    return
            except Exception as exc:
                box["exc"] = exc

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        t.join(self.timeout)
        if t.is_alive():
            raise McpError(f"timed out after {self.timeout}s waiting for id {want_id}")
        if "exc" in box:
            raise McpError(f"read failed: {box['exc']}")
        if "msg" not in box:
            raise McpError(f"server exited before responding; log: {self.server_log}")

        msg = box["msg"]
        if "error" in msg:
            err = msg["error"]
            raise McpError(f"rpc error {err.get('code')}: {err.get('message')}")
        return msg.get("result") or {}

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self._read_result(rid)

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "phantom-inbox", "version": "1"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # -- tools -----------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call one tool and return its text content.

        Raises ToolError when the server reports isError, so a permission
        failure surfaces as a hard error rather than a silent no-op.
        """
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        text = "\n".join(
            part.get("text", "")
            for part in (result.get("content") or [])
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
        if result.get("isError"):
            raise ToolError(f"{name} failed: {text or 'no detail'}")
        return text


def call_once(tool: str, arguments: dict[str, Any], *, command: str | None = None) -> str:
    """Start the server, call one tool, shut it down."""
    with McpStdioClient(find_server(command)) as client:
        return client.call_tool(tool, arguments)
