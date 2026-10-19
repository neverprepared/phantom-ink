"""ADR-007 log shipping for brainbox.

brainbox already emitted ADR-007-shaped records and sent them to stdout:
`log.info("router.task_completed", metadata={"task_id": ...})` carries an event
name, a level, an ISO timestamp and attributes. The only thing missing was a
destination, so adoption is ONE structlog processor rather than a call at every
site -- and every existing `log.*` call becomes eligible at once.

That breadth is why the two defaults here are conservative:

  - **Not everything ships.** WARN and above always do, because they are what
    you come looking for later. Routine INFO ships only if its event name is
    declared in ALLOW. Every shipped line is a document in a store with no
    retention policy, and brainbox logs far more than it wants to keep.
  - **Rule 4 is enforced here, not trusted.** No existing call site was ever
    audited for content or credentials, and the store answers unauthenticated.
    A banned attribute name is DROPPED rather than refused: this runs inside a
    live server's logging pipeline, so raising would turn a careless log call
    into a request failure. The drop is recorded in its place so it is visible
    rather than silent.
"""

from __future__ import annotations

import atexit
import os
import sys

# Keep in lockstep with contracts/obslog-banned-attributes.json. The list is
# duplicated in the Drive inbox's emitter by necessity -- that runs as a
# standalone `uv run --script` with no import path into this package -- so the
# POLICY lives in the contract file and a conformance test in each consumer
# fails if either copy drifts from it.
BANNED_SUBSTRINGS = (
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

SERVICE_NAME = "brainbox"

# The events whose routine INFO records ship. Deliberately small: these are the
# ones ADR-007 chunk 3 exists to make visible -- a run that completed, failed or
# was cancelled. Everything else brainbox logs stays on stdout until somebody
# declares it and accepts the volume, because each shipped line is a document in
# a store with no retention policy.
DECLARED_EVENTS = frozenset(
    {
        "router.task_completed",
        "router.task_failed",
        "router.task_cancelled",
    }
)


def enabled() -> bool:
    """True only when an OTLP log endpoint is configured.

    Default off, so a dev box, a CI run or a container with no collector neither
    tries to ship nor pays the OTLP SDK import.
    """
    return bool(
        os.environ.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    )


# structlog level -> ADR-007 severityText. ADR-007 defines three levels; a
# critical maps onto ERROR rather than inventing a fourth the reader cannot
# filter on.
_SEVERITY = {
    "debug": "DEBUG",
    "info": "INFO",
    "warn": "WARN",
    "warning": "WARN",
    "error": "ERROR",
    "critical": "ERROR",
    "exception": "ERROR",
}

# ADR-007 has the OUTCOME drive severity, and it has to win over the log level:
# router.task_failed is logged at log.info, because the level describes the log
# call and not the task. Without this a failed run ships as INFO and the severity
# filter lies about exactly the records it exists to surface. Changing brainbox's
# stdout levels instead would alter a format other readers key on.
_SEVERITY_FOR_OUTCOME = {"ok": None, "partial": "WARN", "error": "ERROR"}

# Levels that ship regardless of whether anyone declared the event. A severity
# filter that hid the errors would defeat its own purpose.
_ALWAYS_SHIP = {"WARN", "ERROR"}


def _is_banned(name: str) -> bool:
    lowered = name.lower()
    return any(banned in lowered for banned in BANNED_SUBSTRINGS)


def processor(*, send, allow):
    """Build a structlog processor that ships eligible records via `send`.

    `send` takes one ADR-007 record dict. `allow` is the set of event names
    whose INFO-level records ship; WARN and above ignore it.

    The returned processor always returns the event_dict unchanged -- stdout
    logging is what everything else reads, and a processor that swallowed or
    rewrote it would break that silently.
    """
    allow = set(allow or ())

    def _ship(_logger, _method_name, event_dict):
        try:
            severity = _SEVERITY.get(str(event_dict.get("level", "")).lower(), "INFO")
            event_name = str(event_dict.get("event", ""))
            metadata = event_dict.get("metadata") or {}
            severity = (
                _SEVERITY_FOR_OUTCOME.get(str(metadata.get("event.outcome", "")))
                or severity
            )
            if severity not in _ALWAYS_SHIP and event_name not in allow:
                return event_dict

            attributes: dict = {"event.name": event_name}
            dropped: list[str] = []
            for key, value in metadata.items():
                if _is_banned(str(key)):
                    dropped.append(str(key))
                    continue
                # Omit None rather than blanking it. task.job_id is None for a
                # root task; shipping "" makes a field that is sometimes absent
                # and sometimes empty, which is the inconsistency rule 1 is
                # about, and puts a meaningless value in the index.
                if value is None:
                    continue
                attributes[str(key)] = value
            if dropped:
                # Named, not counted: "which attribute did I lose" is the
                # question you have when a record looks thin.
                attributes["obslog.dropped_attributes"] = ",".join(sorted(dropped))

            send(
                {
                    "time": event_dict.get("timestamp"),
                    "severityText": severity,
                    # brainbox's events are dotted names rather than sentences,
                    # so body mirrors the event name the way claude-code's
                    # records do. The reader suppresses the duplicate tag.
                    "body": event_name,
                    "attributes": attributes,
                }
            )
        except Exception:
            # Telemetry is the least important thing a request does: an
            # unreachable collector must not become a 500 on an unrelated
            # endpoint. Deliberately silent -- this IS the logging pipeline, so
            # logging the failure here risks recursing through it.
            pass
        return event_dict

    return _ship


def otlp_sink():
    """An OTLP/gRPC sink for ADR-007 records, or None if unavailable.

    gRPC rather than http/protobuf because the platform's log endpoint is Data
    Prepper, which serves OTLP over gRPC only -- POST /v1/logs 404s there.

    The SDK is an optional extra (`pip install brainbox[otel]`), so a missing
    import is a configuration state rather than a crash: it returns None and
    says so ONCE on stderr. Returning a sink that failed per-record would put a
    line in the log for every line in the log.
    """
    try:
        from opentelemetry._logs import SeverityNumber
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
    except ImportError as exc:
        print(
            "obslog: OTLP log endpoint configured but the SDK is missing "
            f"({exc}); install brainbox[otel] to ship logs. Continuing on stdout only.",
            file=sys.stderr,
        )
        return None

    provider = LoggerProvider(
        resource=Resource.create({"service.name": SERVICE_NAME})
    )
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    atexit.register(provider.shutdown)
    logger = provider.get_logger(SERVICE_NAME)

    def _send(record):
        severity = record["severityText"]
        logger.emit(
            severity_text=severity,
            # A plain int is silently dropped and ships as severityNumber 0:
            # SeverityNumber is an Enum, not an IntEnum.
            severity_number=getattr(SeverityNumber, severity, SeverityNumber.INFO),
            body=record["body"],
            attributes=record["attributes"],
        )

    return _send
