"""S3-compatible artifact store (MinIO) — durable, addressable, profile-scoped.

Two buckets:
  - ``settings.minio.bucket_vault``     — Obsidian markdown vault,
                                          mounted via s3fs-mac elsewhere.
                                          API reads here are mostly for
                                          the desktop file browser.
  - ``settings.minio.bucket_artifacts`` — Everything else: loop
                                          templates, instances, iteration
                                          envelopes, assist outputs,
                                          session recordings.

Profile boundary: all read/write/list calls are scoped to the
``settings.minio.profile_prefix`` namespace. Cross-profile access is
denied by MinIO IAM at the bucket policy level (bootstrap-buckets.sh
configures this); this module trusts the policy but still defends in
depth by prefixing every key it constructs.

Folder semantics: S3 has no folders. The file browser fakes them by
using ``/`` as a delimiter in ``list_v2`` calls. ``list_folder`` returns
two arrays — ``files`` (objects in this level) and ``folders``
(``CommonPrefixes``, the directory entries).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import settings
from .log import get_logger

log = get_logger()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ArtifactError(RuntimeError):
    def __init__(self, operation: str, key: str, reason: str):
        self.operation = operation
        self.key = key
        self.reason = reason
        super().__init__(f"artifact {operation} failed for '{key}': {reason}")


@dataclass(frozen=True)
class ArtifactResult:
    """Returned from put_object."""

    bucket: str
    key: str
    size: int
    etag: str
    timestamp_ms: int


@dataclass(frozen=True)
class ArtifactEntry:
    """One file in a list_folder response."""

    bucket: str
    key: str            # full key including profile prefix
    name: str           # last segment ("foo.md")
    size: int
    last_modified_ms: int
    etag: str


@dataclass(frozen=True)
class FolderEntry:
    """One directory in a list_folder response — a CommonPrefix."""

    bucket: str
    prefix: str         # full prefix including profile prefix
    name: str           # last segment ("loop-templates")


@dataclass(frozen=True)
class FolderListing:
    """Result of list_folder — files at the current level + child folders."""

    bucket: str
    prefix: str
    files: list[ArtifactEntry]
    folders: list[FolderEntry]
    truncated: bool


# ---------------------------------------------------------------------------
# Client cache
# ---------------------------------------------------------------------------


_s3_client_cached = None
_s3_presign_client_cached = None


def _make_client(endpoint_url: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.minio.access_key.get_secret_value(),
        aws_secret_access_key=settings.minio.secret_key.get_secret_value(),
        region_name=settings.minio.region,
        # boto's presigner falls back to legacy SigV2 URLs without this;
        # MinIO tolerates v2 on GET but rejects it on PUT
        # (SignatureDoesNotMatch). Force SigV4 everywhere.
        config=Config(signature_version="s3v4"),
    )


def _client():
    """Cached boto3 S3 client. None when MinIO is disabled."""
    global _s3_client_cached
    if not settings.minio.enabled:
        raise ArtifactError(
            "client", "", "minio integration is disabled (CL_MINIO__ENABLED=false)"
        )
    if _s3_client_cached is None:
        _s3_client_cached = _make_client(settings.minio.endpoint)
    return _s3_client_cached


_presign_clients_by_host: dict[str, Any] = {}


def _presign_client(public_base: str | None = None):
    """Client used ONLY to sign presigned URLs. SigV4 covers the Host
    header, so URLs must be signed against the address the *browser/app*
    will fetch them from, not the daemon-local one.

    Resolution: an explicit ``public_base`` (the app passes its MinIO
    integration address — local or remote depending on its toggle) wins;
    then ``settings.minio.public_endpoint``; then the daemon endpoint
    (single-machine setups). Clients are cached per host — signing is
    offline, so these are cheap, but boto client construction isn't.
    """
    if not settings.minio.enabled:
        raise ArtifactError(
            "client", "", "minio integration is disabled (CL_MINIO__ENABLED=false)"
        )
    base = (public_base or "").strip() or settings.minio.public_endpoint.strip()
    if not base or base == settings.minio.endpoint:
        return _client()
    if not base.startswith(("http://", "https://")):
        raise ArtifactError("presign", base, "presign host must be an http(s) URL")
    client = _presign_clients_by_host.get(base)
    if client is None:
        client = _make_client(base)
        _presign_clients_by_host[base] = client
    return client


def reset_client_for_tests() -> None:
    """Drop the cached clients; used by tests that override settings."""
    global _s3_client_cached
    _s3_client_cached = None
    _presign_clients_by_host.clear()


# ---------------------------------------------------------------------------
# Bucket discovery + readiness
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """The frontend uses this to decide whether to surface the Files panel."""
    return bool(settings.minio.enabled)


def health() -> dict[str, Any]:
    """Cheap reachability probe (one bounded list on the artifacts bucket).

    The panel calls this on mount to render a "MinIO unreachable" empty
    state instead of letting the operator click into folders that 502.
    """
    if not is_enabled():
        return {"ok": False, "reason": "disabled"}
    try:
        _client().list_objects_v2(
            Bucket=settings.minio.bucket_artifacts,
            MaxKeys=1,
        )
        return {
            "ok": True,
            "endpoint": settings.minio.endpoint,
            "public_endpoint": settings.minio.public_endpoint or settings.minio.endpoint,
            "buckets": {
                "vault": settings.minio.bucket_vault,
                "artifacts": settings.minio.bucket_artifacts,
            },
            "profile_prefix": settings.minio.profile_prefix,
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def known_buckets(profile: str = "") -> list[dict[str, str]]:
    """Live bucket catalog for the file browser (requires a credential
    that can ``list_buckets`` — the brainbox service account).

    ``profile`` scoping ("follow the app's active profile"):
      - ``""``      — every bucket, unscoped.
      - otherwise   — a bucket named ``<profile>-*`` belongs wholly to the
        profile (scope_prefix ""); a bucket with a top-level ``<profile>/``
        folder is profile-structured (scope_prefix ``<profile>/``); buckets
        matching neither are omitted.

    ``scope_prefix`` is the root the browser should treat as "/" for
    this bucket under the requested profile.
    """
    client = _client()
    try:
        names = [b["Name"] for b in client.list_buckets().get("Buckets", [])]
    except ClientError as exc:
        raise ArtifactError("list_buckets", "", str(exc)) from exc

    out: list[dict[str, str]] = []
    for name in sorted(names):
        if not profile:
            out.append({"key": name, "name": name, "label": name, "scope_prefix": ""})
            continue
        if name.startswith(profile + "-"):
            out.append({"key": name, "name": name, "label": name, "scope_prefix": ""})
            continue
        try:
            resp = client.list_objects_v2(
                Bucket=name, Prefix=profile + "/", MaxKeys=1
            )
        except ClientError:
            continue
        if resp.get("KeyCount", 0) > 0:
            out.append(
                {"key": name, "name": name, "label": name, "scope_prefix": profile + "/"}
            )
    return out


# ---------------------------------------------------------------------------
# Key construction — every write goes through here
# ---------------------------------------------------------------------------


def _norm_key(key: str) -> str:
    """Keys are RAW bucket keys (exactly as listings return them) — the
    old implicit profile-prefixing double-prefixed everything the browser
    passed back. Namespacing is now the writer's job (e.g. gateway_bundle
    keys under ``<profile>/gateway-bundles/``) and the browser's scoping
    is applied by the API layer via ``known_buckets``' scope_prefix."""
    return key.lstrip("/")


