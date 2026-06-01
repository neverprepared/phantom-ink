"""Nginx fragment management for path-based terminal proxying.

When CL_NGINX_CONFIG_DIR is set, brainbox writes a per-session location block
into that directory whenever a session starts, and removes it when the session
is deleted. After each change it runs CL_NGINX_RELOAD_CMD to pick up the diff.

The parent nginx config must include the fragments directory:

    include /opt/homebrew/etc/nginx/brainbox/*.conf;

The directory must be writable by the user running brainbox.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from ..log import get_logger

log = get_logger()

_FRAGMENT_TEMPLATE = """\
# managed by brainbox — do not edit
location /t/{session_name}/ {{
    proxy_pass http://127.0.0.1:{port}/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 7200s;
    proxy_send_timeout 7200s;
}}
"""


def _fragment_path(config_dir: str, session_name: str) -> Path:
    return Path(config_dir) / f"{session_name}.conf"


def write_fragment(session_name: str, port: int, config_dir: str, reload_cmd: str) -> None:
    """Write nginx location fragment and reload. Safe to call from sync context."""
    try:
        d = Path(config_dir)
        d.mkdir(parents=True, exist_ok=True)
        _fragment_path(config_dir, session_name).write_text(
            _FRAGMENT_TEMPLATE.format(session_name=session_name, port=port)
        )
        _reload(reload_cmd)
        log.info("nginx.fragment_written", metadata={"session": session_name, "port": port})
    except Exception as exc:
        log.warning("nginx.fragment_write_failed", metadata={"session": session_name, "reason": str(exc)})


def remove_fragment(session_name: str, config_dir: str, reload_cmd: str) -> None:
    """Remove nginx location fragment and reload. Safe to call from sync context."""
    try:
        p = _fragment_path(config_dir, session_name)
        if p.exists():
            p.unlink()
            _reload(reload_cmd)
            log.info("nginx.fragment_removed", metadata={"session": session_name})
    except Exception as exc:
        log.warning("nginx.fragment_remove_failed", metadata={"session": session_name, "reason": str(exc)})


async def async_write_fragment(session_name: str, port: int, config_dir: str, reload_cmd: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, write_fragment, session_name, port, config_dir, reload_cmd)


async def async_remove_fragment(session_name: str, config_dir: str, reload_cmd: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, remove_fragment, session_name, config_dir, reload_cmd)


def _reload(cmd: str) -> None:
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        log.warning("nginx.reload_failed", metadata={"cmd": cmd, "stderr": result.stderr[:200]})
