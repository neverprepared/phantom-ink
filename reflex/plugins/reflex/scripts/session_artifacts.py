"""Phase 3 — upload session EVIDENCE (transcript + diff) as artifacts and return
handles for the session.summary event to reference.

Evidence is heavy and opaque, so it never goes in the event metadata — it's
stored once in the profile's object store and referenced by handle. Lands under
``<profile>/sessions/<id>/`` (a NON-protected prefix): the four vault dirs
(memory/artifacts/tasks/skills) are read-only through the artifacts API, so
session evidence uses a sibling namespace instead.

Stdlib only, fail-open — a failed upload just means the event carries fewer (or
no) artifact handles; it never blocks the summary.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request

# Don't push a giant transcript through the object API — evidence over this is
# skipped (the facts + narrative still ship). 25 MiB covers all but pathological runs.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _bucket() -> str:
    return os.environ.get("PHANTOM_ARTIFACT_BUCKET", "phantom-platform")


def _put_object(api: str, key_auth: str, bucket: str, key: str, data: bytes, content_type: str) -> dict | None:
    from urllib.parse import quote

    url = (
        f"{api.rstrip('/')}/api/artifacts/{bucket}/object"
        f"?key={quote(key, safe='')}&content_type={quote(content_type, safe='')}"
    )
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={"Content-Type": content_type, "X-API-Key": key_auth},
    )
    try:
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            if 200 <= resp.status < 300:
                return {"handle": f"{bucket}/{key}", "bytes": len(data)}
    except Exception:
        return None
    return None


def _read_file_capped(path: str) -> bytes | None:
    try:
        if os.path.getsize(path) > MAX_UPLOAD_BYTES:
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _git_diff(cwd: str) -> bytes | None:
    try:
        out = subprocess.run(
            ["git", "diff", "HEAD"], cwd=cwd or None,
            capture_output=True, timeout=6.0,
        )
        data = out.stdout
        if out.returncode == 0 and data and len(data) <= MAX_UPLOAD_BYTES:
            return data
    except Exception:
        return None
    return None


def maybe_upload_artifacts(
    api: str, key_auth: str, profile: str, session_id: str, transcript_path: str, cwd: str
) -> list[dict]:
    bucket = _bucket()
    base = f"{profile}/sessions/{session_id}"
    out: list[dict] = []

    if transcript_path:
        blob = _read_file_capped(transcript_path)
        if blob:
            art = _put_object(api, key_auth, bucket, f"{base}/transcript.jsonl", blob, "application/x-ndjson")
            if art:
                out.append({**art, "kind": "transcript"})

    diff = _git_diff(cwd)
    if diff:
        art = _put_object(api, key_auth, bucket, f"{base}/session.diff", diff, "text/x-patch")
        if art:
            out.append({**art, "kind": "diff"})

    return out


if __name__ == "__main__":  # tiny self-check / manual invocation
    import sys

    print(json.dumps(maybe_upload_artifacts(
        os.environ.get("PHANTOM_API_URL", "http://127.0.0.1:9910"),
        os.environ.get("PHANTOM_API_KEY", os.environ.get("CL_API_KEY", "")),
        "personal", "manual-test", sys.argv[1] if len(sys.argv) > 1 else "", os.getcwd(),
    ), indent=2))
