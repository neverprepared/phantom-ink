"""Drive access for the inbox pipeline.

Credentials are reused from the google-workspace MCP server's own OAuth store
(~/.google_workspace_mcp/credentials/<account>.json). That store already holds
refresh tokens with full `drive` scope for the authorised accounts, so this
pipeline adds no new OAuth flow and no new consent screen. google-auth refreshes
an expired access token in place and we write the refreshed token back, exactly
as the MCP server would.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import (
    LIFECYCLE,
    MIME_DOC,
    QUEUES,
    Config,
    ConfigError,
    month_bucket,
)

CRED_DIR = Path.home() / ".google_workspace_mcp" / "credentials"

SCOPES = ["https://www.googleapis.com/auth/drive"]

FOLDER_MIME = "application/vnd.google-apps.folder"

# A single slow Drive call used to kill a whole drain run: httplib2 has no
# default socket timeout, so a stalled connection hangs until the collect job's
# 60s limit kills the process. Cap it well inside that budget and let the
# client retry, so a transient flake costs a retry rather than the run.
DRIVE_TIMEOUT_S = 15
DRIVE_RETRIES = 3


def load_credentials(account: str):
    """Load and refresh the MCP server's stored OAuth credentials."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = CRED_DIR / f"{account}.json"
    if not path.is_file():
        raise ConfigError(
            f"no Google credentials for {account} at {path}. "
            "Authorise once via the google-workspace MCP server, then retry."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or SCOPES,
    )
    if not creds.valid:
        if not creds.refresh_token:
            raise ConfigError(f"credentials for {account} are expired and have no refresh token")
        creds.refresh(Request())
        # Persist the refreshed access token so the MCP server benefits too.
        data["token"] = creds.token
        if creds.expiry:
            data["expiry"] = creds.expiry.isoformat()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return creds


def build_service(account: str):
    from googleapiclient.discovery import build

    # httplib2 honours the default socket timeout; there is no per-request knob
    # on the discovery client without hand-building an AuthorizedHttp.
    socket.setdefaulttimeout(DRIVE_TIMEOUT_S)
    return build("drive", "v3", credentials=load_credentials(account), cache_discovery=False)


def q_escape(value: str) -> str:
    """Escape a literal for the Drive v3 query language (single-quoted strings)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


# -- folder handles -------------------------------------------------------------


@dataclass
class QueueFolders:
    """Resolved folder ids for one queue's lifecycle."""

    queue: str
    ids: dict[str, str]

    def __getitem__(self, stage: str) -> str:
        return self.ids[stage]


