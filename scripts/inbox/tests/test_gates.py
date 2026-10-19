"""Stage A -- the mechanical gates. No Drive, no network.

These gates are the part of the design that does not depend on a model behaving
well, so they are tested as pure functions on Drive metadata.
"""

from __future__ import annotations

import pytest

from common import MAX_BODY_BYTES
from drive_drain import (
    Rejected,
    gate_body,
    gate_mime,
    gate_owner,
    gate_secrets,
    gate_size,
    is_furniture,
)


def meta(**over):
    base = {
        "id": "fid",
        "name": "note.md",
        "mimeType": "text/markdown",
        "size": "120",
        "owners": [{"emailAddress": "neuralcolony@gmail.com", "me": True}],
    }
    base.update(over)
    return base


# -- owner ----------------------------------------------------------------------


def test_accepts_a_file_we_own():
    gate_owner(meta())


def test_rejects_a_file_owned_by_someone_else():
    """The primary injection vector: content we never asked for."""
    with pytest.raises(Rejected) as exc:
        gate_owner(meta(owners=[{"emailAddress": "stranger@example.com", "me": False}]))
    assert exc.value.reason == "foreign-owner"
    assert "stranger@example.com" in exc.value.detail


def test_rejects_a_file_with_no_owner():
    with pytest.raises(Rejected) as exc:
        gate_owner(meta(owners=[]))
    assert exc.value.reason == "no-owner"


def test_rejects_when_owners_is_absent():
    m = meta()
    del m["owners"]
    with pytest.raises(Rejected):
        gate_owner(m)


# -- mime -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "mime",
    [
        "text/markdown",
        "text/plain",
        "text/x-markdown",
        "application/vnd.google-apps.document",
    ],
)
def test_accepts_capture_formats(mime):
    gate_mime(meta(mimeType=mime))


@pytest.mark.parametrize(
    "mime",
    [
        "application/pdf",
        "image/png",
        "application/vnd.google-apps.spreadsheet",
        "application/zip",
        "text/html",
        "",
    ],
)
def test_rejects_other_formats(mime):
    with pytest.raises(Rejected) as exc:
        gate_mime(meta(mimeType=mime))
    assert exc.value.reason == "mime"


# -- size -----------------------------------------------------------------------


def test_accepts_a_small_file():
    gate_size(meta(size=str(MAX_BODY_BYTES - 1)))


def test_rejects_an_oversized_file():
    with pytest.raises(Rejected) as exc:
        gate_size(meta(size=str(MAX_BODY_BYTES + 1)))
    assert exc.value.reason == "too-large"


def test_missing_size_passes_the_precheck():
    """Google Docs report no size; read_text enforces the cap during download."""
    m = meta()
    del m["size"]
    gate_size(m)


def test_unparseable_size_passes_the_precheck():
    gate_size(meta(size="not-a-number"))


# -- secrets --------------------------------------------------------------------


def test_accepts_ordinary_capture_text():
    gate_secrets("remind me friday to call the accountant")


def test_rejects_a_body_containing_a_token():
    with pytest.raises(Rejected) as exc:
        gate_secrets("here is the key ghp_" + "A" * 24)
    assert exc.value.reason == "secret"
    assert "github-pat" in exc.value.detail


def test_rejection_detail_never_echoes_the_secret():
    """The reason is written into a Drive note, so it must not carry the value."""
    secret = "ghp_" + "B" * 24
    with pytest.raises(Rejected) as exc:
        gate_secrets(f"token: {secret}")
    assert secret not in exc.value.detail
    assert secret not in str(exc.value)


def test_rejects_a_private_key_block():
    with pytest.raises(Rejected):
        gate_secrets("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n")


# -- body -----------------------------------------------------------------------


def test_accepts_a_body_with_content():
    gate_body("something")


@pytest.mark.parametrize("body", ["", "   ", "\n\n", "\t"])
def test_rejects_an_empty_body(body):
    with pytest.raises(Rejected) as exc:
        gate_body(body)
    assert exc.value.reason == "empty"


# -- inbox furniture ------------------------------------------------------------

# A file the author left in the inbox on purpose is not a capture. Before this
# existed, a README reminding them of the format was ingested as an idea on
# every single run.


@pytest.mark.parametrize(
    "name",
    [
        "README.md",
        "readme",
        "Readme.txt",
        "index.md",
        ".DS_Store",
        "_scratch.md",
        ".hidden.md",
    ],
)
def test_furniture_is_skipped(name):
    assert is_furniture(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "CLAUDE.md",
        "AGENTS.md",
        "GEMINI.md",
    ],
)
def test_agent_instruction_files_are_skipped(name):
    """Drive is a multi-writer surface: such a file is data, never instructions.

    Claude Code only reads CLAUDE.md from local filesystem paths, so a copy in
    Drive is inert -- but anyone a folder is shared with can create one, and it
    has no business in the vault either way.
    """
    assert is_furniture(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "call-accountant.md",
        "r: call the accountant",
        "idea - nightly rollup.md",
        "my readme for the project.md",
        "notes.txt",
    ],
)
def test_real_captures_are_not_furniture(name):
    assert is_furniture(name) is False
