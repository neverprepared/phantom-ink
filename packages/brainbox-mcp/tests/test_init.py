"""Tests for brainbox-init — guest-side bundle applier."""

from __future__ import annotations

import io
import json
import os
import stat
import tarfile
from pathlib import Path

import pytest
import zstandard

from brainbox_mcp._credentials import (
    Manifest,
    generate_identity,
    pack,
    seal,
)
from brainbox_mcp.init import main


def _read_perm(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture()
def fake_profile(tmp_path: Path) -> Path:
    ws = tmp_path / "src"
    (ws / ".ssh").mkdir(parents=True)
    (ws / ".aws").mkdir()
    priv = ws / ".ssh" / "id_ed25519"
    priv.write_text("PRIVATE")
    os.chmod(priv, 0o600)
    (ws / ".aws" / "credentials").write_text("[default]\nakid=AKIA\n")
    os.chmod(ws / ".aws" / "credentials", 0o600)
    return ws


def _build_sealed_bundle(profile_dir: Path, recipient: str, env: dict[str, str]) -> bytes:
    sources = [
        (profile_dir / ".ssh", ".ssh", None),
        (profile_dir / ".aws", ".aws", None),
    ]
    return seal(pack(sources, env, profile="work"), recipient)


def test_keygen_writes_identity_with_correct_perms(tmp_path: Path):
    ident_path = tmp_path / "run" / "identity.key"
    rc = main(["keygen", "--identity-out", str(ident_path)])
    assert rc == 0
    assert ident_path.exists()
    assert _read_perm(ident_path) == 0o600
    body = ident_path.read_text().strip()
    assert body.startswith("AGE-SECRET-KEY-")
    assert _read_perm(ident_path.parent) == 0o700


def test_keygen_writes_recipient_file(tmp_path: Path):
    ident_path = tmp_path / "id.key"
    rcpt_path = tmp_path / "rcpt.txt"
    rc = main(
        [
            "keygen",
            "--identity-out",
            str(ident_path),
            "--recipient-out",
            str(rcpt_path),
        ]
    )
    assert rc == 0
    assert rcpt_path.read_text().strip().startswith("age1")


def test_apply_unseals_bundle_and_lays_down_files(
    tmp_path: Path, fake_profile: Path, capsys: pytest.CaptureFixture
):
    pub, ident = generate_identity()
    ciphertext = _build_sealed_bundle(fake_profile, pub, {"FOO": "bar", "API_KEY": "secret"})

    ident_path = tmp_path / "id.key"
    ident_path.write_text(ident)
    bundle_path = tmp_path / "b.age"
    bundle_path.write_bytes(ciphertext)
    home = tmp_path / "home"

    rc = main(
        [
            "apply",
            "--identity",
            str(ident_path),
            "--bundle",
            str(bundle_path),
            "--home",
            str(home),
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["profile"] == "work"
    assert out["env_vars"] == 2

    assert (home / ".ssh" / "id_ed25519").read_text() == "PRIVATE"
    assert _read_perm(home / ".ssh" / "id_ed25519") == 0o600
    assert _read_perm(home / ".aws" / "credentials") == 0o600

    env_file = home / ".env"
    assert env_file.exists()
    assert _read_perm(env_file) == 0o600
    body = env_file.read_text()
    assert "FOO=bar" in body
    assert "API_KEY=secret" in body


def test_apply_with_wrong_identity_fails(
    tmp_path: Path, fake_profile: Path, capsys: pytest.CaptureFixture
):
    right_pub, _right = generate_identity()
    _wrong_pub, wrong_ident = generate_identity()
    ciphertext = _build_sealed_bundle(fake_profile, right_pub, {})

    ident_path = tmp_path / "wrong.key"
    ident_path.write_text(wrong_ident)
    bundle_path = tmp_path / "b.age"
    bundle_path.write_bytes(ciphertext)

    rc = main(
        [
            "apply",
            "--identity",
            str(ident_path),
            "--bundle",
            str(bundle_path),
            "--home",
            str(tmp_path / "home"),
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "ok" in err and "false" in err.lower()


def test_apply_skips_env_file_when_no_env(tmp_path: Path, fake_profile: Path):
    pub, ident = generate_identity()
    ciphertext = _build_sealed_bundle(fake_profile, pub, env={})
    ident_path = tmp_path / "id.key"
    ident_path.write_text(ident)
    bundle_path = tmp_path / "b.age"
    bundle_path.write_bytes(ciphertext)
    home = tmp_path / "home"

    rc = main(
        [
            "apply",
            "--identity",
            str(ident_path),
            "--bundle",
            str(bundle_path),
            "--home",
            str(home),
        ]
    )
    assert rc == 0
    assert not (home / ".env").exists()


def test_apply_rejects_tampered_bundle(tmp_path: Path, fake_profile: Path):
    """A bundle with a tar member missing from the manifest must be rejected."""
    pub, ident = generate_identity()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        m = Manifest(profile="x", created_at="2026-01-01T00:00:00+00:00")
        body = m.to_json().encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(body)
        info.mode = 0o600
        tf.addfile(info, io.BytesIO(body))
        smuggled = b"surprise"
        info2 = tarfile.TarInfo("files/evil")
        info2.size = len(smuggled)
        info2.mode = 0o644
        tf.addfile(info2, io.BytesIO(smuggled))
    plaintext = zstandard.ZstdCompressor().compress(buf.getvalue())
    ciphertext = seal(plaintext, pub)

    ident_path = tmp_path / "id.key"
    ident_path.write_text(ident)
    bundle_path = tmp_path / "b.age"
    bundle_path.write_bytes(ciphertext)

    rc = main(
        [
            "apply",
            "--identity",
            str(ident_path),
            "--bundle",
            str(bundle_path),
            "--home",
            str(tmp_path / "home"),
        ]
    )
    assert rc == 1
