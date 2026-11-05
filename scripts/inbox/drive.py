"""Drive access for the inbox pipeline.

Two credential shapes, chosen by whether CL_INBOX_GOOGLE_SA_KEY is set.

**User OAuth** (the default) reuses the google-workspace MCP server's own store
(~/.google_workspace_mcp/credentials/<account>.json), so this pipeline adds no
new OAuth flow and no new consent screen. google-auth refreshes an expired
access token in place and we write the refreshed token back, exactly as the MCP
server would. Suitable for interactive runs on a workstation where a human can
re-authorise.

**A service-account key** suits unattended runs, and on a headless host it is
the only shape that works: a consent screen whose publishing status is
"Testing" is issued refresh tokens expiring in 7 DAYS, which turns a scheduled
drain into a job that works for a week and then fails forever with
`invalid_grant`. A key file never expires.

A service account reaches a consumer account's Drive by having the inbox folder
SHARED with its client_email -- domain-wide delegation needs a Workspace admin
console and is unavailable for gmail.com. It can read, list, export, move,
trash and delete the human's files, but it cannot CREATE one: it becomes the
owner on create and owning a file in a personal My Drive requires quota it does
not have there, so `files.create` returns 403 storageQuotaExceeded even when
the service account's own Drive is empty. Hence `can_create_files`.
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
    Config,
    ConfigError,
    log,
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


def load_service_account_credentials(sa_key_path: str):
    """Build credentials from a service-account key file.

    Validated eagerly and by shape, because the two credential files look alike
    and live a directory apart: pointing this at a user-OAuth JSON otherwise
    fails deep inside google-auth on a missing `private_key`, which reads as a
    library bug rather than a misconfiguration.
    """
    from google.oauth2 import service_account

    path = Path(sa_key_path)
    if not path.is_file():
        raise ConfigError(
            f"no service-account key at {path} (from CL_INBOX_GOOGLE_SA_KEY). "
            "Download the key JSON for the service account from Google Cloud, "
            "and share the Drive inbox folder with its client_email."
        )
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"service-account key at {path} is not valid JSON: {exc}") from exc
    if info.get("type") != "service_account":
        raise ConfigError(
            f'{path} is not a service-account key: expected type="service_account", '
            f"found {info.get('type') or 'no type field'!r}. CL_INBOX_GOOGLE_SA_KEY "
            "wants the key downloaded from Google Cloud, not a user OAuth credential."
        )
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def load_credentials(account: str, sa_key_path: str | None = None):
    """Credentials for Drive: a service-account key if configured, else user OAuth."""
    if sa_key_path:
        return load_service_account_credentials(sa_key_path)

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


def build_service(account: str, sa_key_path: str | None = None):
    from googleapiclient.discovery import build

    # httplib2 honours the default socket timeout; there is no per-request knob
    # on the discovery client without hand-building an AuthorizedHttp.
    socket.setdefaulttimeout(DRIVE_TIMEOUT_S)
    return build(
        "drive",
        "v3",
        credentials=load_credentials(account, sa_key_path=sa_key_path),
        cache_discovery=False,
    )


def q_escape(value: str) -> str:
    """Escape a literal for the Drive v3 query language (single-quoted strings)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


# -- folder handles -------------------------------------------------------------


@dataclass
class InboxFolders:
    """Resolved folder ids for one profile's inbox lifecycle."""

    profile: str
    ids: dict[str, str]

    def __getitem__(self, stage: str) -> str:
        return self.ids[stage]


