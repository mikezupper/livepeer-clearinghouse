"""Structured logging configuration test."""

import json
import logging

import structlog

from clearinghouse.infrastructure.logging import configure_logging


def test_configured_logger_emits_json(capfd: object) -> None:
    configure_logging("INFO")
    structlog.get_logger().info("test_event", answer=42)
    captured = capfd.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.err)
    assert payload["event"] == "test_event"
    assert payload["answer"] == 42
    assert payload["level"] == "info"
    logging.shutdown()
