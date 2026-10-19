"""The emitted entries must satisfy the timeline-entry contract.

`runCollectCommand` (app/collect.go:793) only requires id/kind/title and drops
anything else, so an invented status is accepted at the boundary and only
breaks downstream where the attention system reads it. These tests read the
IN-REPO schema -- the same one the Go codegen is generated from -- so the
mapping cannot drift when the contract tag is bumped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common import (
    ENTRY_STATUSES,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_NEEDS_ACTION,
    STATUS_UPCOMING,
    entry,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA = REPO_ROOT / "app" / "internal" / "contract" / "timeline-entry.schema.json"


@pytest.fixture(scope="module")
def schema() -> dict:
    if not SCHEMA.is_file():
        pytest.skip(f"contract schema not present at {SCHEMA}")
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def allowed_statuses(schema) -> set[str]:
    return set(schema["$defs"]["EnvelopeStatus"]["enum"])


def test_every_status_constant_is_in_the_contract(allowed_statuses):
    """The bug this guards: 'pending' and 'error' are not envelope statuses."""
    assert ENTRY_STATUSES <= allowed_statuses, ENTRY_STATUSES - allowed_statuses


def test_we_use_the_whole_contract_vocabulary_deliberately(allowed_statuses):
    """Catches a contract that gains or loses a status without us noticing."""
    assert ENTRY_STATUSES == allowed_statuses


@pytest.mark.parametrize(
    "status",
    [STATUS_UPCOMING, STATUS_DONE, STATUS_FAILED, STATUS_BLOCKED, STATUS_NEEDS_ACTION],
)
def test_each_pipeline_status_is_valid(status, allowed_statuses):
    assert status in allowed_statuses


def test_entry_default_status_is_valid(allowed_statuses):
    assert entry("id", "kind", "title")["status"] in allowed_statuses


def test_no_source_file_emits_a_literal_status():
    """Statuses must come from the constants, never a bare string.

    A bare literal is exactly how 'pending' and 'error' got in: they read fine
    and nothing at the boundary rejects them.
    """
    inbox = Path(__file__).resolve().parent.parent
    offenders = []
    for path in inbox.glob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if 'status="' in stripped or "status='" in stripped:
                offenders.append(f"{path.name}:{n}: {stripped}")
    assert not offenders, "use the STATUS_* constants:\n" + "\n".join(offenders)


def test_required_entry_fields_match_the_schema(schema):
    """runCollectCommand drops entries missing id/kind/title."""
    props = set(schema.get("properties", {}))
    e = entry("id1", "inbox_upcoming", "a title", description="d", tags=["x"], url="u")
    unknown = set(e) - props
    assert not unknown, f"entry emits fields the contract does not define: {unknown}"