def _resolve_bucket(name: str) -> str:
    """Accept either the frontend's logical name ("vault" / "artifacts")
    or the real bucket name. Returns the real bucket name."""
    if name == "vault":
        return settings.minio.bucket_vault
    if name == "artifacts":
        return settings.minio.bucket_artifacts
    if not name or "/" in name:
        raise ArtifactError("resolve_bucket", name, "invalid bucket name")
    return name  # dynamic catalog: any real bucket the credential can reach


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def put_object(
    bucket: str,
    key: str,
    data: bytes,
    *,
    content_type: str | None = None,
    metadata: dict[str, str] | None = None,
) -> ArtifactResult:
    """Upload bytes to ``bucket/key`` (raw key). Metadata goes onto
    the object as user-defined ``x-amz-meta-*`` headers."""
    real_bucket = _resolve_bucket(bucket)
    real_key = _norm_key(key)
    try:
        kwargs: dict[str, Any] = {
            "Bucket": real_bucket,
            "Key": real_key,
            "Body": data,
        }
        if content_type:
            kwargs["ContentType"] = content_type
        if metadata:
            kwargs["Metadata"] = {k: str(v) for k, v in metadata.items()}
        resp = _client().put_object(**kwargs)
        return ArtifactResult(
            bucket=real_bucket,
            key=real_key,
            size=len(data),
            etag=resp.get("ETag", "").strip('"'),
            timestamp_ms=int(time.time() * 1000),
        )
    except ClientError as exc:
        raise ArtifactError("put", real_key, str(exc)) from exc


