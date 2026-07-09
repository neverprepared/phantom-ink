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


def _client():
    """Cached boto3 S3 client. None when MinIO is disabled."""
    global _s3_client_cached
    if not settings.minio.enabled:
        raise ArtifactError(
            "client", "", "minio integration is disabled (CL_MINIO__ENABLED=false)"
        )
    if _s3_client_cached is None:
        _s3_client_cached = boto3.client(
            "s3",
            endpoint_url=settings.minio.endpoint,
            aws_access_key_id=settings.minio.access_key.get_secret_value(),
            aws_secret_access_key=settings.minio.secret_key.get_secret_value(),
            region_name=settings.minio.region,
        )
    return _s3_client_cached


def reset_client_for_tests() -> None:
    """Drop the cached client; used by tests that override settings."""
    global _s3_client_cached
    _s3_client_cached = None


# ---------------------------------------------------------------------------
# Bucket discovery + readiness
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """The frontend uses this to decide whether to surface the Files panel."""
    return bool(settings.minio.enabled)


def health() -> dict[str, Any]:
    """Cheap profile-scoped reachability probe. Uses list_objects_v2
    against the artifacts bucket bounded by the profile prefix —
    matches the exact permission grant in the per-profile IAM policy
    (admin-level ``list_buckets`` would 403 here).

    The panel calls this on mount to render a "MinIO unreachable" empty
    state instead of letting the operator click into folders that 502.
    """
    if not is_enabled():
        return {"ok": False, "reason": "disabled"}
    try:
        client = _client()
        prefix = settings.minio.profile_prefix.strip("/")
        scoped = (prefix + "/") if prefix else ""
        client.list_objects_v2(
            Bucket=settings.minio.bucket_artifacts,
            Prefix=scoped,
            MaxKeys=1,
        )
        return {
            "ok": True,
            "endpoint": settings.minio.endpoint,
            "buckets": {
                "vault": settings.minio.bucket_vault,
                "artifacts": settings.minio.bucket_artifacts,
            },
            "profile_prefix": settings.minio.profile_prefix,
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def known_buckets() -> list[dict[str, str]]:
    """Static bucket catalog the frontend renders as the two root entries
    in the file browser."""
    return [
        {"key": "vault", "name": settings.minio.bucket_vault, "label": "Vault"},
        {"key": "artifacts", "name": settings.minio.bucket_artifacts, "label": "Artifacts"},
    ]


# ---------------------------------------------------------------------------
# Key construction — every write goes through here
# ---------------------------------------------------------------------------


def _full_key(relative: str) -> str:
    """Prepend the profile prefix. Strip leading slash so callers can
    pass either ``"loop-templates/foo"`` or ``"/loop-templates/foo"``."""
    rel = relative.lstrip("/")
    prefix = settings.minio.profile_prefix.strip("/")
    if not prefix:
        return rel
    return f"{prefix}/{rel}" if rel else prefix


def _resolve_bucket(name: str) -> str:
    """Accept either the frontend's logical name ("vault" / "artifacts")
    or the real bucket name. Returns the real bucket name."""
    if name == "vault":
        return settings.minio.bucket_vault
    if name == "artifacts":
        return settings.minio.bucket_artifacts
    if name in (settings.minio.bucket_vault, settings.minio.bucket_artifacts):
        return name
    raise ArtifactError("resolve_bucket", name, "unknown bucket")


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
    """Upload bytes to ``bucket/profile_prefix/key``. Metadata goes onto
    the object as user-defined ``x-amz-meta-*`` headers."""
    real_bucket = _resolve_bucket(bucket)
    real_key = _full_key(key)
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
    real_key = _full_key(key)
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
    real_key = _full_key(key)
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
    real_key = _full_key(key)
    try:
        _client().delete_object(Bucket=real_bucket, Key=real_key)
    except ClientError as exc:
        raise ArtifactError("delete", real_key, str(exc)) from exc


# ---------------------------------------------------------------------------
# Folder browsing — the file browser's main affordance
# ---------------------------------------------------------------------------


def list_folder(bucket: str, prefix: str = "", max_keys: int = 500) -> FolderListing:
    """List objects + child folders at ``prefix`` (one level). S3 fakes
    folders via ``Delimiter='/'`` + ``CommonPrefixes``; we expose that
    to the frontend as two arrays.

    The returned ``key`` values include the profile prefix — the
    frontend can pass them back unchanged to get_object / delete.
    """
    real_bucket = _resolve_bucket(bucket)
    # Browser-facing prefix: append the profile prefix to whatever the
    # operator typed. An empty prefix yields the top of the profile's
    # own namespace.
    real_prefix = _full_key(prefix.lstrip("/"))
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


def search_objects(bucket: str, query: str, *, limit: int = 200) -> dict:
    """Case-insensitive substring search over object keys in the profile's
    namespace. Paginates ``list_objects_v2`` under the profile prefix
    (bounded — MAX_SCAN keys) and matches against the profile-relative
    portion of each key. Returns the same file shape as ``list_folder``.
    """
    MAX_SCAN = 10_000
    real_bucket = _resolve_bucket(bucket)
    scoped = _full_key("")
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


def presigned_put_url(bucket: str, key: str, *, expires_seconds: int = 3600) -> str:
    """Generate a presigned PUT URL the worker container can write to
    directly. The Phase 4 assist race fix uses this — agent writes to
    the URL, brainbox reads from the bucket after the container is gone.
    """
    real_bucket = _resolve_bucket(bucket)
    real_key = _full_key(key)
    try:
        return _client().generate_presigned_url(
            "put_object",
            Params={"Bucket": real_bucket, "Key": real_key},
            ExpiresIn=expires_seconds,
        )
    except ClientError as exc:
        raise ArtifactError("presign_put", real_key, str(exc)) from exc


def presigned_get_url(bucket: str, key: str, *, expires_seconds: int = 3600) -> str:
    real_bucket = _resolve_bucket(bucket)
    real_key = _full_key(key)
    try:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": real_bucket, "Key": real_key},
            ExpiresIn=expires_seconds,
        )
    except ClientError as exc:
        raise ArtifactError("presign_get", real_key, str(exc)) from exc
