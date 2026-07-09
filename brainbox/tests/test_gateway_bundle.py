"""Tests for per-profile credential bundles (gateway_bundle.py).

MinIO is faked by monkeypatching the artifacts-module CRUD onto an
in-memory dict — the unit under test is the encrypt/manifest/materialize
logic, not boto3.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import time

import pytest
from pydantic import SecretStr

import brainbox.artifacts as artifacts
import brainbox.gateway_bundle as gb
from brainbox.config import settings
from brainbox.gateway_secrets import LockedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_bundle(entries: list[dict], files: dict[str, bytes]) -> bytes:
    """Build a plaintext bundle tar.gz: manifest.json + files."""
    manifest = {
        "version": 1,
        "captured_at": "2026-07-08T00:00:00Z",
        "app_version": "test",
        "entries": entries,
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        mdata = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(mdata)
        tf.addfile(info, io.BytesIO(mdata))
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


AWS_ENTRY = {
    "source": "aws",
    "kind": "catalog",
    "audiences": ["both"],
    "files": ["config", "credentials"],
    "env": {
        "AWS_CONFIG_FILE": "aws/config",
        "AWS_SHARED_CREDENTIALS_FILE": "aws/credentials",
    },
}
SSH_ENTRY = {
    "source": "ssh",
    "kind": "catalog",
    "audiences": ["session"],
    "files": ["id_ed25519"],
    "env": {},
}
AWS_FILES = {"aws/config": b"[default]\nregion=us-east-1\n", "aws/credentials": b"[default]\n"}


class FakeStore:
    """In-memory stand-in for the artifacts module's CRUD."""

    def __init__(self):
        self.objects: dict[tuple[str, str], dict] = {}
        self.get_calls = 0

    def put_object(self, bucket, key, data, *, content_type=None, metadata=None):
        etag = hashlib.sha256(data).hexdigest()[:16]
        self.objects[(bucket, key)] = {
            "data": data,
            "etag": etag,
            "metadata": dict(metadata or {}),
        }
        return artifacts.ArtifactResult(
            bucket=bucket, key=key, size=len(data), etag=etag,
            timestamp_ms=int(time.time() * 1000),
        )

    def get_object(self, bucket, key):
        self.get_calls += 1
        obj = self.objects.get((bucket, key))
        if obj is None:
            raise artifacts.ArtifactError("get", key, "not found")
        return obj["data"]

    def head_object(self, bucket, key):
        obj = self.objects.get((bucket, key))
        if obj is None:
            raise artifacts.ArtifactError("head", key, "not found")
        return {
            "bucket": bucket, "key": key, "size": len(obj["data"]),
            "etag": obj["etag"], "content_type": "application/octet-stream",
            "last_modified_ms": 0, "metadata": obj["metadata"],
        }

    def delete_object(self, bucket, key):
        self.objects.pop((bucket, key), None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(settings.minio, "enabled", True)
    monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("pp"))
    monkeypatch.setattr(settings.gateway, "bundle_cache_dir", str(tmp_path / "cache"))
    for fn in ("put_object", "get_object", "head_object", "delete_object"):
        monkeypatch.setattr(artifacts, fn, getattr(fake, fn))
    return fake


# ---------------------------------------------------------------------------
# Round-trip + encryption
# ---------------------------------------------------------------------------


def test_store_materialize_roundtrip(store):
    tar = make_bundle([AWS_ENTRY], AWS_FILES)
    meta = gb.store_bundle("personal", tar, app_version="1.0")
    assert meta["sources"] == ["aws"]
    assert meta["captured_at"] == "2026-07-08T00:00:00Z"

    env = gb.materialize("personal", audience="gateway")
    assert set(env) == {"AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"}
    with open(env["AWS_CONFIG_FILE"], "rb") as f:
        assert f.read() == AWS_FILES["aws/config"]


def test_encrypted_at_rest(store):
    tar = make_bundle([AWS_ENTRY], AWS_FILES)
    gb.store_bundle("personal", tar)
    blob = next(iter(store.objects.values()))["data"]
    assert blob != tar
    with pytest.raises(tarfile.TarError):
        tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz").getmembers()
    import pyrage.passphrase

    decrypted = pyrage.passphrase.decrypt(blob, "pp")
    assert tarfile.open(fileobj=io.BytesIO(decrypted), mode="r:gz").getmember("manifest.json")


