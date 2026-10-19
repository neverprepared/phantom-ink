"""Tests for ADR-007 log shipping from brainbox's structlog pipeline.

brainbox already emitted ADR-007-shaped records -- an event name, a level, an
ISO timestamp and a metadata dict -- to stdout and nowhere else. These cover the
processor that ships them to the log store, and the two defaults that keep a
blanket shipper from being a liability:

  - it ships WARN+ and an explicit allowlist of declared event names, not every
    log line, because every line would be a document in a store with no
    retention policy;
  - it refuses rule-4 attribute names, because no existing call site was ever
    audited for content or credentials and the store answers unauthenticated.
"""

from __future__ import annotations

import pytest
import structlog

from brainbox import obslog


@pytest.fixture(autouse=True)
def _restore_structlog_config():
    """Undo global structlog configuration after every test in this module.

    setup_logging() calls structlog.configure(), which is PROCESS-global. Two
    tests here exercise it, and without this they leave a shipper installed --
    one whose sink is a list owned by a test that has already finished -- for
    every later test in the session.

    No observed failure is attributed to this; it is prevention. (It was
    briefly blamed for a failure in tests/test_conversation_orchestrator.py,
    wrongly: pytest collects that file at position ~152 and this one at ~1027,
    so the leak happens strictly after. That failure is a pre-existing racy
    assertion on the completion order of concurrent replies, and it reproduces
    on main.) Keep this anyway -- leaking global logging config out of a test
    module is a trap whether or not it has sprung yet.
    """
    saved = structlog.get_config().copy()
    yield
    structlog.configure(**saved)


def _record(event: str, level: str = "info", **metadata):
    """A structlog event_dict as log.py's processors produce it."""
    ev = {"event": event, "level": level, "timestamp": "2026-10-19T22:31:13.790Z"}
    if metadata:
        ev["metadata"] = metadata
    return ev


def test_allowlisted_event_is_shipped():
    sent = []
    ship = obslog.processor(send=sent.append, allow={"router.task_completed"})

    ship(None, "info", _record("router.task_completed", task_id="t-1"))

    assert len(sent) == 1
    assert sent[0]["attributes"]["event.name"] == "router.task_completed"
    assert sent[0]["attributes"]["task_id"] == "t-1"


def test_undeclared_info_event_is_not_shipped():
    """The default is NOT everything. brainbox logs far more than it wants in a
    store with no retention policy, so an INFO line nobody declared stays on
    stdout."""
    sent = []
    ship = obslog.processor(send=sent.append, allow={"router.task_completed"})

    ship(None, "info", _record("router.recycle_failed", task_id="t-1"))

    assert sent == []


def test_warnings_and_errors_ship_without_being_declared():
    """The allowlist governs routine chatter. A warning or an error is the thing
    you come looking for later, so it ships whether or not anyone declared it --
    otherwise the filter hides exactly the records it exists to surface."""
    sent = []
    ship = obslog.processor(send=sent.append, allow=set())

    ship(None, "warning", _record("router.recycle_failed", level="warn"))
    ship(None, "error", _record("router.something_broke", level="error"))

    assert [r["severityText"] for r in sent] == ["WARN", "ERROR"]


def test_the_structlog_record_passes_through_unchanged():
    """A processor that swallowed or rewrote the event_dict would silently break
    stdout logging, which is what everything else reads."""
    sent = []
    ship = obslog.processor(send=sent.append, allow={"router.task_completed"})

    original = _record("router.task_completed", task_id="t-1")
    returned = ship(None, "info", original)

    assert returned is original


def test_a_banned_attribute_is_dropped_and_the_record_still_ships():
    """Rule 4, but NOT by raising: this runs inside the logging pipeline of a
    live server, so refusing loudly here would turn a careless log call into a
    request failure. The offending attribute is dropped, the record still ships,
    and the drop is visible in its place."""
    sent = []
    ship = obslog.processor(send=sent.append, allow={"router.task_completed"})

    ship(
        None,
        "info",
        _record("router.task_completed", task_id="t-1", auth_token="sk-live-abc"),
    )

    assert len(sent) == 1
    attrs = sent[0]["attributes"]
    assert "auth_token" not in attrs
    assert attrs["task_id"] == "t-1"
    assert attrs["obslog.dropped_attributes"] == "auth_token"


def test_a_failing_sink_never_breaks_the_caller():
    """Telemetry is the least important thing a request does. An unreachable
    collector must not turn into a 500 on an unrelated endpoint."""
    def explode(_record):
        raise OSError("collector unreachable")

    ship = obslog.processor(send=explode, allow={"router.task_completed"})

    returned = ship(None, "info", _record("router.task_completed", task_id="t-1"))

    assert returned["event"] == "router.task_completed"


