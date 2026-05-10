"""Tests for the command-center daemon HTTP layer."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from brainbox.credentials import generate_identity


@pytest.fixture()
def app_with_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BRAINBOX_CC_API_KEY", "test-key-abc123")
    from brainbox.credentials.daemon import create_app

    return create_app()


@pytest.fixture()
def client(app_with_key) -> TestClient:
    return TestClient(app_with_key)


def test_healthz(client: TestClient):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_seal_requires_api_key(client: TestClient):
    pub, _ = generate_identity()
    r = client.post(
        "/seal", json={"workspace_profile": "p", "workspace_home": "/x", "recipient": pub}
    )
    assert r.status_code == 401


def test_seal_rejects_wrong_api_key(client: TestClient):
    pub, _ = generate_identity()
    r = client.post(
        "/seal",
        headers={"X-API-Key": "wrong"},
        json={"workspace_profile": "p", "workspace_home": "/x", "recipient": pub},
    )
    assert r.status_code == 401


def test_seal_rejects_non_age_recipient(client: TestClient):
    r = client.post(
        "/seal",
        headers={"X-API-Key": "test-key-abc123"},
        json={"workspace_profile": "p", "workspace_home": "/x", "recipient": "not-an-age-key"},
    )
    assert r.status_code == 400


def test_seal_returns_ciphertext(client: TestClient, tmp_path):
    pub, ident = generate_identity()
    aws = tmp_path / ".aws"
    aws.mkdir()
    (aws / "credentials").write_text("[default]\nakid=AKIA\n")
    os.chmod(aws / "credentials", 0o600)
    fake_mounts = {str(aws): {"bind": "/home/developer/.aws", "mode": "ro"}}

    with patch("brainbox.lifecycle._resolve_profile_mounts", return_value=fake_mounts):
        with patch("brainbox.lifecycle._resolve_profile_env", return_value="FOO=bar\n"):
            r = client.post(
                "/seal",
                headers={"X-API-Key": "test-key-abc123"},
                json={
                    "workspace_profile": "test",
                    "workspace_home": str(tmp_path),
                    "recipient": pub,
                },
            )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert int(r.headers["x-bundle-bytes"]) == len(r.content)

    from brainbox.credentials import unpack, unseal

    manifest = unpack(unseal(r.content, ident), tmp_path / "extracted")
    assert manifest.profile == "test"
    assert manifest.env == {"FOO": "bar"}


def test_seal_returns_404_when_no_sources(client: TestClient):
    pub, _ = generate_identity()
    with patch("brainbox.lifecycle._resolve_profile_mounts", return_value={}):
        r = client.post(
            "/seal",
            headers={"X-API-Key": "test-key-abc123"},
            json={
                "workspace_profile": "empty",
                "workspace_home": "/nonexistent",
                "recipient": pub,
            },
        )
    assert r.status_code == 404


def test_serve_fails_fast_without_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BRAINBOX_CC_API_KEY", raising=False)
    monkeypatch.delenv("CL_API_KEY", raising=False)
    from brainbox.credentials.daemon import serve

    with pytest.raises(RuntimeError, match="API_KEY"):
        serve(host="127.0.0.1", port=0)
