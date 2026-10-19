"""ADR-007 conformance tests for the log emitter.

These test the RECORD the emitter builds, not the transport. The convention is
what has to hold: a record that indexes fine but breaks rule 1 or 2 is worse
than no record, because it silently poisons the field's type for the whole
daily index.
"""

import re

from obslog import build_record, new_run_id, outcome_for


def test_severity_is_always_set():
    """ADR-007 rule 2: an unset level is indistinguishable from INFO and makes
    the whole corpus unfilterable. The caller did not ask for a severity here."""
    rec = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: spooled 3, rejected 1",
    )

    assert rec["severityText"] == "INFO"


def test_duration_stays_a_json_number():
    """ADR-007 rule 1. claude-code's own records ship duration_ms as a string on
    some events and a number on others, which makes the field unaggregatable for
    the whole daily index. A reader that only handles the numeric form sees 0,
    and 0 renders as absent rather than as wrong."""
    rec = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: spooled 3, rejected 1",
        duration_ms=38,
    )

    assert rec["attributes"]["duration_ms"] == 38
    assert not isinstance(rec["attributes"]["duration_ms"], str)


def test_service_and_workspace_are_resource_attributes():
    """These are what separate our records from claude-code's, and TailLogs
    filters on workspace. They belong on the RESOURCE, not the record, so every
    record of a service carries them without the caller repeating them."""
    rec = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: spooled 3, rejected 1",
    )

    assert rec["resource"]["service.name"] == "phantom-inbox"
    assert rec["resource"]["workspace"] == "personal"


def test_event_name_is_a_dotted_attribute():
    """Data Prepper flattens attribute names, replacing '.' with '@', so
    'event.name' is what makes the queryable field
    log.attributes.event@name.keyword. Emitting 'event_name' instead would land
    as a field the reader does not look at."""
    rec = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: spooled 3, rejected 1",
    )

    assert rec["attributes"]["event.name"] == "inbox.drain.run"


def test_body_is_one_readable_sentence():
    """ADR-007: body must be readable with no tooling, and it has no .keyword
    subfield, which is why event.name exists separately rather than being parsed
    back out of the body."""
    rec = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: spooled 3, rejected 1",
    )

    assert rec["body"] == "drain: spooled 3, rejected 1"


def test_error_outcome_raises_severity():
    """event.outcome is the one thing a scanner reads first, and severity is the
    filter. If a run that failed still says INFO, the filter is a lie and the
    error is as buried as it was before the convention."""
    rec = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: Drive list failed",
        outcome="error",
    )

    assert rec["attributes"]["event.outcome"] == "error"
    assert rec["severityText"] == "ERROR"


def test_partial_outcome_warns():
    """A run that filed 2 of 3 is not a success and not a failure. ADR-007 gives
    it its own outcome; it needs its own severity or it hides among the INFOs."""
    rec = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: spooled 2, rejected 1",
        outcome="partial",
    )

    assert rec["severityText"] == "WARN"


def test_run_id_ties_a_runs_records_together():
    """run.id is this store's log stream: it is what lets you pull every record
    of one drain out of a day holding thousands."""
    run_id = new_run_id()

    start = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: started",
        run_id=run_id,
    )
    finish = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: spooled 3, rejected 1",
        run_id=run_id,
    )

    assert start["attributes"]["run.id"] == finish["attributes"]["run.id"] == run_id


def test_run_ids_are_distinct_and_time_sortable():
    """ADR-007 asks for a ULID: distinct per run, and lexically sortable so the
    newest run sorts last without parsing a timestamp."""
    first = new_run_id()
    second = new_run_id()

    assert first != second
    assert first < second
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", first), first


def test_record_carries_an_iso8601_time():
    rec = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: spooled 3, rejected 1",
    )

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+\+00:00", rec["time"]), rec["time"]


def test_counts_and_correlation_ids_are_allowed_attributes():
    """Rule 5 wants one record saying 'spooled 3, rejected 1' rather than four
    records, so the counts have to ride along as numbers. Correlation uses the
    ids the other systems already have."""
    rec = build_record(
        service_name="phantom-inbox",
        workspace="personal",
        event_name="inbox.drain.run",
        body="drain: spooled 3, rejected 1",
        attributes={"spooled": 3, "rejected": 1, "session.id": "abc123"},
    )

    assert rec["attributes"]["spooled"] == 3
    assert rec["attributes"]["session.id"] == "abc123"


def test_content_attribute_is_refused():
    """Rule 4. This store has no access model, no severity and no retention
    policy; message bodies and file text do not belong in it. Refusing loudly
    beats stripping silently, which would leave the caller believing it logged."""
    try:
        build_record(
            service_name="phantom-inbox",
            workspace="personal",
            event_name="inbox.apply.run",
            body="apply: filed 1",
            attributes={"note_content": "the actual text of the captured note"},
        )
    except ValueError as exc:
        assert "note_content" in str(exc)
    else:
        raise AssertionError("expected build_record to refuse a content attribute")


def test_identity_attribute_is_refused():
    """Rule 4 also excludes identity. The reader drops user@* and
    organization@*, so emitting them ships PII to a store nobody reads it from."""
    try:
        build_record(
            service_name="phantom-inbox",
            workspace="personal",
            event_name="inbox.apply.run",
            body="apply: filed 1",
            attributes={"user.email": "someone@example.com"},
        )
    except ValueError as exc:
        assert "user.email" in str(exc)
    else:
        raise AssertionError("expected build_record to refuse an identity attribute")


def test_a_clean_run_is_ok():
    assert outcome_for({"spooled": 3, "rejected": 0, "errors": 0}) == "ok"


def test_a_run_that_refused_an_item_is_partial():
    """A drain that spooled 2 and rejected 1 is neither a success nor a failure.
    Calling it ok hides the rejection; calling it an error pages someone over a
    pipeline working exactly as designed."""
    assert outcome_for({"spooled": 2, "rejected": 1, "errors": 0}) == "partial"


def test_a_run_that_hit_an_error_is_an_error():
    assert outcome_for({"spooled": 0, "rejected": 0, "errors": 1}) == "error"


def test_an_idle_run_is_ok():
    """An empty inbox is the normal state, not a problem. ADR-007 wants the run
    visible either way, so this still emits -- as ok."""
    assert outcome_for({"spooled": 0, "rejected": 0, "errors": 0}) == "ok"
