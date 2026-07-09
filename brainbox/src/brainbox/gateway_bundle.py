"""MCP gateway — per-profile credential bundles (files) via MinIO.

The Wails app captures selected credential FILES from the operator's
workstation (aws config/SSO cache, kube config, gitconfig, …) into a
tar.gz alongside a ``manifest.json``, and PUTs it here. This module
encrypts the tar with the same operator key as the env store
(``CL_GATEWAY__SECRET_KEY``, age passphrase mode) and parks it in MinIO
at ``gateway-bundles/{profile}/bundle.age`` (inside the daemon's
artifacts namespace). At MCP-server spawn the gateway materializes the
bundle into a per-profile cache dir on the API host and injects the
manifest's env mappings (``AWS_CONFIG_FILE`` → ``<cache>/aws/config``).

Why: gateway-spawned servers run on the API host, but credential files
live on the operator's machine — path-valued env vars copied from a
workstation profile are garbage here. The bundle moves the *files* to
the host that needs them, captured at a moment (image rebuild / Sync
now) when the app provably runs where they exist.

Design contract:
  - **Manifest-driven.** This module has no source→env knowledge; catalog
    and operator-defined custom sources are indistinguishable. Whatever
    ``entries[].env`` says is applied — after ``_ENV_BLOCKED`` filtering,
    so a bundle can never hijack the spawned process (PATH, LD_*, …).
  - **Audience-filtered.** Entries are tagged gateway|session|both; only
    matching entries are extracted/mapped, so session-only material
    (e.g. SSH keys) never touches the API host's disk.
  - **Precedence.** Callers merge the returned env UNDER the profile env
    store — an operator-set key always wins over the bundle.
  - Bundle failures must never fail a spawn; ``materialize`` raises only
    for programming errors, and gateway_pool wraps it defensively anyway.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import time
from pathlib import Path

import pyrage
import pyrage.passphrase

from .config import settings
from .gateway_secrets import GatewaySecretsError, LockedError, _passphrase, _validate_profile
from .log import get_logger

log = get_logger()

_MANIFEST_NAME = "manifest.json"
_BUNDLE_OBJECT = "bundle.age"
_ETAG_MARKER = ".etag"

# Env vars a manifest mapping may never set — process-hijack surface.
_ENV_BLOCKED = {"PATH", "HOME", "PYTHONPATH", "PYTHONSTARTUP", "BASH_ENV", "ENV", "IFS"}
_ENV_BLOCKED_PREFIXES = ("LD_", "DYLD_")


class GatewayBundleError(GatewaySecretsError):
    """Invalid bundle payload / manifest."""


class BundleDisabledError(GatewayBundleError):
    """MinIO integration is off — bundle storage is unavailable."""


def _require_minio() -> None:
    if not settings.minio.enabled:
        raise BundleDisabledError(
            "credential bundles require MinIO (CL_MINIO__ENABLED=false)"
        )


def _bundle_key(profile: str) -> str:
    return f"gateway-bundles/{_validate_profile(profile)}/{_BUNDLE_OBJECT}"


def _cache_root() -> Path:
    d = settings.gateway.bundle_cache_dir or str(
        settings.config_dir / "gateway" / "bundles"
    )
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(0o700)
    except OSError:
        pass
    return p


def _cache_dir(profile: str) -> Path:
    return _cache_root() / _validate_profile(profile)


def _env_blocked(var: str) -> bool:
    return var in _ENV_BLOCKED or var.startswith(_ENV_BLOCKED_PREFIXES)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _read_manifest(tar_gz: bytes) -> dict:
    """Extract + validate manifest.json from a plaintext bundle tar."""
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
            member = tf.getmember(_MANIFEST_NAME)
            fh = tf.extractfile(member)
            if fh is None:
                raise GatewayBundleError("manifest.json is not a regular file")
            manifest = json.loads(fh.read())
    except GatewayBundleError:
        raise
    except KeyError:
        raise GatewayBundleError("bundle has no manifest.json") from None
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise GatewayBundleError(f"unreadable bundle: {exc}") from exc

    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise GatewayBundleError("unsupported bundle manifest (want version 1)")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise GatewayBundleError("manifest entries must be a list")
    for e in entries:
        if not isinstance(e, dict) or not e.get("source"):
            raise GatewayBundleError("manifest entry missing source name")
        if "/" in e["source"] or e["source"] in (".", ".."):
            raise GatewayBundleError(f"invalid source name {e['source']!r}")
    return manifest


def _entry_matches(entry: dict, audience: str) -> bool:
    audiences = entry.get("audiences") or []
    return audience in audiences or "both" in audiences


def _safe_member(member: tarfile.TarInfo) -> bool:
    """Only plain files at sane relative paths get extracted."""
    name = member.name
    if member.islnk() or member.issym() or not (member.isfile() or member.isdir()):
        return False
    if name.startswith(("/", "\\")) or ".." in Path(name).parts:
        return False
    return True


# ---------------------------------------------------------------------------
# Store / meta / delete
# ---------------------------------------------------------------------------


def store_bundle(profile: str, tar_gz: bytes, *, app_version: str = "") -> dict:
    """Encrypt + upload a profile's bundle. Returns summary meta."""
    from . import artifacts

    _validate_profile(profile)
    _require_minio()
    pw = _passphrase()  # raises LockedError when the operator key is unset
    max_bytes = settings.gateway.bundle_max_bytes
    if len(tar_gz) > max_bytes:
        raise GatewayBundleError(
            f"bundle is {len(tar_gz)} bytes; cap is {max_bytes} (CL_GATEWAY__BUNDLE_MAX_BYTES)"
        )
    manifest = _read_manifest(tar_gz)
    captured_at = str(manifest.get("captured_at") or "")
    sources = [e["source"] for e in manifest["entries"]]

    blob = pyrage.passphrase.encrypt(tar_gz, pw)
    res = artifacts.put_object(
        "artifacts",
        _bundle_key(profile),
        blob,
        content_type="application/octet-stream",
        metadata={
            "captured_at": captured_at,
            "app_version": app_version,
            "manifest_sha256": hashlib.sha256(tar_gz).hexdigest(),
        },
    )
    log.info(
        "gateway_bundle.stored",
        metadata={
            "profile": profile,
            "sources": sources,
            "size": len(blob),
            "etag": res.etag,
        },
    )
    return {
        "etag": res.etag,
        "captured_at": captured_at,
        "sources": sources,
        "size": len(blob),
    }


