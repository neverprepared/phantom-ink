"""Structured JSON logging via structlog."""

from __future__ import annotations

import sys
from typing import Any

import structlog

from . import obslog


def _rename_warning_to_warn(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Rename 'warning' level to 'warn' for Node.js log compatibility."""
    if event_dict.get("level") == "warning":
        event_dict["level"] = "warn"
    return event_dict


def setup_logging(*, ship=None) -> None:
    """Configure structlog with JSON output matching the Node.js format.

    When an OTLP log endpoint is configured (or `ship` is supplied), an ADR-007
    shipper is inserted before the renderer so brainbox's existing structured
    events also reach the log store. It ships WARN+ and the declared task
    lifecycle only -- see obslog -- and returns the event_dict untouched, so
    stdout logging is unaffected either way.

    `ship` exists for tests: the OTLP SDK is an optional extra, so this has to
    be exercisable without it.
    """
    if ship is None and obslog.enabled():
        ship = obslog.otlp_sink()

    shipper = (
        [obslog.processor(send=ship, allow=obslog.DECLARED_EVENTS)] if ship else []
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _rename_warning_to_warn,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Before the renderer: the shipper reads the event_dict, and after
            # JSONRenderer it would be a string.
            *shipper,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(
    session_name: str | None = None,
    container_name: str | None = None,
) -> structlog.stdlib.BoundLogger:
    """Return a logger optionally bound with session context."""
    logger = structlog.get_logger()
    if session_name:
        logger = logger.bind(session_name=session_name)
    if container_name:
        logger = logger.bind(container_name=container_name)
    return logger
