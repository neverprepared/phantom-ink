"""MCP gateway — per-profile encrypted env store (ADR-002, phase 1).

Stores each profile's environment variables (the credentials MCP servers
read at startup) **encrypted at rest** with a single operator-held key
(``CL_GATEWAY__SECRET_KEY``). The key lives only in the gateway process
env/memory; only ciphertext is written to disk. Decryption happens
in-memory when the gateway spawns a profile's MCP server subprocess
(phase 2) or when the operator edits a profile's env via the app (phase 3).

Crypto: **age** via ``pyrage`` (passphrase mode) — the operator key is a
passphrase. We do NOT roll our own crypto. (age keypair / SOPS are drop-in
alternatives behind this same interface if that interop is later wanted.)

Trust model: the read-plaintext operations here are **operator-only** — the
API routes that expose them require the API key (full trust). Agents never
read raw creds; they get scoped tokens + tool results (phase 2/3). One key
unlocks all profiles (by design); the store is useless at rest without it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyrage
import pyrage.passphrase

from .config import settings

_SUFFIX = ".env.enc"


class GatewaySecretsError(Exception):
    """Base error for the gateway secret store."""


class LockedError(GatewaySecretsError):
    """No operator key set, or the key cannot decrypt — store is locked."""


def _secrets_dir() -> Path:
    d = settings.gateway.secrets_dir or str(settings.config_dir / "gateway" / "secrets")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(0o700)
    except OSError:
        pass
    return p


def _passphrase() -> str:
    pw = settings.gateway.secret_key.get_secret_value().strip()
    if not pw:
        raise LockedError(
            "CL_GATEWAY__SECRET_KEY is not set — the gateway secret store is locked"
        )
    return pw


def _validate_profile(profile: str) -> str:
    if not profile or "/" in profile or "\\" in profile or profile in (".", ".."):
        raise GatewaySecretsError(f"invalid profile name: {profile!r}")
    return profile


def _path(profile: str) -> Path:
    return _secrets_dir() / f"{_validate_profile(profile)}{_SUFFIX}"


def is_unlocked() -> bool:
    """True if an operator key is configured."""
    try:
        _passphrase()
        return True
    except LockedError:
        return False


def list_profiles() -> list[str]:
    """Profile names that have a stored env blob (no decryption needed)."""
    return sorted(p.name[: -len(_SUFFIX)] for p in _secrets_dir().glob(f"*{_SUFFIX}"))


def set_profile_env(profile: str, env: dict[str, str]) -> None:
    """Encrypt and persist a profile's env (full replace). Atomic write, 0600."""
    pw = _passphrase()
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise GatewaySecretsError("env must be a dict of str -> str")
    blob = pyrage.passphrase.encrypt(json.dumps(env).encode(), pw)
    dest = _path(profile)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(blob)
    tmp.chmod(0o600)
    tmp.replace(dest)  # atomic within the same dir


def get_profile_env(profile: str) -> dict[str, str]:
    """Decrypt and return a profile's env. Operator-only (exposes plaintext)."""
    pw = _passphrase()
    path = _path(profile)
    if not path.exists():
        raise GatewaySecretsError(f"no env stored for profile {profile!r}")
    try:
        data = pyrage.passphrase.decrypt(path.read_bytes(), pw)
    except pyrage.DecryptError as exc:
        raise LockedError(
            "operator key cannot decrypt this profile's env (wrong key?)"
        ) from exc
    return json.loads(data)


def delete_profile_env(profile: str) -> bool:
    """Remove a profile's stored env. Returns True if a blob existed."""
    path = _path(profile)
    if path.exists():
        path.unlink()
        return True
    return False
