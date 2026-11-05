"""Terminal state for a credential that cannot trash: move to complete/.

In Drive only the file's OWNER may trash or delete it. An Editor cannot, even
with the full `drive` scope, so a service account -- which owns nothing of the
human's -- can never dispose of a capture. Found in production: the apply stage
filed the item correctly and then failed its cleanup with
`403 insufficientFilePermissions`, leaving the file in processing/ for good.

A MOVE is the one terminal action a non-owner does have, so disposal becomes a
move into a `complete/` folder. The folder is NOT part of LIFECYCLE on purpose:
a service account cannot create folders either, so requiring it would hard-fail
every run against an existing tree until a human made it.
"""

from __future__ import annotations

import pytest

import drive
from common import ConfigError

ROOT = "phantom-ink"
PROFILE = "personal"
COMPLETE = "complete"


class FakeFiles:
    def __init__(self, *, folders: dict, parents: dict | None = None):
        self.folders = folders  # (parent, name) -> id
        self.parents = parents or {}  # file_id -> [parent ids]
        self.updates: list[dict] = []
        self.deletes: list[str] = []

    def list(self, **kwargs):
        q = kwargs.get("q", "")
        hits = []
        for (parent, name), fid in self.folders.items():
            if f"'{parent}' in parents" in q and f"name = '{name}'" in q:
                hits = [{"id": fid, "name": name}]

        class Req:
            def execute(self, **_):
                return {"files": hits}

        return Req()

    def get(self, **kwargs):
        fid = kwargs["fileId"]
        parents = self.parents.get(fid, [])

        class Req:
            def execute(self, **_):
                return {"id": fid, "parents": parents}

        return Req()

    def update(self, **kwargs):
        self.updates.append(kwargs)

        class Req:
            def execute(self, **_):
                return {"id": kwargs["fileId"]}

        return Req()

    def delete(self, **kwargs):
        self.deletes.append(kwargs["fileId"])

        class Req:
            def execute(self, **_):
                return None

        return Req()


class FakeService:
    def __init__(self, **kwargs):
        self._files = FakeFiles(**kwargs)

    def files(self):
        return self._files


def tree(*, with_complete: bool) -> dict:
    folders = {("root", ROOT): "root-id", ("root-id", PROFILE): "prof-id"}
    for stage in drive.LIFECYCLE:
        folders[("prof-id", stage)] = f"{stage}-id"
    if with_complete:
        folders[("prof-id", COMPLETE)] = "complete-id"
    return folders


def make(*, with_complete: bool, can_dispose: bool):
    svc = FakeService(folders=tree(with_complete=with_complete), parents={"f1": ["processing-id"]})
    d = drive.Drive(svc, ROOT, PROFILE, can_create_files=can_dispose, can_dispose=can_dispose)
    return svc, d


class TestOwnerStillTrashes:
    """User OAuth owns the file; its disposal must not change."""

    def test_dispose_trashes(self):
        svc, d = make(with_complete=True, can_dispose=True)

        d.dispose("f1")

        assert svc.files().updates == [
            {"fileId": "f1", "body": {"trashed": True}},
        ]

    def test_hard_delete_still_deletes(self):
        svc, d = make(with_complete=True, can_dispose=True)

        d.dispose("f1", hard_delete=True)

        assert svc.files().deletes == ["f1"]


class TestNonOwnerMovesToComplete:
    def test_dispose_moves_instead_of_trashing(self):
        svc, d = make(with_complete=True, can_dispose=False)

        d.dispose("f1")

        assert not any("trashed" in str(u.get("body", "")) for u in svc.files().updates)
        move = svc.files().updates[-1]
        assert move["addParents"] == "complete-id"

    def test_the_move_removes_the_old_parent(self):
        """Otherwise the file is in BOTH folders and processing/ never drains."""
        svc, d = make(with_complete=True, can_dispose=False)

        d.dispose("f1")

        assert svc.files().updates[-1]["removeParents"] == "processing-id"

    def test_hard_delete_is_downgraded_to_a_move(self):
        """A non-owner cannot delete either, so honouring the flag would 403."""
        svc, d = make(with_complete=True, can_dispose=False)

        d.dispose("f1", hard_delete=True)

        assert svc.files().deletes == []
        assert svc.files().updates[-1]["addParents"] == "complete-id"

    def test_nothing_is_trashed_or_deleted(self):
        svc, d = make(with_complete=True, can_dispose=False)

        d.dispose("f1")

        assert svc.files().deletes == []


class TestMissingCompleteFolder:
    def test_it_is_a_config_error_naming_the_folder(self):
        _, d = make(with_complete=False, can_dispose=False)

        with pytest.raises(ConfigError, match=COMPLETE):
            d.dispose("f1")

    def test_the_message_says_a_human_must_create_it(self):
        """The credential that needs the folder cannot create it."""
        _, d = make(with_complete=False, can_dispose=False)

        with pytest.raises(ConfigError, match="creat"):
            d.dispose("f1")

    def test_an_owner_does_not_need_the_folder_at_all(self):
        svc, d = make(with_complete=False, can_dispose=True)

        d.dispose("f1")

        assert svc.files().updates[-1]["body"] == {"trashed": True}


class TestResolveTreatsCompleteAsOptional:
    def test_complete_is_exposed_when_present(self):
        _, d = make(with_complete=True, can_dispose=False)

        assert d.resolve().ids[COMPLETE] == "complete-id"

    def test_its_absence_does_not_fail_resolve(self):
        """complete/ is deliberately not in LIFECYCLE -- see the module docstring."""
        _, d = make(with_complete=False, can_dispose=False)

        folders = d.resolve()

        assert COMPLETE not in folders.ids
        assert folders.ids["inbox"] == "inbox-id"


class TestOpenDrive:
    def test_a_service_account_cannot_dispose(self, tmp_path, monkeypatch):
        from common import load_config

        monkeypatch.setattr(drive, "build_service", lambda *a, **k: FakeService(folders={}))
        key = tmp_path / "sa.json"
        key.write_text("{}")
        cfg = load_config(
            "personal", env={"CL_INBOX_GOOGLE_SA_KEY": str(key), "WORKSPACE_HOME": "/w"}
        )

        assert drive.open_drive(cfg).can_dispose is False

    def test_user_oauth_can_dispose(self, monkeypatch):
        from common import load_config

        monkeypatch.setattr(drive, "build_service", lambda *a, **k: FakeService(folders={}))
        cfg = load_config("personal", env={"WORKSPACE_HOME": "/w"})

        assert drive.open_drive(cfg).can_dispose is True
