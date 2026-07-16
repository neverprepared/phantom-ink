"""The contract export must stay faithful to the canonical model.

Guards the generated-only invariant (DECISIONS.md D2): the phantom-contracts
schema is produced from AgentEnvelope by scripts/export_contract.py, and that
generation is deterministic and identity-stamped. If someone edits the model,
these still pass (they read the model); if someone breaks the exporter, they fail.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_contract.py"
_spec = importlib.util.spec_from_file_location("export_contract", _SCRIPT)
export_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_contract)


def test_schema_has_contract_identity():
    schema = export_contract.build_schema()
    assert schema["$id"] == "phantom-ink:timeline-entry/v2.1"
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert "DO NOT EDIT" in schema["$comment"]


def test_schema_reflects_the_model():
    # The schema is derived from AgentEnvelope, so its properties must match the
    # model's fields exactly — proves generated == model, not a hand copy.
    from brainbox.agent_store import AgentEnvelope

    schema = export_contract.build_schema()
    assert set(schema["properties"]) == set(AgentEnvelope.model_fields)
    # Required set matches the model's required (non-defaulted) fields.
    required_fields = {
        name
        for name, f in AgentEnvelope.model_fields.items()
        if f.is_required()
    }
    assert set(schema["required"]) == required_fields


def test_export_is_deterministic():
    a = export_contract.dump(export_contract.build_schema())
    b = export_contract.dump(export_contract.build_schema())
    assert a == b
    assert a.endswith("\n")
    # Round-trips as valid JSON.
    json.loads(a)


def test_status_enum_is_the_envelope_status():
    from brainbox.agent_store import EnvelopeStatus

    schema = export_contract.build_schema()
    enum = schema["$defs"]["EnvelopeStatus"]["enum"]
    assert set(enum) == {s.value for s in EnvelopeStatus}
