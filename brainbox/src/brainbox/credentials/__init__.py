"""Credential bundle delivery — sealed artifacts shipped from laptop to guests.

Phase 1: bundle format (manifest + tar.zst) and age sealing primitives.
Backend wiring lives in later phases.
"""

from .build import build_sealed_bundle
from .bundle import FileEntry, Manifest, pack, unpack
from .seal import generate_identity, recipient_of, seal, unseal

__all__ = [
    "FileEntry",
    "Manifest",
    "build_sealed_bundle",
    "generate_identity",
    "pack",
    "recipient_of",
    "seal",
    "unpack",
    "unseal",
]