def get_object(bucket: str, key: str) -> bytes:
    """Read the full object body. Caller absorbs the cost — the API
    handler decides whether to stream or buffer."""
    real_bucket = _resolve_bucket(bucket)
    real_key = _norm_key(key)
    try:
        resp = _client().get_object(Bucket=real_bucket, Key=real_key)
        return resp["Body"].read()
    except ClientError as exc:
        if exc.response["Error"].get("Code") in ("NoSuchKey", "404"):
            raise ArtifactError("get", real_key, "not found") from exc
        raise ArtifactError("get", real_key, str(exc)) from exc


def head_object(bucket: str, key: str) -> dict[str, Any]:
    """Object metadata without downloading the body. Used by the file
    browser to render sizes / timestamps when the operator clicks an
    entry."""
    real_bucket = _resolve_bucket(bucket)
    real_key = _norm_key(key)
    try:
        resp = _client().head_object(Bucket=real_bucket, Key=real_key)
        return {
            "bucket": real_bucket,
            "key": real_key,
            "size": resp.get("ContentLength", 0),
            "etag": resp.get("ETag", "").strip('"'),
            "content_type": resp.get("ContentType", ""),
            "last_modified_ms": int(resp["LastModified"].timestamp() * 1000),
            "metadata": resp.get("Metadata", {}),
        }
    except ClientError as exc:
        if exc.response["Error"].get("Code") in ("NoSuchKey", "404", "NotFound"):
            raise ArtifactError("head", real_key, "not found") from exc
        raise ArtifactError("head", real_key, str(exc)) from exc


def delete_object(bucket: str, key: str) -> None:
    real_bucket = _resolve_bucket(bucket)
    real_key = _norm_key(key)
    try:
        _client().delete_object(Bucket=real_bucket, Key=real_key)
    except ClientError as exc:
        raise ArtifactError("delete", real_key, str(exc)) from exc


# Reserved trash namespace at the bucket root. Browser deletes move
# objects here (server-side copy, no data transfer); deleting a key
# already under it is permanent. An ILM rule on the bucket expires
# trash automatically (see docs in the ops runbook / bootstrap).
TRASH_PREFIX = ".trash/"


def is_trash_key(key: str) -> bool:
    return _norm_key(key).startswith(TRASH_PREFIX)


def trash_object(bucket: str, key: str) -> str:
    """Soft-delete: move ``key`` to ``.trash/<epoch-ms>/<key>`` in the
    same bucket and remove the original. Returns the trash key. The
    timestamp segment keeps repeated deletes of the same key distinct
    and gives the ILM expiry rule a stable prefix to age out.
    """
    real_bucket = _resolve_bucket(bucket)
    real_key = _norm_key(key)
    if real_key.startswith(TRASH_PREFIX):
        raise ArtifactError("trash", real_key, "already in trash — delete is permanent there")
    trash_key = f"{TRASH_PREFIX}{int(time.time() * 1000)}/{real_key}"
    try:
        _client().copy_object(
            Bucket=real_bucket,
            Key=trash_key,
            CopySource={"Bucket": real_bucket, "Key": real_key},
            MetadataDirective="COPY",
        )
        _client().delete_object(Bucket=real_bucket, Key=real_key)
    except ClientError as exc:
        raise ArtifactError("trash", real_key, str(exc)) from exc
    return trash_key


# ---------------------------------------------------------------------------
# Folder browsing — the file browser's main affordance
# ---------------------------------------------------------------------------