def get_bundle_meta(profile: str) -> dict | None:
    """Object metadata for a profile's bundle, or None when absent."""
    from . import artifacts

    _require_minio()
    try:
        head = artifacts.head_object("artifacts", _bundle_key(profile))
    except artifacts.ArtifactError as exc:
        if "not found" in str(exc):
            return None
        raise
    meta = head.get("metadata", {})
    return {
        "profile": profile,
        "etag": head.get("etag", ""),
        "size": head.get("size", 0),
        "last_modified_ms": head.get("last_modified_ms", 0),
        "captured_at": meta.get("captured_at", ""),
        "app_version": meta.get("app_version", ""),
        "sources": None,  # listing sources requires decrypt; meta stays cheap
    }


def delete_bundle(profile: str) -> bool:
    """Remove a profile's bundle object + materialized cache."""
    from . import artifacts

    _require_minio()
    existed = get_bundle_meta(profile) is not None
    if existed:
        artifacts.delete_object("artifacts", _bundle_key(profile))
    cleanup(profile)
    return existed


def cleanup(profile: str) -> None:
    """Drop the materialized cache dir (bundle changed or was removed)."""
    shutil.rmtree(_cache_dir(profile), ignore_errors=True)


# ---------------------------------------------------------------------------
# Materialize — the spawn-time consumer
# ---------------------------------------------------------------------------


def materialize(profile: str, *, audience: str = "gateway") -> dict[str, str]:
    """Ensure the profile's bundle is extracted locally; return its env map.

    Returns ``{}`` when MinIO is disabled or no bundle exists. The cache is
    keyed by the object's etag — repeat calls while the bundle is unchanged
    cost one HEAD. Only entries matching ``audience`` are extracted and
    mapped, and mappings pass the blocklist filter.
    """
    from . import artifacts

    if not settings.minio.enabled:
        return {}
    meta = get_bundle_meta(profile)
    cache = _cache_dir(profile)
    if meta is None:
        cleanup(profile)  # bundle gone → stale cache must not linger
        return {}

    etag_file = cache / _ETAG_MARKER
    manifest_file = cache / _MANIFEST_NAME
    fresh = (
        etag_file.is_file()
        and manifest_file.is_file()
        and etag_file.read_text().strip() == meta["etag"]
    )
    if not fresh:
        blob = artifacts.get_object("artifacts", _bundle_key(profile))
        try:
            tar_gz = pyrage.passphrase.decrypt(blob, _passphrase())
        except pyrage.DecryptError as exc:
            raise LockedError(
                "operator key cannot decrypt this profile's bundle (wrong key?)"
            ) from exc
        manifest = _read_manifest(tar_gz)
        _extract(cache, tar_gz, manifest, audience)
        etag_file.write_text(meta["etag"])
        etag_file.chmod(0o600)
        log.info(
            "gateway_bundle.materialized",
            metadata={"profile": profile, "audience": audience, "etag": meta["etag"]},
        )
    else:
        manifest = json.loads(manifest_file.read_text())

    return _manifest_env(cache, manifest, audience)


def _extract(cache: Path, tar_gz: bytes, manifest: dict, audience: str) -> None:
    """(Re)extract audience-matching entries into a wiped cache dir."""
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True, mode=0o700)
    allowed_roots = {
        e["source"] for e in manifest["entries"] if _entry_matches(e, audience)
    }
    with tarfile.open(fileobj=io.BytesIO(tar_gz), mode="r:gz") as tf:
        for member in tf.getmembers():
            if member.name == _MANIFEST_NAME:
                continue
            if not _safe_member(member):
                log.warning(
                    "gateway_bundle.member_skipped", metadata={"name": member.name}
                )
                continue
            root = Path(member.name).parts[0]
            if root not in allowed_roots:
                continue  # wrong audience for this consumer
            dest = cache / member.name
            if member.isdir():
                dest.mkdir(parents=True, mode=0o700, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            fh = tf.extractfile(member)
            if fh is None:
                continue
            dest.write_bytes(fh.read())
            dest.chmod(0o600)
    # Manifest lands last so a torn extract never looks fresh.
    manifest_path = cache / _MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o600)


def _manifest_env(cache: Path, manifest: dict, audience: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for entry in manifest["entries"]:
        if not _entry_matches(entry, audience):
            continue
        for var, rel in (entry.get("env") or {}).items():
            if _env_blocked(var):
                log.warning(
                    "gateway_bundle.env_blocked",
                    metadata={"var": var, "source": entry["source"]},
                )
                continue
            target = cache / rel
            if not target.is_file():
                log.warning(
                    "gateway_bundle.env_target_missing",
                    metadata={"var": var, "path": str(target)},
                )
                continue
            env[var] = str(target)
    return env


def now_iso() -> str:
    """UTC timestamp helper for callers that build manifests server-side."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
