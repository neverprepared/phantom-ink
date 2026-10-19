"""ADR-007 log records for the Drive inbox."""

import atexit
import secrets
import sys
import threading
from datetime import UTC, datetime

# An outcome without a matching severity makes the severity filter a lie, so the
# mapping lives here rather than at each call site.
_SEVERITY_FOR_OUTCOME = {"ok": "INFO", "partial": "WARN", "error": "ERROR"}

# Crockford base32: no I, L, O or U, so a run id read off a dashboard and typed
# back into a query cannot be transcribed wrong.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_ULID_LOCK = threading.Lock()
_LAST = {"ms": -1, "rand": 0}


def _encode(value, length):
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_run_id():
    """A monotonic ULID: 48-bit millisecond timestamp, 80 bits of randomness.

    Monotonic matters. Two runs starting in the same millisecond would otherwise
    sort by their random halves, and a run id that does not sort is a run id you
    have to parse a timestamp out of to order.
    """
    ms = int(datetime.now(UTC).timestamp() * 1000)
    with _ULID_LOCK:
        if ms == _LAST["ms"]:
            _LAST["rand"] += 1
        elif ms < _LAST["ms"]:
            # Clock went backwards (NTP step). Keep issuing ids that sort after
            # the ones already emitted rather than handing out a duplicate.
            ms = _LAST["ms"]
            _LAST["rand"] += 1
        else:
            _LAST["ms"] = ms
            _LAST["rand"] = secrets.randbits(80)
        rand = _LAST["rand"]
    return _encode(ms, 10) + _encode(rand, 16)


# ADR-007 rule 4: content and credentials never enter this store, and the reader
# drops identity attributes anyway. Matched as substrings so a near-miss name
# (note_content, access_token, user.email) is caught rather than squeaking past
# an exact-match list.
_BANNED_SUBSTRINGS = (
    "content",
    "prompt",
    "response",
    "transcript",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "user.",
    "user@",
    "organization.",
    "organization@",
)


def _refuse_if_banned(name):
    lowered = name.lower()
    for banned in _BANNED_SUBSTRINGS:
        if banned in lowered:
            raise ValueError(
                f"ADR-007 rule 4: refusing to log attribute {name!r} "
                f"(matches {banned!r}). Content and credentials belong in a "
                f"store with an access model; correlate by id instead."
            )


# The severity NUMBER must be a SeverityNumber member, not the equivalent int.
# SeverityNumber is a plain Enum, not an IntEnum, so a bare 17 is not equal to
# SeverityNumber.ERROR: the SDK drops it and the record ships as severityNumber
# 0 (UNSPECIFIED) while severityText still reads ERROR. Verified in the live
# store -- the index looked right and the protocol field was empty.
def _severity_number(severity):
    from opentelemetry._logs import SeverityNumber

    return getattr(SeverityNumber, severity)


# A stage's counts already say how the run went; deriving the outcome from them
# keeps the two from disagreeing, which is what happens when each call site
# decides for itself.
_ERROR_COUNTS = ("errors",)
_PARTIAL_COUNTS = ("rejected", "quarantined", "retrying")


def outcome_for(counts):
    """ADR-007 event.outcome from a stage's counts dict.

    An empty inbox is the normal state, so an idle run is ok, not partial: a
    convention that cries partial every quiet hour trains you to ignore it.
    """
    if any(counts.get(key) for key in _ERROR_COUNTS):
        return "error"
    if any(counts.get(key) for key in _PARTIAL_COUNTS):
        return "partial"
    return "ok"


class Emitter:
    """Ships ADR-007 records for one service, correlated by a single run id.

    One Emitter is one run. It mints the run id at construction, so every record
    it emits is already tied together without the caller threading an id
    through.
    """

    def __init__(self, *, service_name, workspace, provider=None, run_id=None):
        self._service_name = service_name
        self._workspace = workspace
        self._provider = provider
        self.run_id = run_id or new_run_id()

    def emit(
        self,
        *,
        event_name,
        body,
        outcome="ok",
        severity=None,
        duration_ms=None,
        attributes=None,
    ):
        record = build_record(
            service_name=self._service_name,
            workspace=self._workspace,
            event_name=event_name,
            body=body,
            outcome=outcome,
            severity=severity,
            duration_ms=duration_ms,
            run_id=self.run_id,
            attributes=attributes,
        )
        try:
            self._send(record)
        except Exception as exc:
            # Telemetry is the least important thing this pipeline does. A dead
            # collector, a missing dependency or a protocol change must never be
            # the reason an item goes unfiled, so this swallows everything and
            # says so on stderr. stdout belongs to the collect-job array.
            print(f"obslog: dropped {event_name} record: {exc}", file=sys.stderr)

    def _send(self, record):
        severity = record["severityText"]
        logger = self._provider_or_default().get_logger(self._service_name)
        timestamp_ns = int(datetime.fromisoformat(record["time"]).timestamp() * 1_000_000_000)
        logger.emit(
            timestamp=timestamp_ns,
            observed_timestamp=timestamp_ns,
            severity_text=severity,
            severity_number=_severity_number(severity),
            body=record["body"],
            attributes=record["attributes"],
        )

    def _provider_or_default(self):
        if self._provider is None:
            self._provider = _default_provider(self._service_name, self._workspace)
        return self._provider


SERVICE_NAME = "phantom-inbox"


def emit_run(
    *,
    workspace,
    event_name,
    stage,
    counts,
    duration_ms,
    attributes=None,
    provider=None,
):
    """One ADR-007 record for a completed stage run.

    This is the whole adoption surface for a stage: it derives the outcome and
    the severity from the counts, so a stage cannot report a rejection and an
    'ok' in the same breath, and folds the counts into one record per rule 5.
    """
    detail = ", ".join(f"{name} {value}" for name, value in counts.items() if value)
    emitter = Emitter(service_name=SERVICE_NAME, workspace=workspace, provider=provider)
    emitter.emit(
        event_name=event_name,
        body=f"{stage}: {detail or 'idle'}",
        outcome=outcome_for(counts),
        duration_ms=duration_ms,
        attributes={
            # A space in an attribute name is a field you cannot address in a
            # query, and the stages' count keys contain spaces.
            **{name.replace(" ", "_"): value for name, value in counts.items()},
            **(attributes or {}),
        },
    )


def _default_provider(service_name, workspace):
    """An OTLP/gRPC provider aimed at whatever OTEL_EXPORTER_OTLP_* says.

    gRPC rather than http/protobuf because the platform's log endpoint is
    Data Prepper, which serves OTLP over gRPC only -- POST /v1/logs 404s there.
    """
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    provider = LoggerProvider(
        resource=Resource.create({"service.name": service_name, "workspace": workspace})
    )
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    atexit.register(provider.shutdown)
    return provider


def build_record(
    *,
    service_name,
    workspace,
    event_name,
    body,
    outcome="ok",
    severity=None,
    duration_ms=None,
    run_id=None,
    attributes=None,
):
    attrs = {"event.name": event_name, "event.outcome": outcome}
    if run_id is not None:
        attrs["run.id"] = run_id
    if duration_ms is not None:
        attrs["duration_ms"] = duration_ms
    for name, value in (attributes or {}).items():
        _refuse_if_banned(name)
        attrs[name] = value
    return {
        "time": datetime.now(UTC).isoformat(),
        "severityText": severity or _SEVERITY_FOR_OUTCOME[outcome],
        "body": body,
        "attributes": attrs,
        "resource": {"service.name": service_name, "workspace": workspace},
    }
