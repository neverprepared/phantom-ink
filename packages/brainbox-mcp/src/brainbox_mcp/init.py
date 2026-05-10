"""brainbox-init — guest-side credential bundle applier.

Two subcommands:
  keygen — generate ephemeral X25519 identity, write to disk, emit recipient pubkey
  apply  — unseal a bundle and lay down credentials in $HOME

Phase 2 scope: primitives only. Orchestration (who triggers keygen, who fetches
the bundle, who calls apply) lands in Phase 3 with the API + entrypoint wiring.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ._credentials import generate_identity, unpack, unseal

DEFAULT_IDENTITY_PATH = "/run/brainbox/identity.key"
DEFAULT_BUNDLE_PATH = "/run/brainbox/bundle.age"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brainbox-init")
    sub = parser.add_subparsers(dest="cmd")

    p_kg = sub.add_parser("keygen", help="generate an X25519 identity for this guest")
    p_kg.add_argument("--identity-out", default=DEFAULT_IDENTITY_PATH)
    p_kg.add_argument("--recipient-out", default=None)

    p_apply = sub.add_parser("apply", help="unseal a bundle and apply it to $HOME")
    p_apply.add_argument("--identity", default=DEFAULT_IDENTITY_PATH)
    p_apply.add_argument("--bundle", default=DEFAULT_BUNDLE_PATH)
    p_apply.add_argument("--home", default=None, help="target dir (default: $HOME)")
    p_apply.add_argument("--env-out", default=None, help="env file path (default: <home>/.env)")

    args = parser.parse_args(argv)

    if not args.cmd:
        parser.print_help()
        return 1

    try:
        if args.cmd == "keygen":
            return _keygen(args)
        if args.cmd == "apply":
            return _apply(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    return 1


def _keygen(args: argparse.Namespace) -> int:
    pub, ident = generate_identity()

    ident_path = Path(args.identity_out)
    ident_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    ident_path.write_text(ident + "\n")
    os.chmod(ident_path, 0o600)

    if args.recipient_out:
        rpath = Path(args.recipient_out)
        rpath.parent.mkdir(parents=True, exist_ok=True)
        rpath.write_text(pub + "\n")

    print(
        json.dumps(
            {
                "ok": True,
                "recipient": pub,
                "identity_path": str(ident_path),
                "recipient_path": args.recipient_out,
            }
        )
    )
    return 0


def _apply(args: argparse.Namespace) -> int:
    home = Path(args.home) if args.home else Path(os.environ.get("HOME") or str(Path.home()))
    home.mkdir(parents=True, exist_ok=True)

    identity = Path(args.identity).read_text().strip()
    ciphertext = Path(args.bundle).read_bytes()
    manifest = unpack(unseal(ciphertext, identity), home)

    env_out = Path(args.env_out) if args.env_out else home / ".env"
    if manifest.env:
        env_out.parent.mkdir(parents=True, exist_ok=True)
        env_out.write_text("\n".join(f"{k}={v}" for k, v in manifest.env.items()) + "\n")
        os.chmod(env_out, 0o600)

    print(
        json.dumps(
            {
                "ok": True,
                "profile": manifest.profile,
                "files": len(manifest.files),
                "env_vars": len(manifest.env),
                "home": str(home),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
