"""CLI entrypoint: python -m brainbox <command> [options]."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .log import setup_logging


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(prog="brainbox")
    sub = parser.add_subparsers(dest="command")

    # Common opts
    def add_session_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--session", default="default")
        p.add_argument("--role", choices=["developer", "researcher", "performer"], default=None)
        p.add_argument("--port", type=int, default=None)
        p.add_argument("--hardened", action="store_true", default=False)
        p.add_argument("--ttl", type=int, default=None)
        p.add_argument("--volume", action="append", default=[])
        p.add_argument("--llm-provider", choices=["claude", "ollama", "codex"], default="claude")
        p.add_argument("--llm-model", default=None)
        p.add_argument("--ollama-host", default=None)
        p.add_argument("--codex-api-key", default=None)

    # provision
    p_prov = sub.add_parser("provision")
    add_session_opts(p_prov)

    # run (full pipeline)
    p_run = sub.add_parser("run")
    add_session_opts(p_run)

    # recycle
    p_recycle = sub.add_parser("recycle")
    p_recycle.add_argument("--session", default="default")
    p_recycle.add_argument("--reason", default="cli")

    # api
    p_api = sub.add_parser("api")
    p_api.add_argument("--host", default="127.0.0.1")
    p_api.add_argument("--port", type=int, default=9999)
    p_api.add_argument("--reload", action="store_true", default=False)
    p_api.add_argument("--daemon", action="store_true", default=False)

    # stop
    p_stop = sub.add_parser("stop")
    p_stop.add_argument("--timeout", type=int, default=10)

    # status
    p_status = sub.add_parser("status")
    p_status.add_argument("--json", action="store_true", default=False)

    # restart
    p_restart = sub.add_parser("restart")
    p_restart.add_argument("--host", default="127.0.0.1")
    p_restart.add_argument("--port", type=int, default=9999)
    p_restart.add_argument("--reload", action="store_true", default=False)

    # mcp
    p_mcp = sub.add_parser("mcp")
    p_mcp.add_argument(
        "--url", default=None, help="API URL (default: $BRAINBOX_URL or http://127.0.0.1:9999)"
    )

    # cc — command center primitives (credential bundle building/unsealing)
    p_cc = sub.add_parser("cc", help="credential bundle utilities")
    cc_sub = p_cc.add_subparsers(dest="cc_command")

    p_cc_keygen = cc_sub.add_parser("keygen", help="generate an X25519 identity keypair")
    p_cc_keygen.add_argument("--out", default=None, help="write identity to file (chmod 0600)")

    p_cc_bundle = cc_sub.add_parser("bundle", help="build and seal a credential bundle")
    p_cc_bundle.add_argument("--profile", required=True, help="workspace profile name")
    p_cc_bundle.add_argument(
        "--workspace-home", default=None, help="profile home dir (default: $WORKSPACES_HOME/$profile)"
    )
    p_cc_bundle.add_argument("--recipient", required=True, help="age recipient pubkey (age1...)")
    p_cc_bundle.add_argument("-o", "--output", required=True, help="output path for sealed bundle")

    p_cc_unseal = cc_sub.add_parser("unseal", help="unseal a bundle into a directory")
    p_cc_unseal.add_argument("-i", "--identity", required=True, help="path to identity secret file")
    p_cc_unseal.add_argument("bundle", help="sealed bundle path")
    p_cc_unseal.add_argument("dest", help="destination directory")

    p_cc_serve = cc_sub.add_parser("serve", help="run the command-center sealing daemon")
    p_cc_serve.add_argument("--host", default="127.0.0.1")
    p_cc_serve.add_argument("--port", type=int, default=9888)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "provision":
            asyncio.run(_provision(args))
        elif args.command == "run":
            asyncio.run(_run_pipeline(args))
        elif args.command == "recycle":
            asyncio.run(_recycle(args))
        elif args.command == "api":
            _start_api(args)
        elif args.command == "mcp":
            _start_mcp(args)
        elif args.command == "stop":
            _stop_daemon(args)
        elif args.command == "status":
            _status_daemon(args)
        elif args.command == "restart":
            _restart_daemon(args)
        elif args.command == "cc":
            _cc_dispatch(args, p_cc)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        sys.exit(1)


async def _provision(args: argparse.Namespace) -> None:
    from .lifecycle import provision

    ctx = await provision(
        session_name=args.session,
        role=args.role,
        port=args.port,
        hardened=args.hardened,
        ttl=args.ttl,
        volume_mounts=args.volume,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        ollama_host=args.ollama_host,
        codex_api_key=args.codex_api_key,
    )
    print(json.dumps({"ok": True, "session": ctx.session_name, "port": ctx.port}))


async def _run_pipeline(args: argparse.Namespace) -> None:
    from .lifecycle import run_pipeline

    ctx = await run_pipeline(
        session_name=args.session,
        role=args.role,
        port=args.port,
        hardened=args.hardened,
        ttl=args.ttl,
        volume_mounts=args.volume,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        ollama_host=args.ollama_host,
        codex_api_key=args.codex_api_key,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "session": ctx.session_name,
                "port": ctx.port,
                "url": f"http://localhost:{ctx.port}",
            }
        )
    )


async def _recycle(args: argparse.Namespace) -> None:
    from .lifecycle import recycle

    await recycle(args.session, reason=args.reason)


def _start_mcp(args: argparse.Namespace) -> None:
    import os

    if args.url:
        os.environ["BRAINBOX_URL"] = args.url

    from .mcp_server import run

    run()


def _start_api(args: argparse.Namespace) -> None:
    if args.daemon:
        from .daemon import DaemonManager

        manager = DaemonManager()
        pid, message = manager.start(host=args.host, port=args.port, reload=args.reload)
        print(message)
    else:
        import uvicorn

        uvicorn.run(
            "brainbox.api:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )


def _stop_daemon(args: argparse.Namespace) -> None:
    from .daemon import DaemonManager

    manager = DaemonManager()
    message = manager.stop(timeout=args.timeout)
    print(message)


def _status_daemon(args: argparse.Namespace) -> None:
    from .daemon import DaemonManager

    manager = DaemonManager()
    status = manager.status()

    if args.json:
        print(json.dumps(manager.to_dict(status), indent=2))
    else:
        if status.running:
            uptime_str = (
                _format_uptime(status.uptime_seconds) if status.uptime_seconds else "unknown"
            )
            print("✓ Daemon running")
            print(f"  PID: {status.pid}")
            print(f"  URL: http://{status.host}:{status.port}")
            print(f"  Uptime: {uptime_str}")
            if status.log_file:
                print(f"  Logs: {status.log_file}")
        else:
            print("✗ Daemon not running")
            if status.log_file:
                print(f"  Logs: {status.log_file}")


def _restart_daemon(args: argparse.Namespace) -> None:
    from .daemon import DaemonManager

    manager = DaemonManager()
    pid, message = manager.restart(host=args.host, port=args.port, reload=args.reload)
    print(message)


def _cc_dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.cc_command == "keygen":
        _cc_keygen(args)
    elif args.cc_command == "bundle":
        _cc_bundle(args)
    elif args.cc_command == "unseal":
        _cc_unseal(args)
    elif args.cc_command == "serve":
        _cc_serve(args)
    else:
        parser.print_help()
        sys.exit(1)


def _cc_keygen(args: argparse.Namespace) -> None:
    import os

    from .credentials import generate_identity

    pub, ident = generate_identity()
    if args.out:
        out = args.out
        with open(out, "w", encoding="utf-8") as f:
            f.write(ident + "\n")
        os.chmod(out, 0o600)
        print(json.dumps({"ok": True, "recipient": pub, "identity_path": out}))
    else:
        print(json.dumps({"ok": True, "recipient": pub, "identity": ident}))


def _cc_bundle(args: argparse.Namespace) -> None:
    import os
    from pathlib import Path

    from .credentials import build_sealed_bundle

    workspace_home = args.workspace_home
    if not workspace_home:
        ws_root = os.environ.get("WORKSPACES_HOME") or os.environ.get("WORKSPACE_HOME")
        if ws_root:
            workspace_home = str(Path(ws_root) / args.profile)

    ciphertext = build_sealed_bundle(
        workspace_profile=args.profile,
        workspace_home=workspace_home,
        recipient=args.recipient,
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(ciphertext)
    os.chmod(out, 0o600)

    print(
        json.dumps(
            {
                "ok": True,
                "output": str(out),
                "bytes_sealed": len(ciphertext),
            }
        )
    )


def _cc_serve(args: argparse.Namespace) -> None:
    from .credentials.daemon import serve

    serve(host=args.host, port=args.port)


def _cc_unseal(args: argparse.Namespace) -> None:
    from pathlib import Path

    from .credentials import unpack, unseal

    identity = Path(args.identity).read_text().strip()
    ciphertext = Path(args.bundle).read_bytes()
    plaintext = unseal(ciphertext, identity)
    manifest = unpack(plaintext, Path(args.dest))

    print(
        json.dumps(
            {
                "ok": True,
                "profile": manifest.profile,
                "files": len(manifest.files),
                "env_vars": len(manifest.env),
                "dest": args.dest,
            }
        )
    )


def _format_uptime(seconds: int) -> str:
    """Format uptime seconds into human-readable string.

    Args:
        seconds: Uptime in seconds

    Returns:
        Formatted string like "2h 15m" or "45s"
    """
    if seconds < 60:
        return f"{seconds}s"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"

    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes > 0:
        return f"{hours}h {remaining_minutes}m"
    return f"{hours}h"


if __name__ == "__main__":
    main()