def list_folder(bucket: str, prefix: str = "", max_keys: int = 500) -> FolderListing:
    """List objects + child folders at ``prefix`` (one level). S3 fakes
    folders via ``Delimiter='/'`` + ``CommonPrefixes``; we expose that
    to the frontend as two arrays.

    ``prefix`` is a RAW bucket prefix (the browser starts from the
    bucket's scope_prefix); returned ``key`` values are raw and can be
    passed back unchanged to head / presign / delete.
    """
    real_bucket = _resolve_bucket(bucket)
    real_prefix = _norm_key(prefix)
    if real_prefix and not real_prefix.endswith("/"):
        real_prefix = real_prefix + "/"
    try:
        resp = _client().list_objects_v2(
            Bucket=real_bucket,
            Prefix=real_prefix,
            Delimiter="/",
            MaxKeys=max_keys,
        )
    except ClientError as exc:
        raise ArtifactError("list", real_prefix, str(exc)) from exc

    files = [
        ArtifactEntry(
            bucket=real_bucket,
            key=obj["Key"],
            name=obj["Key"].rsplit("/", 1)[-1],
            size=obj["Size"],
            last_modified_ms=int(obj["LastModified"].timestamp() * 1000),
            etag=obj.get("ETag", "").strip('"'),
        )
        for obj in resp.get("Contents", [])
        # Hide the directory marker that some clients write as a 0-byte
        # object ending in "/".
        if not obj["Key"].endswith("/")
    ]

    folders = [
        FolderEntry(
            bucket=real_bucket,
            prefix=cp["Prefix"],
            name=cp["Prefix"].rstrip("/").rsplit("/", 1)[-1],
        )
        for cp in resp.get("CommonPrefixes", [])
    ]

    return FolderListing(
        bucket=real_bucket,
        prefix=real_prefix,
        files=files,
        folders=folders,
        truncated=bool(resp.get("IsTruncated")),
    )


def search_objects(bucket: str, query: str, *, limit: int = 200, prefix: str = "") -> dict:
    """Case-insensitive substring search over object keys under ``prefix``
    (the browser passes the bucket's scope_prefix — "" = whole bucket).
    Paginates ``list_objects_v2`` (bounded — MAX_SCAN keys) and matches
    against the prefix-relative portion of each key. Returns the same
    file shape as ``list_folder``.
    """
    MAX_SCAN = 10_000
    real_bucket = _resolve_bucket(bucket)
    scoped = _norm_key(prefix)
    if scoped and not scoped.endswith("/"):
        scoped += "/"
    needle = query.lower()

    files: list[ArtifactEntry] = []
    scanned = 0
    truncated = False
    try:
        paginator = _client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=real_bucket, Prefix=scoped):
            for obj in page.get("Contents", []):
                scanned += 1
                key = obj["Key"]
                rel = key[len(scoped):] if scoped and key.startswith(scoped) else key
                if key.endswith("/"):
                    continue  # directory markers
                if key.startswith(TRASH_PREFIX):
                    continue  # trash is browsable, not searchable noise
                if needle in rel.lower():
                    files.append(ArtifactEntry(
                        bucket=real_bucket,
                        key=key,
                        name=key.rsplit("/", 1)[-1],
                        size=obj["Size"],
                        last_modified_ms=int(obj["LastModified"].timestamp() * 1000),
                        etag=obj.get("ETag", "").strip('"'),
                    ))
                    if len(files) >= limit:
                        truncated = True
                        break
                if scanned >= MAX_SCAN:
                    truncated = True
                    break
            if truncated:
                break
    except ClientError as exc:
        raise ArtifactError("search", query, str(exc)) from exc

    return {
        "bucket": real_bucket,
        "query": query,
        "files": files,
        "truncated": truncated,
        "scanned": scanned,
    }


# ---------------------------------------------------------------------------
# Presigned URLs — for the assist worker writes, Phase 4
# ---------------------------------------------------------------------------


def presigned_put_url(
    bucket: str, key: str, *, expires_seconds: int = 3600, public_base: str | None = None
) -> str:
    """Generate a presigned PUT URL the worker container can write to
    directly. The Phase 4 assist race fix uses this — agent writes to
    the URL, brainbox reads from the bucket after the container is gone.
    """
    real_bucket = _resolve_bucket(bucket)
    real_key = _norm_key(key)
    try:
        return _presign_client(public_base).generate_presigned_url(
            "put_object",
            Params={"Bucket": real_bucket, "Key": real_key},
            ExpiresIn=expires_seconds,
        )
    except ClientError as exc:
        raise ArtifactError("presign_put", real_key, str(exc)) from exc


def presigned_get_url(
    bucket: str, key: str, *, expires_seconds: int = 3600, public_base: str | None = None
) -> str:
    real_bucket = _resolve_bucket(bucket)
    real_key = _norm_key(key)
    try:
        return _presign_client(public_base).generate_presigned_url(
            "get_object",
            Params={"Bucket": real_bucket, "Key": real_key},
            ExpiresIn=expires_seconds,
        )
    except ClientError as exc:
        raise ArtifactError("presign_get", real_key, str(exc)) from exc