def test_banned_list_matches_the_contract():
    """The rule-4 policy is duplicated in the Drive inbox's emitter out of
    necessity -- that runs as a standalone `uv run --script` with no import path
    into this package. Duplicating a SECURITY guard is how one copy quietly
    stops matching, so the policy lives in one contract file and this fails if
    either copy drifts from it."""
    import json
    from pathlib import Path

    contract = (
        Path(__file__).resolve().parents[2] / "contracts" / "obslog-banned-attributes.json"
    )
    declared = json.loads(contract.read_text())["banned_substrings"]

    assert list(obslog.BANNED_SUBSTRINGS) == declared


def test_shipping_is_off_unless_an_endpoint_is_configured(monkeypatch):
    """Default off. A dev box, a CI run and a container with no collector must
    not try to ship, and must not pay the import cost of the OTLP SDK."""
    for var in ("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)

    assert obslog.enabled() is False


def test_shipping_turns_on_with_a_logs_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "https://otel-logs.example.com")

    assert obslog.enabled() is True


def test_the_task_lifecycle_is_declared():
    """These are the events ADR-007 chunk 3 exists to make visible: a run that
    completed, failed or was cancelled. Anything else stays on stdout until
    someone declares it and accepts the volume."""
    for event in (
        "router.task_completed",
        "router.task_failed",
        "router.task_cancelled",
    ):
        assert event in obslog.DECLARED_EVENTS, event


def test_setup_logging_installs_no_shipper_when_disabled(monkeypatch):
    import structlog

    from brainbox import log as log_module

    for var in ("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)

    log_module.setup_logging()

    names = [getattr(p, "__name__", type(p).__name__) for p in structlog.get_config()["processors"]]
    assert not any("ship" in n for n in names), names


def test_setup_logging_installs_the_shipper_when_given_a_sink():
    """The sink is injectable so this is testable without the OTLP SDK, which is
    an optional extra: brainbox must not pull grpcio into every install for a
    feature that is off by default."""
    import structlog

    from brainbox import log as log_module

    sent = []
    log_module.setup_logging(ship=sent.append)

    structlog.get_logger().info("router.task_completed", metadata={"task_id": "t-9"})

    assert len(sent) == 1
    assert sent[0]["attributes"]["event.name"] == "router.task_completed"
    assert sent[0]["attributes"]["task_id"] == "t-9"


def test_outcome_overrides_the_log_level_for_severity():
    """router.task_failed is logged at log.INFO — the level describes the log
    call, not the task. Without this a failed run ships as INFO and the severity
    filter lies about precisely the records it exists to surface. ADR-007 has
    outcome drive severity, and the inbox emitter already does; this keeps the
    two consumers consistent without changing brainbox's stdout levels, which
    other readers key on."""
    sent = []
    ship = obslog.processor(send=sent.append, allow={"router.task_failed"})

    ship(None, "info", _record("router.task_failed", level="info", **{"event.outcome": "error"}))

    assert sent[0]["severityText"] == "ERROR"


def test_a_cancelled_run_warns_rather_than_erroring():
    """Cancelled is a deliberate stop: not a success, not a failure. ADR-007's
    third outcome exists for exactly this, and paging on it would train you to
    ignore the level."""
    sent = []
    ship = obslog.processor(send=sent.append, allow={"router.task_cancelled"})

    ship(
        None,
        "info",
        _record("router.task_cancelled", level="info", **{"event.outcome": "partial"}),
    )

    assert sent[0]["severityText"] == "WARN"


def test_an_outcome_upgraded_record_ships_even_if_undeclared():
    """A failure nobody declared is still a failure."""
    sent = []
    ship = obslog.processor(send=sent.append, allow=set())

    ship(None, "info", _record("router.something_new", level="info", **{"event.outcome": "error"}))

    assert len(sent) == 1


def test_none_valued_attributes_are_omitted_not_blanked():
    """task.job_id is None for a root task. Shipping it as "" makes a field that
    is sometimes absent and sometimes empty — the same inconsistency rule 1 is
    about, and it puts a meaningless value in the index. Seen in the live store
    as log.attributes.job@id: ''."""
    sent = []
    ship = obslog.processor(send=sent.append, allow={"router.task_completed"})

    ship(
        None,
        "info",
        _record("router.task_completed", **{"task.id": "t-1", "job.id": None}),
    )

    attrs = sent[0]["attributes"]
    assert attrs["task.id"] == "t-1"
    assert "job.id" not in attrs
