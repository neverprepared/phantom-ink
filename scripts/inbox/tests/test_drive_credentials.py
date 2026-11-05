"""Credential selection for Drive access: user OAuth vs service account.

Why this matters: an OAuth consent screen whose publishing status is "Testing"
is issued refresh tokens that expire after 7 DAYS, which silently converts a
scheduled drain into a job that works for a week and then fails forever. A
service-account key is static on disk and never expires, so it is the only
credential shape that suits unattended execution.

The trade is that a service account cannot OWN files in a personal My Drive --
it becomes the owner on create and has no quota there -- so file creation has
to be suppressed rather than attempted. These tests pin both halves.
"""

from __future__ import annotations

import json

import pytest

import drive
from common import ConfigError, load_config


@pytest.fixture(scope="module")
def rsa_pem() -> str:
    """A throwaway RSA key, generated per run.

    google-auth genuinely parses the private key when building service-account
    credentials, so a placeholder string will not do. Generating one here keeps
    a private-key-shaped blob out of the repository.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def sa_key(tmp_path, rsa_pem):
    """A well-formed service-account key file."""
    path = tmp_path / "sa.json"
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "proj",
                "private_key_id": "kid",
                "private_key": rsa_pem,
                "client_email": "inbox@proj.iam.gserviceaccount.com",
                "client_id": "123",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    )
    return path


class TestServiceAccountCredentials:
    def test_loads_a_service_account_key(self, sa_key):
        creds = drive.load_credentials("unused@example.com", sa_key_path=str(sa_key))

        from google.oauth2 import service_account

        assert isinstance(creds, service_account.Credentials)
        assert creds.service_account_email == "inbox@proj.iam.gserviceaccount.com"

    def test_requests_the_full_drive_scope(self, sa_key):
        """Moving a human's file needs `drive`; drive.file can never see it."""
        creds = drive.load_credentials("unused@example.com", sa_key_path=str(sa_key))

        assert list(creds.scopes) == drive.SCOPES

    def test_the_account_argument_is_not_consulted(self, sa_key, monkeypatch):
        """A service account must not fall back to the OAuth store.

        Guards against a branch that reads the key AND still stats the per-account
        OAuth file, which would make a working service account depend on an
        unrelated -- and probably expired -- user credential being present.
        """
        monkeypatch.setattr(drive, "CRED_DIR", tmp := sa_key.parent / "absent")

        creds = drive.load_credentials("nobody@example.com", sa_key_path=str(sa_key))

        assert creds is not None
        assert not tmp.exists()

    def test_a_missing_key_file_is_a_config_error(self, tmp_path):
        missing = tmp_path / "nope.json"

        with pytest.raises(ConfigError, match=str(missing)):
            drive.load_credentials("a@example.com", sa_key_path=str(missing))

    def test_an_oauth_file_passed_as_a_key_is_refused(self, tmp_path):
        """The two credential files look alike and live in the same directory.

        Pointing the key variable at a user-OAuth file would otherwise fail deep
        inside google-auth with a KeyError on a missing private_key.
        """
        wrong = tmp_path / "wrong.json"
        wrong.write_text(json.dumps({"refresh_token": "x", "client_id": "y"}))

        with pytest.raises(ConfigError, match="service_account"):
            drive.load_credentials("a@example.com", sa_key_path=str(wrong))

    def test_unparseable_json_is_a_config_error(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")

        with pytest.raises(ConfigError, match=str(bad)):
            drive.load_credentials("a@example.com", sa_key_path=str(bad))


class TestUserOAuthStillWorks:
    """The workstation keeps the OAuth path; only m3 needs a service account."""

    def test_absent_oauth_credentials_still_name_the_account_and_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(drive, "CRED_DIR", tmp_path)

        with pytest.raises(ConfigError, match="a@example.com"):
            drive.load_credentials("a@example.com")

    def test_an_empty_key_path_means_user_oauth(self, tmp_path, monkeypatch):
        """Unset env vars arrive as "" and must not be read as a key path."""
        monkeypatch.setattr(drive, "CRED_DIR", tmp_path)

        with pytest.raises(ConfigError, match="google-workspace MCP server"):
            drive.load_credentials("a@example.com", sa_key_path="")


class TestConfig:
    def test_reads_the_service_account_key_path(self):
        cfg = load_config(
            "personal", env={"CL_INBOX_GOOGLE_SA_KEY": "/keys/sa.json", "WORKSPACE_HOME": "/w"}
        )

        assert cfg.google_sa_key == "/keys/sa.json"

    def test_defaults_to_no_service_account(self):
        cfg = load_config("personal", env={"WORKSPACE_HOME": "/w"})

        assert cfg.google_sa_key == ""


class FakeFiles:
    def __init__(self):
        self.created: list[dict] = []

    def create(self, **kwargs):
        self.created.append(kwargs)

        class Req:
            def execute(self, **_):
                return {"id": "new"}

        return Req()


class FakeService:
    def __init__(self):
        self._files = FakeFiles()

    def files(self):
        return self._files


class TestWriteNoteOwnership:
    """A service account cannot own a new file in a personal My Drive.

    `files.create` returns 403 storageQuotaExceeded there even when the service
    account's own Drive is empty, so the call is suppressed rather than made and
    allowed to fail. Both callers already move the file out of the inbox BEFORE
    writing the note, so skipping the note loses no queue state.
    """

    def test_a_note_is_written_when_credentials_can_own_files(self):
        svc = FakeService()
        d = drive.Drive(svc, "root", "personal")

        d.write_note("parent-id", "n.txt", "why")

        assert len(svc.files().created) == 1
        assert svc.files().created[0]["body"]["parents"] == ["parent-id"]

    def test_a_note_is_skipped_when_credentials_cannot_own_files(self):
        svc = FakeService()
        d = drive.Drive(svc, "root", "personal", can_create_files=False)

        d.write_note("parent-id", "n.txt", "why")

        assert svc.files().created == []

    def test_skipping_is_logged_so_the_absence_is_explainable(self, capsys):
        """On stderr: stdout is reserved for the entries contract."""
        d = drive.Drive(FakeService(), "root", "personal", can_create_files=False)

        d.write_note("parent-id", "n.txt", "why")

        assert "n.txt" in capsys.readouterr().err


class TestOpenDrive:
    def test_a_service_account_cannot_create_files(self, sa_key, monkeypatch):
        monkeypatch.setattr(drive, "build_service", lambda *a, **k: FakeService())
        cfg = load_config(
            "personal",
            env={"CL_INBOX_GOOGLE_SA_KEY": str(sa_key), "WORKSPACE_HOME": "/w"},
        )

        assert drive.open_drive(cfg).can_create_files is False

    def test_user_oauth_can_create_files(self, monkeypatch):
        monkeypatch.setattr(drive, "build_service", lambda *a, **k: FakeService())
        cfg = load_config("personal", env={"WORKSPACE_HOME": "/w"})

        assert drive.open_drive(cfg).can_create_files is True

    def test_the_key_path_reaches_build_service(self, sa_key, monkeypatch):
        """open_drive must forward the key, not silently drop it."""
        seen = {}

        def fake_build(account, sa_key_path=None):
            seen["account"] = account
            seen["sa_key_path"] = sa_key_path
            return FakeService()

        monkeypatch.setattr(drive, "build_service", fake_build)
        cfg = load_config(
            "personal",
            env={"CL_INBOX_GOOGLE_SA_KEY": str(sa_key), "WORKSPACE_HOME": "/w"},
        )

        drive.open_drive(cfg)

        assert seen["sa_key_path"] == str(sa_key)


class FakeFilesWithList(FakeFiles):
    """Also answers the folder lookup `ensure_folder` does first."""

    def list(self, **_):
        class Req:
            def execute(self, **_):
                return {"files": []}

        return Req()


class FakeServiceWithList(FakeService):
    def __init__(self):
        self._files = FakeFilesWithList()


class TestEnsureFolderOwnership:
    """`--init` creates folders, and a folder is a file for quota purposes.

    Reached only via resolve(create=True), so a normal run against an existing
    tree never touches it -- but when it is reached under a service account it
    must say so, instead of surfacing a bare 403 storageQuotaExceeded from a
    call that could never have worked.
    """

    def test_an_existing_folder_is_returned_without_creating(self):
        svc = FakeServiceWithList()
        d = drive.Drive(svc, "root", "personal", can_create_files=False)
        d._folder_cache[("parent", "inbox")] = "cached-id"

        assert d.ensure_folder("parent", "inbox") == "cached-id"
        assert svc.files().created == []

    def test_creating_a_missing_folder_is_refused_with_instructions(self):
        d = drive.Drive(FakeServiceWithList(), "root", "personal", can_create_files=False)

        with pytest.raises(ConfigError, match="--init"):
            d.ensure_folder("parent", "inbox")

    def test_user_oauth_still_creates_folders(self):
        svc = FakeServiceWithList()
        d = drive.Drive(svc, "root", "personal")

        assert d.ensure_folder("parent", "inbox") == "new"
        assert svc.files().created[0]["body"]["mimeType"] == drive.FOLDER_MIME
