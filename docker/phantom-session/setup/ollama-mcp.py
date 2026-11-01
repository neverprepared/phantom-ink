#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp", "httpx"]
# ///
"""Ollama <-> phantom-ink MCP gateway bridge.

Ollama isn't an MCP client, so this harness does the translation: it lists
the gateway's tools, hands their schemas to an Ollama model as
function-calling tools, executes the tool_calls the model emits against the
gateway, and feeds the results back — an interactive chat loop where the
local/host model can reach every gateway tool scoped to the session's
profile.

Run:  ollama-mcp            (installed on PATH in the session image)
      ./ollama-mcp.py       (uv auto-installs mcp + httpx on first run)

Env (baked into the session container by the daemon):
  PHANTOM_GATEWAY_URL    streamable-HTTP MCP endpoint
  PHANTOM_GATEWAY_TOKEN  Tier-0 bearer token, scoped to the profile
  OLLAMA_HOST            inference endpoint (host or remote daemon)
  MODEL / CLAUDE_MODEL   model name (needs tool/function-calling support)
"""
import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

GATEWAY = os.environ.get("PHANTOM_GATEWAY_URL")
TOKEN = os.environ.get("PHANTOM_GATEWAY_TOKEN")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434")
MODEL = os.environ.get("MODEL") or os.environ.get("CLAUDE_MODEL") or "qwen3:8b"

if not GATEWAY or not TOKEN:
    sys.exit(
        "ollama-mcp: PHANTOM_GATEWAY_URL / PHANTOM_GATEWAY_TOKEN not set — "
        "this session has no MCP gateway wired. Use `ollama run <model>` for "
        "a plain chat, or enable the gateway for this profile."
    )

HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def to_ollama_tools(tools):
    return [{
        "type": "function",
        "function": {
            "name": t.name,
            "description": (t.description or "")[:900],
            "parameters": t.inputSchema or {"type": "object", "properties": {}},
        },
    } for t in tools]


async def chat(client, messages, tools):
    r = await client.post(f"{OLLAMA}/api/chat", json={
        "model": MODEL, "messages": messages, "tools": tools, "stream": False,
    })
    r.raise_for_status()
    return r.json()["message"]


async def main():
    async with streamablehttp_client(GATEWAY, headers=HEADERS) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"gateway: {len(tools)} tools available - model: {MODEL}")
            print("type a prompt (or 'tools' to list, 'quit' to exit)\n")
            ollama_tools = to_ollama_tools(tools)
            history = []
            async with httpx.AsyncClient(timeout=300) as client:
                while True:
                    try:
                        user = input("you> ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        return
                    if not user:
                        continue
                    if user in ("quit", "exit"):
                        return
                    if user == "tools":
                        for t in tools:
                            print(f"  {t.name}")
                        continue
                    history.append({"role": "user", "content": user})
                    for _hop in range(6):
                        msg = await chat(client, history, ollama_tools)
                        calls = msg.get("tool_calls") or []
                        if not calls:
                            print(f"\n{MODEL}> {(msg.get('content') or '').strip()}\n")
                            history.append(msg)
                            break
                        history.append(msg)
                        for tc in calls:
                            fn = tc["function"]["name"]
                            args = tc["function"].get("arguments") or {}
                            if isinstance(args, str):
                                args = json.loads(args or "{}")
                            print(f"  -> calling {fn}({json.dumps(args)[:100]})")
                            res = await session.call_tool(fn, args)
                            text = "".join(
                                c.text for c in res.content if getattr(c, "text", None)
                            )
                            history.append(
                                {"role": "tool", "content": text[:4000], "tool_name": fn}
                            )


asyncio.run(main())