def test_wrong_key_raises_locked(store, monkeypatch):
    gb.store_bundle("personal", make_bundle([AWS_ENTRY], AWS_FILES))
    monkeypatch.setattr(settings.gateway, "secret_key", SecretStr("other"))
    with pytest.raises(LockedError):
        gb.materialize("personal")


# ---------------------------------------------------------------------------
# Audience + blocklist
# ---------------------------------------------------------------------------


def test_session_only_entries_never_touch_gateway_disk(store, tmp_path):
    tar = make_bundle(
        [AWS_ENTRY, SSH_ENTRY],
        {**AWS_FILES, "ssh/id_ed25519": b"PRIVATE KEY"},
    )
    gb.store_bundle("personal", tar)
    env = gb.materialize("personal", audience="gateway")
    assert "AWS_CONFIG_FILE" in env
    cache = tmp_path / "cache" / "personal"
    assert (cache / "aws" / "config").is_file()
    assert not (cache / "ssh").exists(), "session-only files must not be extracted"


def test_env_blocklist(store):
    entry = {
        "source": "custom",
        "kind": "custom",
        "audiences": ["both"],
        "files": ["conf"],
        "env": {
            "PATH": "custom/conf",
            "LD_PRELOAD": "custom/conf",
            "DYLD_INSERT_LIBRARIES": "custom/conf",
            "HOME": "custom/conf",
            "MY_TOOL_CONFIG": "custom/conf",
        },
    }
    gb.store_bundle("personal", make_bundle([entry], {"custom/conf": b"x"}))
    env = gb.materialize("personal", audience="gateway")
    assert set(env) == {"MY_TOOL_CONFIG"}


def test_env_mapping_to_missing_file_skipped(store):
    entry = {**AWS_ENTRY, "env": {"AWS_CONFIG_FILE": "aws/config", "GHOST": "aws/nope"}}
    gb.store_bundle("personal", make_bundle([entry], AWS_FILES))
    env = gb.materialize("personal")
    assert "GHOST" not in env and "AWS_CONFIG_FILE" in env


# ---------------------------------------------------------------------------
# Freshness + lifecycle
# ---------------------------------------------------------------------------


def test_unchanged_etag_downloads_once(store):
    gb.store_bundle("personal", make_bundle([AWS_ENTRY], AWS_FILES))
    gb.materialize("personal")
    gb.materialize("personal")
    assert store.get_calls == 1


def test_new_upload_invalidates_cache(store):
    gb.store_bundle("personal", make_bundle([AWS_ENTRY], AWS_FILES))
    env1 = gb.materialize("personal")
    files2 = {"aws/config": b"[default]\nregion=eu-west-1\n", "aws/credentials": b"[p]\n"}
    gb.store_bundle("personal", make_bundle([AWS_ENTRY], files2))
    gb.cleanup("personal")  # what the PUT endpoint does
    env2 = gb.materialize("personal")
    with open(env2["AWS_CONFIG_FILE"], "rb") as f:
        assert b"eu-west-1" in f.read()
    assert env1["AWS_CONFIG_FILE"] == env2["AWS_CONFIG_FILE"]  # stable path


def test_delete_removes_object_and_cache(store, tmp_path):
    gb.store_bundle("personal", make_bundle([AWS_ENTRY], AWS_FILES))
    gb.materialize("personal")
    assert gb.delete_bundle("personal") is True
    assert not (tmp_path / "cache" / "personal").exists()
    assert gb.get_bundle_meta("personal") is None
    assert gb.materialize("personal") == {}
    assert gb.delete_bundle("personal") is False


def test_materialize_without_bundle_is_empty(store):
    assert gb.materialize("personal") == {}


# ---------------------------------------------------------------------------
# Validation + safety
# ---------------------------------------------------------------------------


