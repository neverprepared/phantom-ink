"""Spool, ledger, frontmatter and the secret scan. No network."""

from __future__ import annotations

import json

import pytest

from common import (
    Config,
    ConfigError,
    SpoolRecord,
    declared_kind,
    drop_spool,
    entry,
    item_id_for,
    kind_from_filename,
    ledger_append,
    ledger_ids,
    load_config,
    normalise_kind,
    read_pending,
    scan_secrets,
    split_frontmatter,
    write_spool,
)


@pytest.fixture
def cfg(tmp_path) -> Config:
    return load_config(
        "personal",
        env={
            "WORKSPACE_PROFILE": "personal",
            "WORKSPACE_HOME": str(tmp_path),
            "CL_ROUTER_API": "http://127.0.0.1:9910/",
            "CL_API_KEY": "k",
        },
    )


def rec(item_id="aaaa0000", declared="", body="hello") -> SpoolRecord:
    return SpoolRecord(
        item_id=item_id,
        profile="personal",
        declared_kind=declared,
        drive_file_id="fid",
        drive_name="note.md",
        mime="text/markdown",
        captured_at="2026-10-18T12:00:00Z",
        drained_at="2026-10-18T12:00:01Z",
        body=body,
    )


# -- config ---------------------------------------------------------------------


def test_config_requires_a_profile():
    with pytest.raises(ConfigError, match="no profile"):
        load_config(None, env={})


def test_config_strips_trailing_slash_from_urls(cfg):
    assert cfg.router_api == "http://127.0.0.1:9910"


def test_reminders_list_defaults_per_profile(cfg):
    """Per-profile lists are what keep profiles from mixing in Reminders."""
    assert cfg.reminders_list == "phantom-personal"


def test_reminders_list_is_overridable(tmp_path):
    c = load_config(
        "personal", env={"WORKSPACE_HOME": str(tmp_path), "CL_INBOX_REMINDERS_LIST": "Work"}
    )
    assert c.reminders_list == "Work"


# -- item identity --------------------------------------------------------------


def test_item_id_is_content_addressed():
    assert item_id_for("same") == item_id_for("same")
    assert item_id_for("a") != item_id_for("b")
    assert len(item_id_for("x")) == 16


# -- spool ----------------------------------------------------------------------


def test_spool_roundtrip(cfg):
    write_spool(cfg, rec(body="buy milk"))
    got = read_pending(cfg)
    assert len(got) == 1
    assert got[0].body == "buy milk"
    assert got[0].declared_kind == ""


