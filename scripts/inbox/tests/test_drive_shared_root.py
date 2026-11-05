"""Resolving the inbox root when the folder is SHARED rather than owned.

The alias `root` in a Drive query means the authenticated user's own My Drive
root. For a service account that is its own -- empty -- Drive, so a folder the
human shared with it is invisible to `'root' in parents`: it lives under the
human's root and is reachable only via `sharedWithMe = true`, or by id.

This was found in production. The credential worked perfectly and the error was
"Drive folder 'phantom-ink' is missing. Run the drain script with --init once",
which points at the folder tree rather than at the lookup -- and `--init` would
then have been refused by the ownership guard.
"""

from __future__ import annotations

import pytest

import drive
from common import ConfigError

ROOT = "phantom-ink"
PROFILE = "personal"


class RecordingFiles:
    """Answers folder lookups from a table keyed by the query's shape."""

    def __init__(self, *, owned: dict | None = None, shared: dict | None = None):
        self.owned = owned or {}  # (parent, name) -> id
        self.shared = shared or {}  # name -> id
        self.queries: list[str] = []
        self.created: list[dict] = []

    def list(self, **kwargs):
        q = kwargs.get("q", "")
        self.queries.append(q)
        hits: list[dict] = []
        if "sharedWithMe" in q:
            for name, fid in self.shared.items():
                if f"name = '{name}'" in q:
                    hits = [{"id": fid, "name": name}]
        else:
            for (parent, name), fid in self.owned.items():
                if f"'{parent}' in parents" in q and f"name = '{name}'" in q:
                    hits = [{"id": fid, "name": name}]

        class Req:
            def execute(self, **_):
                return {"files": hits}

        return Req()

    def create(self, **kwargs):
        self.created.append(kwargs)

        class Req:
            def execute(self, **_):
                return {"id": "created-id"}

        return Req()


class RecordingService:
    def __init__(self, **kwargs):
        self._files = RecordingFiles(**kwargs)

    def files(self):
        return self._files


def full_tree(root_id: str) -> dict:
    """The profile folder and its lifecycle children, parented under root_id."""
    owned = {(root_id, PROFILE): "prof-id"}
    for stage in drive.LIFECYCLE:
        owned[("prof-id", stage)] = f"{stage}-id"
    return owned


class TestOwnedRootStillWins:
    """User OAuth owns the folder; nothing about that path may change."""

    def test_a_root_in_my_drive_is_used(self):
        svc = RecordingService(owned={("root", ROOT): "root-id", **full_tree("root-id")})
        d = drive.Drive(svc, ROOT, PROFILE)

        folders = d.resolve()

        assert folders.ids["inbox"] == "inbox-id"

    def test_sharedwithme_is_not_queried_when_the_root_is_owned(self):
        """Avoid a pointless extra round-trip on the common path."""
        svc = RecordingService(owned={("root", ROOT): "root-id", **full_tree("root-id")})

        drive.Drive(svc, ROOT, PROFILE).resolve()

        assert not any("sharedWithMe" in q for q in svc.files().queries)


class TestSharedRoot:
    def test_a_shared_root_is_found(self):
        svc = RecordingService(shared={ROOT: "shared-root-id"}, owned=full_tree("shared-root-id"))
        d = drive.Drive(svc, ROOT, PROFILE, can_create_files=False)

        folders = d.resolve()

        assert folders.ids["inbox"] == "inbox-id"
        assert folders.ids["_root"] == "prof-id"

    def test_the_shared_lookup_is_restricted_to_folders(self):
        """A FILE of the same name must not be mistaken for the root."""
        svc = RecordingService(shared={ROOT: "shared-root-id"}, owned=full_tree("shared-root-id"))

        drive.Drive(svc, ROOT, PROFILE, can_create_files=False).resolve()

        shared_q = next(q for q in svc.files().queries if "sharedWithMe" in q)
        assert drive.FOLDER_MIME in shared_q
        assert f"name = '{ROOT}'" in shared_q

    def test_a_shared_root_satisfies_create_mode_without_creating(self):
        """--init against an already-shared tree must not try to create it."""
        svc = RecordingService(shared={ROOT: "shared-root-id"}, owned=full_tree("shared-root-id"))
        d = drive.Drive(svc, ROOT, PROFILE, can_create_files=False)

        d.resolve(create=True)

        assert svc.files().created == []

    def test_the_root_lookup_is_cached(self):
        """resolve() is called repeatedly per run; the fallback must not re-query."""
        svc = RecordingService(shared={ROOT: "shared-root-id"}, owned=full_tree("shared-root-id"))
        d = drive.Drive(svc, ROOT, PROFILE, can_create_files=False)

        d.resolve()
        d.resolve()

        assert sum("sharedWithMe" in q for q in svc.files().queries) == 1


class TestNeitherOwnedNorShared:
    def test_a_genuinely_absent_root_still_says_init(self):
        svc = RecordingService()
        d = drive.Drive(svc, ROOT, PROFILE)

        with pytest.raises(ConfigError, match="--init"):
            d.resolve()

    def test_the_message_mentions_sharing_for_a_non_owner(self):
        """A service account's likeliest cause is an unshared folder, not an absent one."""
        svc = RecordingService()
        d = drive.Drive(svc, ROOT, PROFILE, can_create_files=False)

        with pytest.raises(ConfigError, match="shar"):
            d.resolve()

    def test_a_missing_profile_folder_under_a_shared_root_is_distinguishable(self):
        """The root was shared but the profile subfolder is absent."""
        svc = RecordingService(shared={ROOT: "shared-root-id"})
        d = drive.Drive(svc, ROOT, PROFILE, can_create_files=False)

        with pytest.raises(ConfigError, match=PROFILE):
            d.resolve()
