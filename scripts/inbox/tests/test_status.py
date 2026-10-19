"""Every run must report, including the paths that used to return early.

The apply stage returns before it ever touches Drive when the spool is empty,
which is the exact case that was previously silent: exit 0, empty stdout, no
entry, nothing in last_error. These tests drive `main()` for real (no network —
the empty-spool path short-circuits before any Drive or model call) and assert
a status row still comes out.
"""

from __future__ import annotations

import json

import pytest

import extract_apply
from common import ENTRY_KIND, STATUS_DONE, STATUS_ENTRY_PREFIX, STATUS_FAILED


def run_main(monkeypatch, capsys, tmp_path, argv):
    """Run extract_apply.main() against an isolated workspace home."""
    monkeypatch.setenv("WORKSPACE_PROFILE", "personal")
    monkeypatch.setenv("WORKSPACE_HOME", str(tmp_path))
    monkeypatch.delenv("CL_ROUTER_API", raising=False)
    monkeypatch.delenv("CL_API_KEY", raising=False)
    code = extract_apply.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_empty_spool_still_emits_a_status_row(monkeypatch, capsys, tmp_path):
    """The previously-silent case: nothing pending, exit 0, now visible."""
    code, entries = run_main(monkeypatch, capsys, tmp_path, ["--profile", "personal"])
    assert code == 0
    assert len(entries) == 1
    e = entries[0]
    assert e["id"] == f"{STATUS_ENTRY_PREFIX}-apply"
    assert e["kind"] == ENTRY_KIND
    assert e["status"] == STATUS_DONE
    assert "idle" in e["title"]


def test_stdout_is_a_single_json_array(monkeypatch, capsys, tmp_path):
    """runCollectCommand (app/collect.go:793) parses stdout as one array."""
    _, entries = run_main(monkeypatch, capsys, tmp_path, ["--profile", "personal"])
    assert isinstance(entries, list)


def test_missing_profile_still_emits_a_failure_row(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("WORKSPACE_PROFILE", raising=False)
    monkeypatch.delenv("CL_WORKSPACE_PROFILE", raising=False)
    code = extract_apply.main([])
    entries = json.loads(capsys.readouterr().out)
    assert code == 1
    assert len(entries) == 1
    assert entries[0]["status"] == STATUS_FAILED
    assert entries[0]["id"] == f"{STATUS_ENTRY_PREFIX}-apply"


def test_replayed_spool_records_are_reported_not_silently_dropped(monkeypatch, capsys, tmp_path):
    """A spool record whose content is already in the ledger is dropped. That
    used to emit nothing at all; now the count is visible."""
    from common import SpoolRecord, ledger_append, load_config, write_spool

    cfg = load_config(
        "personal", env={"WORKSPACE_PROFILE": "personal", "WORKSPACE_HOME": str(tmp_path)}
    )
    rec = SpoolRecord(
        item_id="deadbeefdeadbeef",
        profile="personal",
        declared_kind="todo",
        drive_file_id="fid",
        drive_name="note.md",
        mime="text/markdown",
        captured_at="2026-10-19T00:00:00Z",
        drained_at="2026-10-19T00:00:01Z",
        body="already filed",
    )
    write_spool(cfg, rec)
    ledger_append(cfg, {"item_id": rec.item_id, "target": "todo", "result": "x" * 64})

    code, entries = run_main(monkeypatch, capsys, tmp_path, ["--profile", "personal"])
    assert code == 0
    assert len(entries) == 1
    assert entries[0]["metadata"]["replays dropped"] == 1


@pytest.mark.parametrize("stage", ["drain", "apply"])
def test_status_entry_ids_are_the_two_we_expect(stage):
    """Stable ids are what make the row upsert instead of accumulating."""
    from common import status_entry

    assert status_entry(stage, ok=True, summary="idle")["id"] == (f"{STATUS_ENTRY_PREFIX}-{stage}")
