"""Pure bundle-building entry point — used by both the inline path
(docker backend in-process) and the command-center daemon.

Phase 4 extracts this from the docker backend so the daemon can call the
exact same function with the exact same inputs. No I/O beyond reading the
profile filesystem and writing the sealed bytes to a return value.
"""

from __future__ import annotations

from .bundle import pack
from .seal import seal


def build_sealed_bundle(
    workspace_profile: str | None,
    workspace_home: str | None,
    recipient: str,
) -> bytes:
    """Resolve a profile's credential sources + env, pack, and seal to recipient.

    Returns the age-sealed ciphertext bytes ready to land at /run/brainbox/bundle.age
    in a guest. Raises ValueError if no sources are resolvable for the profile.
    """
    # Imported lazily to avoid pulling lifecycle's heavy module graph into
    # the daemon's import chain.
    from ..lifecycle import _resolve_credential_sources, _resolve_profile_env

    sources = _resolve_credential_sources(workspace_profile, workspace_home)
    if not sources:
        raise ValueError(
            f"no credential sources resolvable for profile={workspace_profile!r}, "
            f"workspace_home={workspace_home!r}"
        )

    env_text = _resolve_profile_env(
        workspace_profile=workspace_profile, workspace_home=workspace_home
    )
    env: dict[str, str] = {}
    if env_text:
        for raw in env_text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            k, _, v = line.partition("=")
            env[k.strip()] = v

    plaintext = pack(sources, env, profile=workspace_profile or "default")
    return seal(plaintext, recipient)