def test_minio_disabled(store, monkeypatch):
    monkeypatch.setattr(settings.minio, "enabled", False)
    with pytest.raises(gb.BundleDisabledError):
        gb.store_bundle("personal", make_bundle([AWS_ENTRY], AWS_FILES))
    assert gb.materialize("personal") == {}


def test_size_cap(store, monkeypatch):
    monkeypatch.setattr(settings.gateway, "bundle_max_bytes", 10)
    with pytest.raises(gb.GatewayBundleError, match="cap"):
        gb.store_bundle("personal", make_bundle([AWS_ENTRY], AWS_FILES))


def test_rejects_garbage_and_missing_manifest(store):
    with pytest.raises(gb.GatewayBundleError):
        gb.store_bundle("personal", b"not a tarball")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("random.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(gb.GatewayBundleError, match="manifest"):
        gb.store_bundle("personal", buf.getvalue())


def test_tar_escape_members_skipped(store, tmp_path):
    entry = {"source": "aws", "kind": "catalog", "audiences": ["both"],
             "files": [], "env": {"AWS_CONFIG_FILE": "aws/config"}}
    tar = make_bundle(
        [entry],
        {"aws/config": b"ok", "../evil": b"escape", "aws/../../evil2": b"escape"},
    )
    gb.store_bundle("personal", tar)
    env = gb.materialize("personal")
    assert "AWS_CONFIG_FILE" in env
    root = tmp_path / "cache"
    assert not (root.parent / "evil").exists()
    assert not (root / "evil").exists()
    assert not (root / "evil2").exists()


def test_materialized_permissions(store, tmp_path):
    gb.store_bundle("personal", make_bundle([AWS_ENTRY], AWS_FILES))
    env = gb.materialize("personal")
    import os
    import stat

    cache = tmp_path / "cache" / "personal"
    assert stat.S_IMODE(os.stat(cache).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(env["AWS_CONFIG_FILE"]).st_mode) == 0o600


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


class TestBundleEndpoints:
    async def _client(self):
        from httpx import ASGITransport, AsyncClient

        from brainbox.api import app

        return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    async def test_put_get_delete_flow(self, store, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        pool = MagicMock()
        pool.close = AsyncMock()
        monkeypatch.setattr("brainbox.api._gateway_pool", pool)

        # Pre-seed a stale materialized cache — PUT must clean it.
        stale = tmp_path / "cache" / "personal"
        stale.mkdir(parents=True)
        (stale / "junk").write_text("old")

        tar = make_bundle([AWS_ENTRY], AWS_FILES)
        async with await self._client() as c:
            r = await c.put(
                "/api/gateway/profiles/personal/bundle",
                content=tar,
                headers={"Content-Type": "application/gzip", "X-App-Version": "t1"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["saved"] is True and body["sources"] == ["aws"]
            pool.close.assert_awaited_with("personal")
            assert not stale.exists(), "PUT must drop the stale materialized cache"

            r = await c.get("/api/gateway/profiles/personal/bundle")
            assert r.status_code == 200
            meta = r.json()
            assert meta["captured_at"] == "2026-07-08T00:00:00Z"
            assert meta["app_version"] == "t1"

            r = await c.delete("/api/gateway/profiles/personal/bundle")
            assert r.status_code == 200 and r.json()["deleted"] is True

            r = await c.get("/api/gateway/profiles/personal/bundle")
            assert r.status_code == 404

    async def test_put_disabled_is_503(self, store, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        async with await self._client() as c:
            r = await c.put(
                "/api/gateway/profiles/personal/bundle",
                content=make_bundle([AWS_ENTRY], AWS_FILES),
            )
        assert r.status_code == 503

    async def test_put_locked_is_409(self, store, monkeypatch):
        monkeypatch.setattr(settings.gateway, "secret_key", SecretStr(""))
        async with await self._client() as c:
            r = await c.put(
                "/api/gateway/profiles/personal/bundle",
                content=make_bundle([AWS_ENTRY], AWS_FILES),
            )
        assert r.status_code == 409

    async def test_put_garbage_is_400(self, store):
        async with await self._client() as c:
            r = await c.put("/api/gateway/profiles/personal/bundle", content=b"nope")
        assert r.status_code == 400
