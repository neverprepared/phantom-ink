"""Tests for the bundle/bind delivery split in lifecycle.py."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from brainbox.lifecycle import (
    _BUNDLE_DELIVERED_BINDS,
    _is_credential_bind,
    _resolve_credential_sources,
)
from brainbox.models import SessionContext, SessionState
from brainbox.models_api import CreateSessionRequest


def test_credential_binds_recognized():
    for bind in (
        "/home/developer/.aws",
        "/home/developer/.ssh",
        "/home/developer/.gnupg/pubring.kbx",
        "/home/developer/.kube",
        "/home/developer/.gitconfig",
    ):
        assert _is_credential_bind(bind), bind


def test_non_credential_binds_not_recognized():
    for bind in (
        "/home/developer/.gnupg/S.gpg-agent",  # live socket — not a credential file
        "/home/developer/workspace",
        "/home/developer/.claude",
        "/home/developer/.claude/projects",
        "/opt/homebrew/opt/reflex/share/reflex",
    ):
        assert not _is_credential_bind(bind), bind


def test_bundle_delivered_binds_subset_of_known_targets():
    expected_prefixes = {"/home/developer/.", "/home/developer/.aws/sso"}
    for bind in _BUNDLE_DELIVERED_BINDS:
        assert any(bind.startswith(p) for p in expected_prefixes), bind


def test_resolve_credential_sources_walks_mount_dict(tmp_path: Path):
    aws = tmp_path / ".aws"
    aws.mkdir()
    (aws / "credentials").write_text("[default]\nakid=A\n")
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519").write_text("KEY")
    os.chmod(ssh / "id_ed25519", 0o600)
    workspace = tmp_path / "workspace"
    workspace.mkdir()  # not a credential dir; should not appear

    fake_mounts = {
        str(aws): {"bind": "/home/developer/.aws", "mode": "ro"},
        str(ssh): {"bind": "/home/developer/.ssh", "mode": "ro"},
        str(workspace): {"bind": "/home/developer/workspace", "mode": "rw"},
    }

    with patch("brainbox.lifecycle._resolve_profile_mounts", return_value=fake_mounts):
        sources = _resolve_credential_sources("test", str(tmp_path))

    targets = {target for _host, target, _mode in sources}
    assert ".aws" in targets
    assert ".ssh" in targets
    assert "workspace" not in targets
    assert all(mode is None for _h, _t, mode in sources)


def test_resolve_credential_sources_empty_when_no_mounts():
    with patch("brainbox.lifecycle._resolve_profile_mounts", return_value={}):
        assert _resolve_credential_sources(None, None) == []


def test_session_context_default_delivery_is_bind():
    ctx = SessionContext(
        session_name="x",
        container_name="x",
        port=1234,
        created_at=0,
        ttl=60,
        state=SessionState.PROVISIONING,
    )
    assert ctx.delivery == "bind"


def test_session_context_accepts_bundle_delivery():
    ctx = SessionContext(
        session_name="x",
        container_name="x",
        port=1234,
        created_at=0,
        ttl=60,
        state=SessionState.PROVISIONING,
        delivery="bundle",
    )
    assert ctx.delivery == "bundle"


def test_create_session_request_delivery_optional():
    body = CreateSessionRequest()
    assert body.delivery is None
    body = CreateSessionRequest(delivery="bundle")
    assert body.delivery == "bundle"