def test_spool_write_is_atomic_leaving_no_temp_files(cfg):
    write_spool(cfg, rec())
    leftovers = [p.name for p in cfg.pending_dir.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_read_pending_respects_the_limit(cfg):
    for i in range(5):
        write_spool(cfg, rec(item_id=f"id{i:04d}", body=f"body {i}"))
    assert len(read_pending(cfg, limit=2)) == 2


def test_drop_spool_removes_the_record(cfg):
    write_spool(cfg, rec())
    drop_spool(cfg, "aaaa0000")
    assert read_pending(cfg) == []


def test_drop_spool_is_idempotent(cfg):
    drop_spool(cfg, "never-existed")  # must not raise


def test_corrupt_spool_file_is_set_aside_not_retried(cfg):
    """A bad file must not stall the queue forever."""
    cfg.pending_dir.mkdir(parents=True, exist_ok=True)
    bad = cfg.pending_dir / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    assert read_pending(cfg) == []
    assert not bad.exists()
    assert (cfg.pending_dir / "broken.corrupt").exists()


# -- ledger ---------------------------------------------------------------------


def test_ledger_append_and_read(cfg):
    ledger_append(cfg, {"item_id": "abc", "target": "todo"})
    ledger_append(cfg, {"item_id": "def", "target": "idea"})
    assert ledger_ids(cfg) == {"abc", "def"}


def test_ledger_stamps_applied_at(cfg):
    ledger_append(cfg, {"item_id": "abc"})
    row = json.loads(cfg.ledger_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["applied_at"].endswith("Z")


def test_ledger_ids_is_empty_before_anything_is_applied(cfg):
    assert ledger_ids(cfg) == set()


def test_ledger_tolerates_a_corrupt_line(cfg):
    """A torn write must not hide every other applied item."""
    ledger_append(cfg, {"item_id": "good"})
    with cfg.ledger_path.open("a", encoding="utf-8") as fh:
        fh.write("{partial\n")
    ledger_append(cfg, {"item_id": "also-good"})
    assert ledger_ids(cfg) == {"good", "also-good"}


# -- frontmatter ----------------------------------------------------------------


def test_splits_frontmatter():
    fm, body = split_frontmatter("---\ntitle: X\npriority: high\n---\nthe body\n")
    assert fm == {"title": "X", "priority": "high"}
    assert body.strip() == "the body"


def test_no_frontmatter_returns_the_whole_text():
    fm, body = split_frontmatter("just a note\n")
    assert fm == {}
    assert body == "just a note\n"


def test_malformed_frontmatter_does_not_lose_the_note():
    """Losing a capture over a YAML typo would defeat the point."""
    fm, body = split_frontmatter("---\n: : :\nnot: [valid\n---\nkeep me\n")
    assert fm == {}
    assert "keep me" in body


def test_untrusted_frontmatter_keys_are_dropped():
    """The accepted key set is closed, so a note cannot set arbitrary fields."""
    fm, _ = split_frontmatter(
        "---\ntitle: X\ntoken: sekrit\ncommand: rm -rf /\nactionable: true\n---\nbody\n"
    )
    assert fm == {"title": "X", "actionable": True}


def test_non_mapping_frontmatter_is_ignored():
    fm, _body = split_frontmatter("---\n- a\n- b\n---\nbody\n")
    assert fm == {}


# -- secret scan ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("a" * 64, "64-hex"),
        ("sk-abcdefghijklmnopqrstuvwx", "openai-sk"),
        ("ghp_" + "A" * 24, "github-pat"),
        ("github_pat_" + "A" * 24, "github-fine-grained"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----", "private-key"),
        ("AKIAIOSFODNN7EXAMPLE", "aws-akid"),
        ("xoxb-123456789012-abcdefgh", "slack"),
        ("AIza" + "B" * 35, "google-api-key"),
        ("sk-ant-api03-abcdefghijklmnop", "anthropic"),
    ],
)
def test_secret_scan_catches_credential_shapes(text, expected):
    """A credential learned into a vault is durable, synthesised and recallable."""
    assert expected in scan_secrets(f"notes about the thing\n{text}\nmore notes")


def test_secret_scan_passes_ordinary_capture_text():
    assert scan_secrets("remind me friday to call the accountant about Q3") == []


def test_secret_scan_does_not_flag_a_short_hex_string():
    assert scan_secrets("commit 13c9940 broke the build") == []


# -- collect-job entry contract -------------------------------------------------


def test_entry_has_the_required_fields():
    """runCollectCommand drops entries missing id/kind/title (app/collect.go:793)."""
    e = entry("id1", "inbox_pending", "a title")
    assert e["id"] and e["kind"] and e["title"]
    assert e["status"] == "active"


def test_entry_truncates_long_text():
    e = entry("id1", "k", "t" * 500, description="d" * 900)
    assert len(e["title"]) == 200
    assert len(e["description"]) == 500


def test_entry_omits_empty_optionals():
    e = entry("id1", "k", "t")
    assert "tags" not in e and "url" not in e and "metadata" not in e


# -- kind markers ---------------------------------------------------------------

# The filename marker exists because frontmatter is awkward from a phone
# share-sheet, where the filename is often the only thing you get to type.


@pytest.mark.parametrize(
    "name,kind,rest",
    [
        ("r: call the accountant", "reminder", "call the accountant"),
        ("reminder - renew the domain", "reminder", "renew the domain"),
        ("t: ship the PR.md", "todo", "ship the PR"),
        ("i: what if we", "idea", "what if we"),
        ("idea - nightly rollup.md", "idea", "nightly rollup"),
        ("IDEA: shouting works", "idea", "shouting works"),
        ("  todo : leading space", "todo", "leading space"),
        ("t:no space after colon", "todo", "no space after colon"),
    ],
)
def test_filename_markers_are_parsed(name, kind, rest):
    assert kind_from_filename(name) == (kind, rest)


@pytest.mark.parametrize(
    "name",
    [
        "call-accountant.md",
        "rotate the prod key",
        "notes.txt",
        "random: thoughts",
        "ideas-are-great.md",
        "idea-nightly-rollup.md",
        "todo_fix the build",
        "reminders_list.md",
        "t-shirt sizing.md",
    ],
)
def test_unmarked_filenames_leave_the_kind_to_the_model(name):
    kind, _ = kind_from_filename(name)
    assert kind == ""


def test_filename_marker_strips_the_extension():
    assert kind_from_filename("todo: ship it.markdown")[1] == "ship it"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("reminder", "reminder"),
        ("Reminders", "reminder"),
        ("TASK", "todo"),
        ("note", "idea"),
        ("#idea", "idea"),
        ("projekt", ""),
        ("", ""),
        (None, ""),
        (7, ""),
    ],
)
def test_normalise_kind(value, expected):
    assert normalise_kind(value) == expected


def test_frontmatter_type_beats_the_filename():
    assert declared_kind({"type": "idea"}, "r: call them") == "idea"


def test_filename_is_used_when_frontmatter_is_silent():
    assert declared_kind({}, "r: call them") == "reminder"


def test_no_declaration_returns_empty():
    assert declared_kind({}, "call-accountant.md") == ""


def test_frontmatter_accepts_a_type_key():
    fm, _ = split_frontmatter("---\ntype: idea\ntitle: X\n---\nbody\n")
    assert fm["type"] == "idea"
