"""Adversarial tests for bounded OpenTelemetry boundaries."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from clearinghouse.infrastructure.telemetry import (
    Component,
    Outcome,
    Reason,
    Telemetry,
    TelemetryConfig,
    TelemetryLogFilter,
    TelemetryMiddleware,
    _auth_route,
    _http_outcome,
    _method,
    _request_id,
    _route_template,
    _status_family,
    add_trace_context,
    get_telemetry,
    instrument_database_engine,
    redact_telemetry,
    safe_outcome,
    safe_reason,
    set_telemetry,
)


@pytest.fixture
def observed() -> Iterator[tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter]]:
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    tracer_provider = TracerProvider()
    spans = InMemorySpanExporter()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    telemetry = Telemetry(
        tracer=tracer_provider.get_tracer("test"),
        meter=meter_provider.get_meter("test"),
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )
    set_telemetry(telemetry)
    yield telemetry, reader, spans
    set_telemetry(Telemetry())
    meter_provider.shutdown()
    tracer_provider.shutdown()


def _points(reader: InMemoryMetricReader, name: str) -> list[Any]:
    data = reader.get_metrics_data()
    assert data is not None
    return [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def test_http_boundary_uses_template_and_closed_dimensions(
    observed: tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter],
) -> None:
    telemetry, reader, spans = observed
    app = FastAPI()

    @app.get("/v1/accounts/{account_id}")
    async def account(account_id: str) -> dict[str, str]:
        structlog.get_logger().info("account_read")
        return {"account_id": account_id}

    telemetry.instrument_fastapi(app)
    secret_id = "acct_secret-person@example.com"  # noqa: S105 -- redaction fixture
    with TestClient(app) as client:
        response = client.get(
            f"/v1/accounts/{secret_id}?token=och_live_should-never-appear",
            headers={"X-Request-ID": "request_12345678"},
        )
    assert response.status_code == 200

    points = _points(reader, "clearinghouse.http.requests")
    assert len(points) == 1
    assert dict(points[0].attributes) == {
        "route": "/v1/accounts/{account_id}",
        "method": "GET",
        "status_family": "2xx",
    }
    span = spans.get_finished_spans()[0]
    assert span.attributes is not None
    assert span.attributes["http.route"] == "/v1/accounts/{account_id}"
    assert span.attributes["request.id"] == "request_12345678"
    rendered = repr((points, span.attributes))
    assert secret_id not in rendered
    assert "och_live_should-never-appear" not in rendered


def test_unmatched_and_untrusted_values_collapse_to_finite_dimensions(
    observed: tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter],
) -> None:
    telemetry, reader, spans = observed
    app = FastAPI()
    telemetry.instrument_fastapi(app)
    with TestClient(app) as client:
        response = client.get(
            "/does-not-exist/private@example.org",
            headers={"X-Request-ID": "not valid because spaces"},
        )
    assert response.status_code == 404
    point = _points(reader, "clearinghouse.http.requests")[0]
    assert dict(point.attributes) == {
        "route": "unmatched",
        "method": "GET",
        "status_family": "4xx",
    }
    span = spans.get_finished_spans()[0]
    assert span.attributes is not None
    assert "request.id" not in span.attributes


def test_boundary_classification_covers_auth_signer_and_metering(
    observed: tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter],
) -> None:
    telemetry, reader, _ = observed
    app = FastAPI()

    @app.post("/v1/auth/email/code", status_code=429)
    async def auth() -> None:
        return None

    @app.post("/v1/authorize", status_code=402)
    async def signer() -> None:
        return None

    @app.get("/v1/usage", status_code=503)
    async def metering() -> None:
        return None

    telemetry.instrument_fastapi(app)
    with TestClient(app) as client:
        assert client.post("/v1/auth/email/code").status_code == 429
        assert client.post("/v1/authorize").status_code == 402
        assert client.get("/v1/usage").status_code == 503
    assert dict(_points(reader, "clearinghouse.auth.attempts")[0].attributes)["adapter"] == (
        "email_code"
    )
    assert (
        dict(_points(reader, "clearinghouse.signer.authorization.decisions")[0].attributes)[
            "reason"
        ]
        == "insufficient_funds"
    )
    assert dict(_points(reader, "clearinghouse.metering.events")[0].attributes)["outcome"] == (
        "unavailable"
    )


@pytest.mark.asyncio
async def test_non_http_scope_is_forwarded_without_metrics(
    observed: tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter],
) -> None:
    telemetry, reader, _ = observed
    called = False

    async def app(_scope: Any, _receive: Any, _send: Any) -> None:
        nonlocal called
        called = True

    middleware = TelemetryMiddleware(app, telemetry)
    await middleware({"type": "lifespan"}, None, None)
    assert called
    assert reader.get_metrics_data() is None


def test_exception_marks_span_error_without_exception_text_attribute(
    observed: tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter],
) -> None:
    telemetry, _, spans = observed
    app = FastAPI()

    @app.get("/failure")
    async def failure() -> None:
        raise RuntimeError("person@example.org och_live_private")

    telemetry.instrument_fastapi(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/failure").status_code == 500
    span = spans.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
    assert "person@example.org" not in repr((span.attributes, span.events))
    assert "och_live_private" not in repr((span.attributes, span.events))


def test_auth_signer_metering_and_database_dimensions_are_closed(
    observed: tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter],
) -> None:
    telemetry, reader, _ = observed
    telemetry.record_auth("attacker@example.org", Outcome.DENIED, Reason.RATE_LIMITED)
    telemetry.record_signer(Outcome.DENIED, safe_reason("unknown_credential"))
    telemetry.record_metering(safe_outcome("quarantined"), safe_reason("payload_too_large"))
    telemetry.record_database(Outcome.UNAVAILABLE, Reason.STORAGE)

    expected = {
        "clearinghouse.auth.attempts": ("other", "denied", "rate_limited"),
        "clearinghouse.signer.authorization.decisions": (
            "remote_signer",
            "denied",
            "unknown_credential",
        ),
        "clearinghouse.metering.events": ("redpanda", "invalid", "poison"),
        "clearinghouse.database.operations": ("postgresql", "unavailable", "storage"),
    }
    for name, values in expected.items():
        attributes = dict(_points(reader, name)[0].attributes)
        assert (attributes["adapter"], attributes["outcome"], attributes["reason"]) == values
        assert not (
            {"tenant", "account", "principal", "session", "topic", "partition"} & set(attributes)
        )


def test_operational_snapshots_cover_exposure_consumer_abuse_and_adapter_health(
    observed: tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter],
) -> None:
    telemetry, reader, _ = observed
    telemetry.record_exposure_snapshot(
        open_utilization_bps=8750,
        pending_utilization_bps=3000,
        unresolved_utilization_bps=500,
        pending_count=4,
        unresolved_count=2,
    )
    telemetry.record_consumer_snapshot(aggregate_lag=12, heartbeat_age_seconds=3)
    telemetry.record_settlement_age(pending_seconds=42, unresolved_seconds=300)
    telemetry.record_auth("email_verify", Outcome.DENIED, Reason.RATE_LIMITED)
    telemetry.record_metering(Outcome.INVALID, Reason.GAP)
    telemetry.record_adapter_health(Component.DATABASE, "tenant_secret", healthy=False)
    expected = {
        "clearinghouse.exposure.open.utilization": 8750,
        "clearinghouse.exposure.pending.utilization": 3000,
        "clearinghouse.exposure.unresolved.utilization": 500,
        "clearinghouse.exposure.pending.reservations": 4,
        "clearinghouse.exposure.unresolved.reservations": 2,
        "clearinghouse.metering.consumer.lag": 12,
        "clearinghouse.metering.consumer.heartbeat.age": 3,
        "clearinghouse.auth.abuse": 1,
        "clearinghouse.metering.consumer.dropped": 1,
        "clearinghouse.metering.consumer.gaps": 1,
        "clearinghouse.metering.settlement.failures": 1,
        "clearinghouse.metering.settlement.pending.oldest.age": 42,
        "clearinghouse.metering.settlement.unresolved.oldest.age": 300,
        "clearinghouse.adapter.health": 0,
    }
    forbidden = {"tenant", "account", "principal", "session", "state", "topic", "partition"}
    data = reader.get_metrics_data()
    assert data is not None
    points_by_name = {
        metric.name: point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        for point in metric.data.data_points
    }
    for name, value in expected.items():
        point = cast(Any, points_by_name[name])
        assert point.value == value
        attributes = dict(point.attributes or {})
        assert not forbidden & set(attributes)
        assert "tenant_secret" not in attributes.values()
    adapter_point = cast(Any, points_by_name["clearinghouse.adapter.health"])
    assert dict(adapter_point.attributes or {}) == {"component": "database", "adapter": "other"}

    # A transition updates one stable time series; outcome labels would leave
    # contradictory stale health series in cumulative OTLP exports.
    telemetry.record_adapter_health(Component.DATABASE, "tenant_secret", healthy=True)
    adapter_points = _points(reader, "clearinghouse.adapter.health")
    assert len(adapter_points) == 1
    assert adapter_points[0].value == 1

    with pytest.raises(ValueError):
        telemetry.record_settlement_age(pending_seconds=-1, unresolved_seconds=0)


@pytest.mark.parametrize(
    "values",
    [
        {
            "open_utilization_bps": -1,
            "pending_utilization_bps": 0,
            "unresolved_utilization_bps": 0,
            "pending_count": 0,
            "unresolved_count": 0,
        },
        {
            "open_utilization_bps": 0,
            "pending_utilization_bps": 10_001,
            "unresolved_utilization_bps": 0,
            "pending_count": 0,
            "unresolved_count": 0,
        },
        {
            "open_utilization_bps": 0,
            "pending_utilization_bps": 0,
            "unresolved_utilization_bps": 0,
            "pending_count": -1,
            "unresolved_count": 0,
        },
    ],
)
def test_exposure_snapshot_rejects_invalid_aggregate_values(
    observed: tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter],
    values: dict[str, int],
) -> None:
    with pytest.raises(ValueError):
        observed[0].record_exposure_snapshot(**values)


def test_consumer_snapshot_rejects_invalid_aggregate_values(
    observed: tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter],
) -> None:
    with pytest.raises(ValueError):
        observed[0].record_consumer_snapshot(aggregate_lag=-1, heartbeat_age_seconds=0)
    with pytest.raises(ValueError):
        observed[0].record_consumer_snapshot(aggregate_lag=0, heartbeat_age_seconds=-1)


def test_log_processors_add_trace_context_and_redact_secrets_and_email(
    observed: tuple[Telemetry, InMemoryMetricReader, InMemorySpanExporter],
) -> None:
    telemetry, _, _ = observed
    structlog.contextvars.bind_contextvars(request_id="request_12345678")
    try:
        with telemetry.tracer.start_as_current_span("safe"):
            event = redact_telemetry(
                None,
                "info",
                {
                    "event": "Bearer abc and user@example.org used och_live_tokenvalue",
                    "api_secret": "never-export-this",
                    "safe": "settled",
                },
            )
            correlated = add_trace_context(None, "info", event)
    finally:
        structlog.contextvars.clear_contextvars()
    assert correlated["event"] == ("Bearer [redacted] and [redacted-email] used [redacted-token]")
    assert correlated["api_secret"] == "[redacted]"  # noqa: S105 -- redaction output
    assert correlated["safe"] == "settled"
    assert correlated["request_id"] == "request_12345678"
    assert len(correlated["trace_id"]) == 32
    assert len(correlated["span_id"]) == 16


def test_otlp_log_filter_redacts_third_party_records_before_export() -> None:
    record = logging.LogRecord(
        "dependency",
        logging.WARNING,
        __file__,
        1,
        "request by %s using Bearer raw-value",
        ("person@example.org",),
        None,
    )
    vars(record)["authorization"] = "och_session_private"
    try:
        raise RuntimeError("person@example.org used och_live_exception")
    except RuntimeError:
        record.exc_info = sys.exc_info()
    assert TelemetryLogFilter().filter(record)
    assert record.getMessage() == "request by [redacted-email] using Bearer [redacted]"
    assert vars(record)["authorization"] == "[redacted]"
    assert record.exc_info is None


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"service_name": "UPPER"}, "service_name"),
        ({"service_name": "api", "export_interval_millis": 999}, "interval"),
        ({"service_name": "api", "endpoint": "grpc://collector"}, "endpoint"),
    ],
)
def test_exporter_configuration_fails_closed(values: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        TelemetryConfig(**values)


def test_disabled_export_and_secret_headers_are_safe_in_diagnostics() -> None:
    config = TelemetryConfig(
        service_name="clearinghouse-api",
        headers={"Authorization": "Bearer collector-secret"},
    )
    assert "collector-secret" not in repr(config)
    telemetry = Telemetry.configure(config)
    assert telemetry._meter_provider is None
    assert telemetry._tracer_provider is None
    assert telemetry._logger_provider is None
    telemetry.shutdown()


def test_otlp_http_exports_metrics_traces_and_redacted_logs() -> None:
    requests: list[tuple[str, bytes]] = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 -- stdlib HTTP callback
            length = int(self.headers.get("Content-Length", "0"))
            requests.append((self.path, self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        telemetry = Telemetry.configure(
            TelemetryConfig(
                service_name="clearinghouse-test",
                endpoint=f"http://127.0.0.1:{server.server_port}",
                export_interval_millis=1_000,
            )
        )
        with telemetry.tracer.start_as_current_span("export-test"):
            telemetry.record_auth("session", Outcome.SUCCESS)
            logging.getLogger("test.telemetry").warning(
                "authentication failed for person@example.org with Bearer private-value"
            )
        with pytest.raises(RuntimeError, match="already configured"):
            Telemetry.configure(
                TelemetryConfig(
                    service_name="clearinghouse-test",
                    endpoint=f"http://127.0.0.1:{server.server_port}",
                )
            )
        telemetry.shutdown()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert {path for path, _body in requests} == {"/v1/logs", "/v1/metrics", "/v1/traces"}
    encoded = b"".join(body for _path, body in requests)
    assert b"person@example.org" not in encoded
    assert b"private-value" not in encoded


def test_reason_and_outcome_normalization_never_passes_free_form_values() -> None:
    assert safe_reason("tenant_tenant-secret") is Reason.OTHER
    assert safe_reason(object()) is Reason.OTHER
    assert safe_outcome("settled") is Outcome.SUCCESS
    assert safe_outcome("denied") is Outcome.DENIED
    assert safe_outcome("fork") is Outcome.INVALID
    assert safe_outcome("arbitrary-account-id") is Outcome.ERROR


def test_boundary_helpers_collapse_every_untrusted_shape() -> None:
    assert _http_outcome(200) == (Outcome.SUCCESS, Reason.NONE)
    assert _http_outcome(401) == (Outcome.DENIED, Reason.UNAUTHENTICATED)
    assert _http_outcome(403) == (Outcome.DENIED, Reason.UNAUTHORIZED)
    assert _http_outcome(409) == (Outcome.CONFLICT, Reason.OTHER)
    assert _http_outcome(400) == (Outcome.INVALID, Reason.INVALID_INPUT)
    assert _auth_route("/v1/auth/email/verify") == "email_verify"
    assert _auth_route("/v1/auth/oauth/{provider}/start") == "oauth"
    assert _auth_route("/v1/auth/session") == "session"
    assert _method("TRACE") == "OTHER"
    assert _method(None) == "OTHER"
    assert _status_family(42) == "other"
    assert _request_id(None) is None
    assert _request_id([]) is None
    assert _route_template({"route": type("Route", (), {"path": "/bad:$"})()}) == "unmatched"


def test_process_capability_and_database_wrapper_delegate() -> None:
    class RecordingTelemetry(Telemetry):
        def __init__(self) -> None:
            super().__init__()
            self.engine: object | None = None

        def instrument_sqlalchemy(self, engine: Any) -> None:
            self.engine = engine

    telemetry = RecordingTelemetry()
    set_telemetry(telemetry)
    engine = object()
    instrument_database_engine(engine)
    assert get_telemetry() is telemetry
    assert telemetry.engine is engine
    set_telemetry(Telemetry())
