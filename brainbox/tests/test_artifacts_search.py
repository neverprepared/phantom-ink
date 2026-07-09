"""Tests for artifacts.search_objects + its API endpoint (Files search)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import brainbox.artifacts as artifacts
from brainbox.config import settings


class FakePaginator:
    def __init__(self, keys: list[str], page_size: int = 3):
        self.keys = keys
        self.page_size = page_size

    def paginate(self, *, Bucket, Prefix=""):
        matching = [k for k in self.keys if k.startswith(Prefix)]
        ts = datetime(2026, 7, 8, tzinfo=timezone.utc)
        for i in range(0, len(matching), self.page_size):
            yield {
                "Contents": [
                    {"Key": k, "Size": 10, "LastModified": ts, "ETag": '"e"'}
                    for k in matching[i : i + self.page_size]
                ]
            }


class FakeClient:
    """Per-bucket in-memory key store."""

    def __init__(self, buckets: dict[str, list[str]]):
        self._buckets = buckets

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        store = self._buckets

        class _P:
            def paginate(self, *, Bucket, Prefix=""):
                return FakePaginator(store.get(Bucket, [])).paginate(
                    Bucket=Bucket, Prefix=Prefix
                )

        return _P()

    def list_buckets(self):
        return {"Buckets": [{"Name": b} for b in self._buckets]}

    def list_objects_v2(self, *, Bucket, Prefix="", MaxKeys=1000, Delimiter=None):
        matching = [k for k in self._buckets.get(Bucket, []) if k.startswith(Prefix)]
        return {"KeyCount": len(matching[:MaxKeys])}


@pytest.fixture
def fake_minio(monkeypatch):
    buckets = {
        "phantom-artifacts": [
            "personal/loop-templates/Readme.md",
            "personal/recordings/session-alpha.cast",
            "personal/notes/readme-draft.MD",
            "personal/notes/",  # directory marker — must be skipped
            "work/loop-templates/readme.md",  # other profile's slice
        ],
        "gsa-archives": ["2026/dump.tar"],
        "pb-default": ["vaults/memory/x.md"],
    }
    monkeypatch.setattr(settings.minio, "enabled", True)
    monkeypatch.setattr(artifacts, "_client", lambda: FakeClient(buckets))
    return buckets


def test_search_matches_case_insensitive(fake_minio):
    res = artifacts.search_objects("artifacts", "README")
    names = sorted(f.name for f in res["files"])
    # Unscoped search spans the whole bucket — both profiles' readmes.
    assert names == ["Readme.md", "readme-draft.MD", "readme.md"]
    assert res["truncated"] is False


def test_search_scoped_by_prefix(fake_minio):
    res = artifacts.search_objects("artifacts", "readme", prefix="personal/")
    assert all(f.key.startswith("personal/") for f in res["files"])
    assert not any("work/" in f.key for f in res["files"])
    assert len(res["files"]) == 2


def test_known_buckets_unscoped_lists_all(fake_minio):
    buckets = artifacts.known_buckets("")
    assert [b["name"] for b in buckets] == ["gsa-archives", "pb-default", "phantom-artifacts"]
    assert all(b["scope_prefix"] == "" for b in buckets)


def test_known_buckets_profile_scoping(fake_minio):
    buckets = artifacts.known_buckets("personal")
    by_name = {b["name"]: b for b in buckets}
    # profile-structured buckets get a scope_prefix…
    assert by_name["phantom-artifacts"]["scope_prefix"] == "personal/"
    # …and <profile>-* named buckets belong wholly to the profile.
    assert "gsa-archives" not in by_name  # other profile's bucket omitted
    gsa = {b["name"]: b for b in artifacts.known_buckets("gsa")}
    assert gsa["gsa-archives"]["scope_prefix"] == ""


def test_search_limit_truncates(fake_minio):
    res = artifacts.search_objects("artifacts", "e", limit=1)
    assert len(res["files"]) == 1
    assert res["truncated"] is True


def test_search_skips_directory_markers(fake_minio):
    res = artifacts.search_objects("artifacts", "notes")
    assert all(not f.key.endswith("/") for f in res["files"])


class TestSearchEndpoint:
    async def _get(self, path):
        from httpx import ASGITransport, AsyncClient

        from brainbox.api import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            return await c.get(path)

    async def test_search_endpoint(self, fake_minio):
        r = await self._get("/api/artifacts/artifacts/search?q=readme&prefix=personal/")
        assert r.status_code == 200
        body = r.json()
        assert {f["name"] for f in body["files"]} == {"Readme.md", "readme-draft.MD"}

    async def test_buckets_endpoint_profile_param(self, fake_minio):
        r = await self._get("/api/artifacts/buckets?profile=personal")
        assert r.status_code == 200
        names = {b["name"]: b["scope_prefix"] for b in r.json()["buckets"]}
        assert names.get("phantom-artifacts") == "personal/"
        assert "gsa-archives" not in names

    async def test_empty_q_is_400(self, fake_minio):
        r = await self._get("/api/artifacts/artifacts/search?q=%20")
        assert r.status_code == 400

    async def test_disabled_is_503(self, fake_minio, monkeypatch):
        monkeypatch.setattr(settings.minio, "enabled", False)
        r = await self._get("/api/artifacts/artifacts/search?q=x")
        assert r.status_code == 503


class TestPresignPublicEndpoint:
    """SigV4 signs the Host — presigned URLs must be minted against the
    address the app will fetch from, not the daemon-local endpoint."""

    @pytest.fixture(autouse=True)
    def _minio(self, monkeypatch):
        from pydantic import SecretStr

        monkeypatch.setattr(settings.minio, "enabled", True)
        monkeypatch.setattr(settings.minio, "endpoint", "http://localhost:9000")
        monkeypatch.setattr(settings.minio, "access_key", SecretStr("ak"))
        monkeypatch.setattr(settings.minio, "secret_key", SecretStr("sk"))
        artifacts.reset_client_for_tests()
        yield
        artifacts.reset_client_for_tests()

    def test_presign_uses_public_endpoint(self, monkeypatch):
        monkeypatch.setattr(settings.minio, "public_endpoint", "https://minio.example.com")
        url = artifacts.presigned_get_url("artifacts", "a/b.txt")
        assert url.startswith("https://minio.example.com/"), url
        assert "localhost" not in url

    def test_presign_falls_back_to_endpoint(self, monkeypatch):
        monkeypatch.setattr(settings.minio, "public_endpoint", "")
        url = artifacts.presigned_get_url("artifacts", "a/b.txt")
        assert url.startswith("http://localhost:9000/"), url

    def test_put_presign_uses_public_endpoint(self, monkeypatch):
        monkeypatch.setattr(settings.minio, "public_endpoint", "https://minio.example.com")
        url = artifacts.presigned_put_url("artifacts", "a/b.txt")
        assert url.startswith("https://minio.example.com/"), url

    def test_presign_explicit_host_wins(self, monkeypatch):
        monkeypatch.setattr(settings.minio, "public_endpoint", "https://minio.example.com")
        url = artifacts.presigned_get_url(
            "artifacts", "a/b.txt", public_base="http://192.168.87.200:9000"
        )
        assert url.startswith("http://192.168.87.200:9000/"), url

    def test_presign_rejects_non_http_host(self):
        with pytest.raises(artifacts.ArtifactError, match="http"):
            artifacts.presigned_get_url("artifacts", "a/b.txt", public_base="ftp://nope")

    async def test_presign_endpoint_host_param(self):
        from httpx import ASGITransport, AsyncClient

        from brainbox.api import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(
                "/api/artifacts/artifacts/presign",
                params={"key": "x.txt", "op": "get", "host": "https://minio.example.com"},
            )
        assert r.status_code == 200, r.text
        assert r.json()["url"].startswith("https://minio.example.com/")
