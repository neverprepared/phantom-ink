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
    def __init__(self, keys):
        self._keys = keys

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self._keys)


@pytest.fixture
def fake_minio(monkeypatch):
    keys = [
        "personal/loop-templates/Readme.md",
        "personal/recordings/session-alpha.cast",
        "personal/notes/readme-draft.MD",
        "personal/notes/",  # directory marker — must be skipped
        "work/loop-templates/readme.md",  # other profile — outside prefix
    ]
    monkeypatch.setattr(settings.minio, "enabled", True)
    monkeypatch.setattr(settings.minio, "profile_prefix", "personal")
    monkeypatch.setattr(artifacts, "_client", lambda: FakeClient(keys))
    return keys


def test_search_matches_case_insensitive(fake_minio):
    res = artifacts.search_objects("artifacts", "README")
    names = sorted(f.name for f in res["files"])
    assert names == ["Readme.md", "readme-draft.MD"]
    assert res["truncated"] is False


def test_search_is_profile_scoped(fake_minio):
    res = artifacts.search_objects("artifacts", "readme")
    assert all(f.key.startswith("personal/") for f in res["files"])
    assert not any("work/" in f.key for f in res["files"])


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
        r = await self._get("/api/artifacts/artifacts/search?q=readme")
        assert r.status_code == 200
        body = r.json()
        assert {f["name"] for f in body["files"]} == {"Readme.md", "readme-draft.MD"}

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
