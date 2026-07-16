#!/usr/bin/env python3
"""Export the timeline-entry JSON Schema from the canonical `AgentEnvelope` model.

This is the ONE generator for the phantom-contracts schema. `AgentEnvelope`
(brainbox.agent_store) is the single source of truth for the timeline-entry
contract (DECISIONS.md D1); the schema file in phantom-contracts is generated
from it and never hand-edited (D2 invariant).

Usage:
    # Print the schema to stdout (used to diff / verify no drift):
    python scripts/export_contract.py

    # Write the schema into a phantom-contracts checkout:
    python scripts/export_contract.py --out /path/to/phantom-contracts

    # Also emit the FastAPI OpenAPI snapshot (best-effort; skipped if the app
    # can't be imported without a live environment):
    python scripts/export_contract.py --out /path/to/phantom-contracts --openapi

The output is deterministic (sorted keys, trailing newline) so a regenerate
produces a byte-identical file when the model is unchanged — that is what the
compat gate and the "no diff" acceptance check rely on.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# The $id is owned by the model contract, not by this script. Keep it in lockstep
# with the model version; bumping major ($id v2 -> v3) is a governed, parallel-run
# event (see phantom-contracts/evolution-policy.md).
SCHEMA_ID = "phantom-ink:timeline-entry/v2.1"
SCHEMA_DIALECT = "http://json-schema.org/draft-07/schema#"
DO_NOT_EDIT = (
    "DO NOT EDIT — generated from phantom-ink AgentEnvelope "
    "(brainbox/src/brainbox/agent_store.py) by brainbox/scripts/export_contract.py. "
    "Edit the model and regenerate; never hand-edit this file."
)


def build_schema() -> dict[str, Any]:
    """Return the timeline-entry schema derived from the canonical model."""
    from brainbox.agent_store import AgentEnvelope

    schema = AgentEnvelope.model_json_schema()

    # Stamp the contract framing. Insertion order here is cosmetic — the file is
    # dumped with sort_keys=True — but naming the identity fields up front keeps
    # the intent explicit for anyone reading build_schema.
    framed: dict[str, Any] = {
        "$schema": SCHEMA_DIALECT,
        "$id": SCHEMA_ID,
        "$comment": DO_NOT_EDIT,
    }
    framed.update(schema)
    return framed


def dump(obj: dict[str, Any]) -> str:
    """Deterministic JSON: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_openapi() -> dict[str, Any] | None:
    """Best-effort FastAPI OpenAPI snapshot. Returns None if the app can't be
    imported in this environment (the snapshot is an optional convenience)."""
    try:
        from brainbox.api import app  # local import: app pulls heavy deps
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[export_contract] openapi snapshot skipped: {exc}", file=sys.stderr)
        return None
    return app.openapi()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="phantom-contracts checkout dir; writes schema/timeline-entry.schema.json",
    )
    ap.add_argument(
        "--openapi",
        action="store_true",
        help="also emit schema/openapi-events.json (best-effort)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="with --out: exit 1 if the on-disk schema differs from freshly generated",
    )
    ap.add_argument(
        "--print-version",
        action="store_true",
        help="print the version segment of the schema $id (e.g. 'v2.1') and exit",
    )
    args = ap.parse_args()

    if args.print_version:
        print(SCHEMA_ID.rsplit("/", 1)[-1])
        return 0

    schema_text = dump(build_schema())

    if args.out is None:
        sys.stdout.write(schema_text)
        return 0

    schema_dir = args.out / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    schema_path = schema_dir / "timeline-entry.schema.json"

    if args.check:
        current = schema_path.read_text() if schema_path.exists() else ""
        if current != schema_text:
            print(
                f"[export_contract] DRIFT: {schema_path} differs from the model. "
                f"Run: python scripts/export_contract.py --out {args.out}",
                file=sys.stderr,
            )
            return 1
        print(f"[export_contract] {schema_path} is up to date with the model.")
        return 0

    schema_path.write_text(schema_text)
    print(f"[export_contract] wrote {schema_path}")

    if args.openapi:
        openapi = build_openapi()
        if openapi is not None:
            openapi_path = schema_dir / "openapi-events.json"
            openapi_path.write_text(dump(openapi))
            print(f"[export_contract] wrote {openapi_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
