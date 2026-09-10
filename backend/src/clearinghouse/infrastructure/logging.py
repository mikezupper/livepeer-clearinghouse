"""Structured JSON logging configuration."""

from __future__ import annotations

import logging

import structlog

from clearinghouse.infrastructure.telemetry import (
    TelemetryLogFilter,
    add_trace_context,
    redact_telemetry,
)


def configure_logging(level: str) -> None:
    """Configure stdlib and structlog for machine-readable event logs."""
    logging.basicConfig(format="%(message)s", level=level, force=True)
    for handler in logging.getLogger().handlers:
        handler.addFilter(TelemetryLogFilter())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            redact_telemetry,
            add_trace_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
