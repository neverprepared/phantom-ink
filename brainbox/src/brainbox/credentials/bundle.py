"""Credential bundle format — manifest + tar+zstd packing/unpacking.

A bundle is the unsealed plaintext payload that gets handed to age for sealing.
Layout inside the tar:

    manifest.json     # version, profile, created_at, files[], env{}
    files/<target>    # actual credential files, target paths relative to $HOME

The manifest is authoritative: guests apply files to the paths and modes the
manifest specifies. File content lives in the tar; metadata lives in the manifest.
"""

from __future__ import annotations

import io
import json
import os
import stat
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import zstandard

from .seal import BUNDLE_FORMAT_VERSION

MANIFEST_NAME = "manifest.json"
FILES_PREFIX = "files/"

DEFAULT_DIR_MODE = 0o700
ZSTD_LEVEL = 9


@dataclass(frozen=True)
class FileEntry:
    arcname: str
    target: str
    mode: int
    size: int


@dataclass
class Manifest:
    profile: str
    created_at: str
    files: list[FileEntry] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    version: int = BUNDLE_FORMAT_VERSION

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "version": self.version,
            "profile": self.profile,
            "created_at": self.created_at,
            "files": [
                {"arcname": f.arcname, "target": f.target, "mode": f.mode, "size": f.size}
                for f in self.files
            ],
            "env": self.env,
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, data: str) -> "Manifest":
        d = json.loads(data)
        return cls(
            version=d["version"],
            profile=d["profile"],
            created_at=d["created_at"],
            files=[FileEntry(**f) for f in d["files"]],
            env=d.get("env", {}),
        )


def _norm_target(target: str) -> str:
    t = target.strip().lstrip("/")
    if not t or ".." in Path(t).parts:
        raise ValueError(f"invalid target path: {target!r}")
    return t


def _stat_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def pack(
    sources: list[tuple[Path, str, int | None]],
    env: dict[str, str],
    profile: str,
) -> bytes:
    """Pack credential sources into a tar+zstd bundle.

    Each source is (host_path, target_relative_to_home, mode_override).
    Directories are walked recursively; mode_override only applies to single-file
    sources (directory contents preserve filesystem modes).
    """
    entries: list[FileEntry] = []
    buf = io.BytesIO()

    with tarfile.open(fileobj=buf, mode="w") as tf:
        for host_path, raw_target, mode_override in sources:
            host_path = Path(host_path)
            target = _norm_target(raw_target)

            if host_path.is_file():
                arcname = FILES_PREFIX + target
                mode = mode_override if mode_override is not None else _stat_mode(host_path)
                data = host_path.read_bytes()
                _add_file(tf, arcname, data, mode)
                entries.append(FileEntry(arcname, target, mode, len(data)))
            elif host_path.is_dir():
                for sub in sorted(host_path.rglob("*")):
                    if not sub.is_file() or sub.is_symlink():
                        continue
                    rel = sub.relative_to(host_path).as_posix()
                    sub_target = f"{target}/{rel}"
                    arcname = FILES_PREFIX + sub_target
                    mode = _stat_mode(sub)
                    data = sub.read_bytes()
                    _add_file(tf, arcname, data, mode)
                    entries.append(FileEntry(arcname, sub_target, mode, len(data)))
            else:
                continue

        manifest = Manifest(
            profile=profile,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            files=entries,
            env=env,
        )
        _add_file(tf, MANIFEST_NAME, manifest.to_json().encode("utf-8"), 0o600)

    return zstandard.ZstdCompressor(level=ZSTD_LEVEL).compress(buf.getvalue())


def unpack(data: bytes, dest: Path) -> Manifest:
    """Decompress + extract a bundle into dest. Applies manifest modes to files."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    raw = zstandard.ZstdDecompressor().decompress(data)
    buf = io.BytesIO(raw)

    with tarfile.open(fileobj=buf, mode="r") as tf:
        member = tf.getmember(MANIFEST_NAME)
        f = tf.extractfile(member)
        if f is None:
            raise ValueError("manifest.json missing from bundle")
        manifest = Manifest.from_json(f.read().decode("utf-8"))

        by_arcname = {e.arcname: e for e in manifest.files}

        for entry in manifest.files:
            try:
                m = tf.getmember(entry.arcname)
            except KeyError as exc:
                raise ValueError(f"manifest references missing arcname: {entry.arcname}") from exc
            src = tf.extractfile(m)
            if src is None:
                raise ValueError(f"could not read arcname: {entry.arcname}")
            target_path = dest / entry.target
            target_path.parent.mkdir(parents=True, exist_ok=True, mode=DEFAULT_DIR_MODE)
            target_path.write_bytes(src.read())
            os.chmod(target_path, entry.mode)

        for member in tf.getmembers():
            if member.name == MANIFEST_NAME:
                continue
            if member.name not in by_arcname:
                raise ValueError(f"tar contains file not in manifest: {member.name}")

    return manifest


def _add_file(tf: tarfile.TarFile, arcname: str, data: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mode = mode
    info.mtime = 0
    tf.addfile(info, io.BytesIO(data))
