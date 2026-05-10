"""Tests for the build_sealed_bundle entry point."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from brainbox.credentials import (
    build_sealed_bundle,
    generate_identity,
    unpack,
    unseal,
)


@pytest.fixture()
def synthetic_profile(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / ".aws").mkdir(parents=True)
    (ws / ".aws" / "credentials").write_text("[default]\nakid=A\n")
    os.chmod(ws / ".aws" / "credentials", 0o600)
    return ws


def test_build_sealed_bundle_roundtrip(synthetic_profile: Path, tmp_path: Path):
    pub, ident = generate_identity()
    fake_mounts = {
        str(synthetic_profile / ".aws"): {"bind": "/home/developer/.aws", "mode": "ro"}
    }

    with patch("brainbox.lifecycle._resolve_profile_mounts", return_value=fake_mounts):
        with patch(
            "brainbox.lifecycle._resolve_profile_env",
            return_value="API_KEY=secret\nGITHUB_TOKEN=ghp_xxx\n",
        ):
            ciphertext = build_sealed_bundle(
                workspace_profile="test",
                workspace_home=str(synthetic_profile),
                recipient=pub,
            )

    assert isinstance(ciphertext, bytes) and len(ciphertext) > 0
    plaintext = unseal(ciphertext, ident)
    manifest = unpack(plaintext, tmp_path / "out")

    assert manifest.profile == "test"
    assert manifest.env == {"API_KEY": "secret", "GITHUB_TOKEN": "ghp_xxx"}
    assert (tmp_path / "out" / ".aws" / "credentials").read_text() == "[default]\nakid=A\n"


def test_build_sealed_bundle_raises_when_no_sources():
    pub, _ = generate_identity()
    with patch("brainbox.lifecycle._resolve_profile_mounts", return_value={}):
        with pytest.raises(ValueError, match="no credential sources"):
            build_sealed_bundle(
                workspace_profile="empty",
                workspace_home="/nonexistent",
                recipient=pub,
            )


def test_build_sealed_bundle_handles_no_env(synthetic_profile: Path, tmp_path: Path):
    pub, ident = generate_identity()
    fake_mounts = {
        str(synthetic_profile / ".aws"): {"bind": "/home/developer/.aws", "mode": "ro"}
    }
    with patch("brainbox.lifecycle._resolve_profile_mounts", return_value=fake_mounts):
        with patch("brainbox.lifecycle._resolve_profile_env", return_value=None):
            ciphertext = build_sealed_bundle("p", str(synthetic_profile), pub)

    manifest = unpack(unseal(ciphertext, ident), tmp_path / "out")
    assert manifest.env == {}


def test_build_sealed_bundle_strips_export_prefix(synthetic_profile: Path, tmp_path: Path):
    pub, ident = generate_identity()
    fake_mounts = {
        str(synthetic_profile / ".aws"): {"bind": "/home/developer/.aws", "mode": "ro"}
    }
    env_text = "export FOO=bar\nBAZ=qux\n# comment\n\n"
    with patch("brainbox.lifecycle._resolve_profile_mounts", return_value=fake_mounts):
        with patch("brainbox.lifecycle._resolve_profile_env", return_value=env_text):
            ciphertext = build_sealed_bundle("p", str(synthetic_profile), pub)
    manifest = unpack(unseal(ciphertext, ident), tmp_path / "out")
    assert manifest.env == {"FOO": "bar", "BAZ": "qux"}
