"""Round-trip tests for the credential bundle primitives."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from brainbox.credentials import (
    Manifest,
    generate_identity,
    pack,
    recipient_of,
    seal,
    unpack,
    unseal,
)


def _read_perm(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture()
def fake_profile(tmp_path: Path) -> Path:
    """Synthesize a profile-like tree with .ssh, .aws, .gitconfig."""
    ws = tmp_path / "profile"
    (ws / ".ssh").mkdir(parents=True)
    (ws / ".aws").mkdir()
    priv = ws / ".ssh" / "id_ed25519"
    pub = ws / ".ssh" / "id_ed25519.pub"
    priv.write_text("PRIVATE-KEY-MATERIAL")
    pub.write_text("ssh-ed25519 AAAA...")
    os.chmod(priv, 0o600)
    os.chmod(pub, 0o644)
    (ws / ".aws" / "credentials").write_text("[default]\nakid=AKIA\n")
    os.chmod(ws / ".aws" / "credentials", 0o600)
    (ws / ".gitconfig").write_text("[user]\n\tname = test\n")
    os.chmod(ws / ".gitconfig", 0o644)
    return ws


def test_keygen_roundtrip():
    pub, ident = generate_identity()
    assert pub.startswith("age1")
    assert ident.startswith("AGE-SECRET-KEY-")
    assert recipient_of(ident) == pub


def test_seal_unseal_roundtrip():
    pub, ident = generate_identity()
    payload = b"\x00\x01\x02secret bytes\xff" * 100
    ct = seal(payload, pub)
    assert ct != payload
    assert unseal(ct, ident) == payload


def test_seal_wrong_identity_fails():
    pub, _ident = generate_identity()
    _, wrong_ident = generate_identity()
    ct = seal(b"hello", pub)
    with pytest.raises(Exception):
        unseal(ct, wrong_ident)


def test_pack_unpack_preserves_files_and_modes(fake_profile: Path, tmp_path: Path):
    sources = [
        (fake_profile / ".ssh", ".ssh", None),
        (fake_profile / ".aws", ".aws", None),
        (fake_profile / ".gitconfig", ".gitconfig", None),
    ]
    env = {"AWS_PROFILE": "default", "GITHUB_TOKEN": "ghp_xxx"}
    raw = pack(sources, env, profile="work")
    assert isinstance(raw, bytes) and len(raw) > 0

    dest = tmp_path / "extracted"
    manifest = unpack(raw, dest)

    assert manifest.profile == "work"
    assert manifest.env == env
    assert manifest.version == 1
    targets = {f.target for f in manifest.files}
    assert ".ssh/id_ed25519" in targets
    assert ".ssh/id_ed25519.pub" in targets
    assert ".aws/credentials" in targets
    assert ".gitconfig" in targets

    assert (dest / ".ssh" / "id_ed25519").read_text() == "PRIVATE-KEY-MATERIAL"
    assert _read_perm(dest / ".ssh" / "id_ed25519") == 0o600
    assert _read_perm(dest / ".ssh" / "id_ed25519.pub") == 0o644
    assert _read_perm(dest / ".aws" / "credentials") == 0o600
    assert _read_perm(dest / ".gitconfig") == 0o644


def test_full_pipeline_pack_seal_unseal_unpack(fake_profile: Path, tmp_path: Path):
    pub, ident = generate_identity()
    sources = [(fake_profile / ".ssh", ".ssh", None)]
    plaintext = pack(sources, {"FOO": "bar"}, profile="work")
    sealed = seal(plaintext, pub)

    dest = tmp_path / "out"
    manifest = unpack(unseal(sealed, ident), dest)
    assert manifest.env == {"FOO": "bar"}
    assert (dest / ".ssh" / "id_ed25519").read_bytes() == b"PRIVATE-KEY-MATERIAL"
    assert _read_perm(dest / ".ssh" / "id_ed25519") == 0o600


def test_manifest_json_roundtrip():
    m = Manifest(
        profile="work",
        created_at="2026-01-01T00:00:00+00:00",
        env={"K": "V"},
    )
    j = m.to_json()
    decoded = json.loads(j)
    assert decoded["version"] == 1
    assert decoded["profile"] == "work"
    m2 = Manifest.from_json(j)
    assert m2.profile == m.profile
    assert m2.env == m.env
    assert m2.version == m.version


def test_pack_rejects_path_traversal(tmp_path: Path):
    src = tmp_path / "f"
    src.write_text("x")
    with pytest.raises(ValueError):
        pack([(src, "../etc/passwd", None)], {}, profile="x")


def test_pack_mode_override(fake_profile: Path, tmp_path: Path):
    src = fake_profile / ".gitconfig"
    raw = pack([(src, ".gitconfig", 0o400)], {}, profile="x")
    dest = tmp_path / "out"
    manifest = unpack(raw, dest)
    assert manifest.files[0].mode == 0o400
    assert _read_perm(dest / ".gitconfig") == 0o400


def test_unpack_rejects_extra_tar_members(fake_profile: Path, tmp_path: Path):
    """Defends against tampered bundles with files outside the manifest."""
    import io
    import tarfile

    import zstandard

    from brainbox.credentials.bundle import FILES_PREFIX, MANIFEST_NAME, Manifest

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        m = Manifest(profile="x", created_at="2026-01-01T00:00:00+00:00")
        info = tarfile.TarInfo(MANIFEST_NAME)
        body = m.to_json().encode()
        info.size = len(body)
        info.mode = 0o600
        tf.addfile(info, io.BytesIO(body))
        # Smuggled file not in manifest
        stowaway = b"surprise"
        info2 = tarfile.TarInfo(FILES_PREFIX + "evil")
        info2.size = len(stowaway)
        info2.mode = 0o644
        tf.addfile(info2, io.BytesIO(stowaway))
    raw = zstandard.ZstdCompressor().compress(buf.getvalue())
    with pytest.raises(ValueError, match="not in manifest"):
        unpack(raw, tmp_path / "out")
