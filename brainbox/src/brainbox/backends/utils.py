"""Shared backend utility functions."""
from __future__ import annotations

import io
import tarfile


def _extract_from_bundle(bundle_bytes: bytes, arcname: str) -> str | None:
    """Extract a single text file from a tar.gz bundle by archive name."""
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:gz") as tf:
            member = tf.getmember(arcname)
            f = tf.extractfile(member)
            return f.read().decode("utf-8") if f else None
    except (KeyError, tarfile.TarError, OSError):
        return None