class Drive:
    """Thin wrapper over Drive v3 with just the operations this pipeline needs."""

    def __init__(self, service, root_name: str, profile: str):
        self.svc = service
        self.root_name = root_name
        self.profile = profile
        self._folder_cache: dict[tuple[str, str], str] = {}

    # -- folders ---------------------------------------------------------------

    def _find_child(self, parent: str, name: str) -> str | None:
        key = (parent, name)
        if key in self._folder_cache:
            return self._folder_cache[key]
        q = (
            f"name = '{q_escape(name)}' and mimeType = '{FOLDER_MIME}' "
            f"and '{q_escape(parent)}' in parents and trashed = false"
        )
        resp = (
            self.svc.files()
            .list(q=q, fields="files(id,name)", pageSize=2, supportsAllDrives=False)
            .execute(num_retries=DRIVE_RETRIES)
        )
        files = resp.get("files", [])
        if not files:
            return None
        self._folder_cache[key] = files[0]["id"]
        return files[0]["id"]

    def ensure_folder(self, parent: str, name: str) -> str:
        found = self._find_child(parent, name)
        if found:
            return found
        created = (
            self.svc.files()
            .create(
                body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]},
                fields="id",
            )
            .execute(num_retries=DRIVE_RETRIES)
        )
        self._folder_cache[(parent, name)] = created["id"]
        return created["id"]

    def _require_folder(self, parent: str, name: str) -> str:
        found = self._find_child(parent, name)
        if not found:
            raise ConfigError(
                f"Drive folder {name!r} is missing. "
                "Run the drain script with --init once to create the tree."
            )
        return found

    def resolve_queue(self, queue: str, *, create: bool = False) -> QueueFolders:
        """Resolve <root>/<profile>/<queue>/{lifecycle} folder ids."""
        if queue not in QUEUES:
            raise ConfigError(f"unknown queue {queue!r}")
        get = self.ensure_folder if create else self._require_folder
        root = get("root", self.root_name)
        prof = get(root, self.profile)
        qroot = get(prof, queue)
        ids = {stage: get(qroot, stage) for stage in LIFECYCLE}
        ids["_queue_root"] = qroot
        return QueueFolders(queue=queue, ids=ids)

    def init_tree(self) -> dict[str, QueueFolders]:
        return {q: self.resolve_queue(q, create=True) for q in QUEUES}

    # -- files -----------------------------------------------------------------

    def list_inbox(self, folders: QueueFolders, cap: int) -> list[dict[str, Any]]:
        """Oldest-first children of inbox/, carrying the fields the gates need."""
        resp = (
            self.svc.files()
            .list(
                q=f"'{q_escape(folders['inbox'])}' in parents and trashed = false",
                fields=(
                    "files(id,name,mimeType,size,createdTime,modifiedTime,"
                    "owners(emailAddress,me),shared)"
                ),
                orderBy="createdTime",
                pageSize=min(max(cap, 1), 100),
                supportsAllDrives=False,
            )
            .execute(num_retries=DRIVE_RETRIES)
        )
        return [f for f in resp.get("files", []) if f.get("mimeType") != FOLDER_MIME][:cap]

    def read_text(self, meta: dict[str, Any], max_bytes: int) -> str:
        """Fetch a file as text; Google Docs are exported to markdown."""
        from googleapiclient.http import MediaIoBaseDownload

        file_id = meta["id"]
        if meta.get("mimeType") == MIME_DOC:
            req = self.svc.files().export_media(fileId=file_id, mimeType="text/markdown")
        else:
            req = self.svc.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, req, chunksize=max_bytes + 1)
        done = False
        while not done:
            _, done = downloader.next_chunk(num_retries=DRIVE_RETRIES)
            if buf.tell() > max_bytes:
                # A Doc has no reliable size in metadata, so the cap is also
                # enforced here, during the download, not just before it.
                raise ValueError(f"body exceeds {max_bytes} bytes")
        return buf.getvalue().decode("utf-8", errors="replace")

    def move(self, file_id: str, *, to: str, from_: str) -> None:
        self.svc.files().update(
            fileId=file_id, addParents=to, removeParents=from_, fields="id"
        ).execute(num_retries=DRIVE_RETRIES)

    def move_to_done(self, folders: QueueFolders, file_id: str) -> None:
        """Move into done/<YYYY-MM>, which keeps the archive listable."""
        bucket = self.ensure_folder(folders["done"], month_bucket())
        self.move(file_id, to=bucket, from_=folders["processing"])

    def write_note(self, parent: str, name: str, text: str) -> None:
        """Drop a small plain-text note beside a rejected/quarantined file."""
        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(
            io.BytesIO(text.encode("utf-8")), mimetype="text/plain", resumable=False
        )
        self.svc.files().create(
            body={"name": name, "parents": [parent]}, media_body=media, fields="id"
        ).execute(num_retries=DRIVE_RETRIES)

    def stale_processing(self, folders: QueueFolders, older_than_s: int) -> list[dict[str, Any]]:
        """Files stuck in processing/ longer than older_than_s."""
        resp = (
            self.svc.files()
            .list(
                q=f"'{q_escape(folders['processing'])}' in parents and trashed = false",
                fields="files(id,name,mimeType,modifiedTime)",
                orderBy="modifiedTime",
                pageSize=100,
                supportsAllDrives=False,
            )
            .execute(num_retries=DRIVE_RETRIES)
        )
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=older_than_s)
        out: list[dict[str, Any]] = []
        for f in resp.get("files", []):
            if f.get("mimeType") == FOLDER_MIME:
                continue
            try:
                mt = dt.datetime.fromisoformat(f["modifiedTime"])
            # An unparseable timestamp just means we cannot age this file out.
            except Exception:
                continue
            if mt < cutoff:
                out.append(f)
        return out


def open_drive(cfg: Config) -> Drive:
    return Drive(build_service(cfg.google_account), cfg.drive_root, cfg.profile)