class Drive:
    """Thin wrapper over Drive v3 with just the operations this pipeline needs."""

    def __init__(self, service, root_name: str, profile: str, *, can_create_files: bool = True):
        self.svc = service
        self.root_name = root_name
        self.profile = profile
        # False under a service account, which cannot own a new file in a
        # personal My Drive. Only write_note creates anything.
        self.can_create_files = can_create_files
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

    def _find_shared_folder(self, name: str) -> str | None:
        """A folder SHARED with this account, which `'root' in parents` cannot see.

        The alias `root` means the authenticated identity's own My Drive root. A
        service account's root is its own -- empty -- Drive, so a folder the human
        shared with it lives under THEIR root and is reachable only this way.
        """
        key = ("sharedWithMe", name)
        if key in self._folder_cache:
            return self._folder_cache[key]
        q = (
            f"name = '{q_escape(name)}' and mimeType = '{FOLDER_MIME}' "
            "and sharedWithMe = true and trashed = false"
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

    def _resolve_root(self, *, create: bool) -> str:
        """The configured root folder: owned by us, else shared with us.

        Owned first, so the user-OAuth path costs exactly the round-trip it
        always did and never asks about shared folders.
        """
        owned = self._find_child("root", self.root_name)
        if owned:
            return owned
        shared = self._find_shared_folder(self.root_name)
        if shared:
            return shared
        if create:
            return self.ensure_folder("root", self.root_name)
        raise ConfigError(
            f"Drive folder {self.root_name!r} is neither in this account's My Drive "
            "nor shared with it. Run the drain script with --init once to create the "
            "tree, or share the existing folder with this account."
        )

    def ensure_folder(self, parent: str, name: str) -> str:
        found = self._find_child(parent, name)
        if found:
            return found
        # A folder is a file for quota purposes, so this is the second path a
        # service account cannot take. Refused rather than attempted: unlike a
        # note, a missing folder is not something the run can carry on without.
        if not self.can_create_files:
            raise ConfigError(
                f"cannot create Drive folder {name!r}: these credentials cannot own "
                "files (a service account has no quota in a personal My Drive). "
                "Create the tree once with user OAuth (--init), then share the "
                "profile folder with the service account."
            )
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

    def resolve(self, *, create: bool = False) -> InboxFolders:
        """Resolve <root>/<profile>/{inbox,processing,quarantine,rejected}.

        Cached per Drive instance via the folder cache, so the repeated calls
        the stages make cost one round-trip each per run.
        """
        get = self.ensure_folder if create else self._require_folder
        root = self._resolve_root(create=create)
        prof = get(root, self.profile)
        ids = {stage: get(prof, stage) for stage in LIFECYCLE}
        ids["_root"] = prof
        return InboxFolders(profile=self.profile, ids=ids)

    def init_tree(self) -> InboxFolders:
        return self.resolve(create=True)

    # -- files -----------------------------------------------------------------

    def list_inbox(self, folders: InboxFolders, cap: int) -> list[dict[str, Any]]:
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

    def trash(self, file_id: str) -> None:
        """Send a filed item to Drive trash.

        Trash rather than files.delete: the item leaves the inbox immediately,
        which is the point, but a mis-extracted capture is still recoverable
        for ~30 days. The authoritative copy is now the vault note or the
        reminder, and this is the only remaining safety net for the original
        wording.
        """
        self.svc.files().update(fileId=file_id, body={"trashed": True}).execute(
            num_retries=DRIVE_RETRIES
        )

    def delete_forever(self, file_id: str) -> None:
        """Permanently delete a filed item (opt-in via --hard-delete)."""
        self.svc.files().delete(fileId=file_id).execute(num_retries=DRIVE_RETRIES)

    def dispose(self, file_id: str, *, hard_delete: bool = False) -> None:
        """Get a filed item out of the inbox for good."""
        if hard_delete:
            self.delete_forever(file_id)
        else:
            self.trash(file_id)

    def write_note(self, parent: str, name: str, text: str) -> None:
        """Drop a small plain-text note beside a rejected/quarantined file.

        Skipped entirely when the credentials cannot own files, rather than
        attempted and allowed to 403: the reason is already recorded in the
        returned entry and shipped by obslog, and both callers move the file out
        of the inbox BEFORE writing the note, so nothing is left mid-queue.
        """
        if not self.can_create_files:
            log(
                f"skipping Drive note {name!r}: these credentials cannot own files "
                "(a service account has no quota in a personal My Drive)"
            )
            return

        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(
            io.BytesIO(text.encode("utf-8")), mimetype="text/plain", resumable=False
        )
        self.svc.files().create(
            body={"name": name, "parents": [parent]}, media_body=media, fields="id"
        ).execute(num_retries=DRIVE_RETRIES)

    def stale_processing(self, folders: InboxFolders, older_than_s: int) -> list[dict[str, Any]]:
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
    return Drive(
        build_service(cfg.google_account, sa_key_path=cfg.google_sa_key or None),
        cfg.drive_root,
        cfg.profile,
        can_create_files=not cfg.google_sa_key,
    )
