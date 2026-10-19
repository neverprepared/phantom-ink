"""The emitter hands records to the real OTEL SDK, verified in-process.

No mocks: this drives the actual SDK with an in-memory exporter, so what the
test asserts is what an OTLP collector would receive. The one thing it cannot
cover is Data Prepper's flattening -- that is what the live smoke test is for.
"""

from obslog import Emitter


def _exported(emitter_factory):
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor

    exporter = InMemoryLogExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    emitter = emitter_factory(provider)
    return exporter, emitter


def test_emitted_record_reaches_the_exporter_with_severity_and_body():
    exporter, emitter = _exported(
        lambda provider: Emitter(
            service_name="phantom-inbox", workspace="personal", provider=provider
        )
    )

    emitter.emit(event_name="inbox.drain.run", body="drain: spooled 3, rejected 1")

    (record,) = exporter.get_finished_logs()
    assert record.log_record.body == "drain: spooled 3, rejected 1"
    assert record.log_record.severity_text == "INFO"


def test_severity_number_survives_the_sdk():
    """A plain int for severity_number is silently dropped and the record ships
    as severityNumber 0 (UNSPECIFIED). Caught in the live store, not here: the
    first version of these tests only asserted severity_text, so the bug passed.
    Anything reading the protocol rather than the index sees no severity at all."""
    from opentelemetry._logs import SeverityNumber

    exporter, emitter = _exported(
        lambda provider: Emitter(
            service_name="phantom-inbox", workspace="personal", provider=provider
        )
    )

    emitter.emit(event_name="inbox.drain.run", body="drain: Drive list failed", outcome="error")

    (record,) = exporter.get_finished_logs()
    assert record.log_record.severity_number == SeverityNumber.ERROR


def test_attributes_keep_their_dotted_names_and_numeric_types():
    """Data Prepper turns 'event.name' into log.attributes.event@name. The dots
    have to survive the SDK hop, and a count has to stay an int or it poisons
    the field's type for the whole daily index."""
    exporter, emitter = _exported(
        lambda provider: Emitter(
            service_name="phantom-inbox", workspace="personal", provider=provider
        )
    )

    emitter.emit(
        event_name="inbox.drain.run",
        body="drain: spooled 3, rejected 1",
        attributes={"spooled": 3},
    )

    (record,) = exporter.get_finished_logs()
    attrs = dict(record.log_record.attributes)
    assert attrs["event.name"] == "inbox.drain.run"
    assert attrs["spooled"] == 3
    assert not isinstance(attrs["spooled"], str)


def test_emit_never_raises_when_the_collector_is_unreachable():
    """The inbox's governing rule is that one failure must not stall the queue.
    Telemetry is the least important thing the pipeline does, so a dead
    collector must never be the reason an item goes unfiled."""

    class ExplodingProvider:
        def get_logger(self, *_a, **_kw):
            raise OSError("collector unreachable")

    emitter = Emitter(
        service_name="phantom-inbox", workspace="personal", provider=ExplodingProvider()
    )

    emitter.emit(event_name="inbox.drain.run", body="drain: spooled 3")


def test_emit_run_builds_a_sentence_from_the_counts():
    """Rule 3: the record has to answer 'what happened' on its own, and rule 5
    wants the counts in one record rather than one record each. Zero-valued
    counts are left out -- 'skipped 0, errors 0' is noise in a one-line body."""
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor

    import obslog

    exporter = InMemoryLogExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))

    obslog.emit_run(
        workspace="personal",
        event_name="inbox.drain.run",
        stage="drain",
        counts={"spooled": 3, "rejected": 1, "already filed": 0, "errors": 0},
        duration_ms=38,
        provider=provider,
    )

    (record,) = exporter.get_finished_logs()
    assert record.log_record.body == "drain: spooled 3, rejected 1"
    assert record.log_record.severity_text == "WARN"


def test_emit_run_says_idle_rather_than_nothing():
    """An empty inbox still emits, or you cannot tell a quiet pipeline from a
    dead one."""
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor

    import obslog

    exporter = InMemoryLogExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))

    obslog.emit_run(
        workspace="personal",
        event_name="inbox.drain.run",
        stage="drain",
        counts={"spooled": 0, "rejected": 0, "errors": 0},
        duration_ms=4,
        provider=provider,
    )

    (record,) = exporter.get_finished_logs()
    assert record.log_record.body == "drain: idle"
    assert record.log_record.severity_text == "INFO"


def test_emit_run_normalises_count_names_into_attributes():
    """'already filed' has a space in it. A space in an attribute name is a
    field you cannot address in a query, so it becomes already_filed."""
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor

    import obslog

    exporter = InMemoryLogExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))

    obslog.emit_run(
        workspace="personal",
        event_name="inbox.drain.run",
        stage="drain",
        counts={"spooled": 1, "already filed": 2},
        duration_ms=4,
        provider=provider,
    )

    (record,) = exporter.get_finished_logs()
    attrs = dict(record.log_record.attributes)
    assert attrs["already_filed"] == 2
    assert "already filed" not in attrs
