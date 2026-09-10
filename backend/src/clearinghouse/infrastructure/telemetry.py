"""Safe OpenTelemetry primitives for the clearinghouse process boundaries.

Telemetry deliberately accepts only bounded, operational dimensions.  Business
identifiers, raw URLs, Kafka coordinates, credentials, email addresses, and
free-form exception text never become metric attributes.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

import structlog
from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer

_SAFE_REQUEST_ID: Final = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_EMAIL: Final = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_BEARER: Final = re.compile(r"(?i)\bbearer\s+\S+")
_CLEARINGHOUSE_SECRET: Final = re.compile(r"\boch_(?:live|session)_[A-Za-z0-9_-]+\b")
_SENSITIVE_KEYS: Final = frozenset(
    {
        "authorization",
        "bearer",
        "code",
        "cookie",
        "csrf",
        "email",
        "password",
        "secret",
        "session_token",
        "token",
    }
)


class Outcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    INVALID = "invalid"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class Reason(StrEnum):
    NONE = "none"
    UNAUTHENTICATED = "unauthenticated"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    INVALID_INPUT = "invalid_input"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    KILL_SWITCH = "kill_switch"
    UNKNOWN_CREDENTIAL = "unknown_credential"
    LEDGER_UNAVAILABLE = "ledger_unavailable"
    DUPLICATE = "duplicate"
    FORK = "fork"
    GAP = "gap"
    POISON = "poison"
    FEE_MISMATCH = "fee_mismatch"
    LINEAGE = "lineage"
    UNKNOWN_RESERVATION = "unknown_reservation"
    TIMEOUT = "timeout"
    STORAGE = "storage"
    OTHER = "other"


class Component(StrEnum):
    API = "api"
    AUTH = "auth"
    SIGNER = "signer"
    METERING = "metering"
    DATABASE = "database"


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    """Validated process-local exporter configuration.

    Headers are intentionally excluded from repr so collector credentials cannot
    be exposed by diagnostics or exception logging.
    """

    service_name: str
    endpoint: str | None = None
    export_interval_millis: int = 60_000
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", self.service_name):
            raise ValueError("service_name must be a bounded low-cardinality name")
        if not 1_000 <= self.export_interval_millis <= 300_000:
            raise ValueError("export interval must be between 1 and 300 seconds")
        if self.endpoint is not None and not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("OTLP endpoint must use HTTP or HTTPS")


class Telemetry:
    """One process telemetry capability with a no-export default."""

    def __init__(
        self,
        *,
        tracer: Tracer | None = None,
        meter: Meter | None = None,
        tracer_provider: TracerProvider | None = None,
        meter_provider: MeterProvider | None = None,
        logger_provider: LoggerProvider | None = None,
        logging_handler: logging.Handler | None = None,
    ) -> None:
        self.tracer = tracer or trace.get_tracer("clearinghouse")
        selected_meter = meter or metrics.get_meter("clearinghouse")
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        self._logger_provider = logger_provider
        self._logging_handler = logging_handler
        self.http_requests = selected_meter.create_counter(
            "clearinghouse.http.requests", unit="{request}"
        )
        self.http_duration = selected_meter.create_histogram(
            "clearinghouse.http.request.duration", unit="s"
        )
        self.auth_attempts = selected_meter.create_counter(
            "clearinghouse.auth.attempts", unit="{attempt}"
        )
        self.signer_decisions = selected_meter.create_counter(
            "clearinghouse.signer.authorization.decisions", unit="{decision}"
        )
        self.metering_events = selected_meter.create_counter(
            "clearinghouse.metering.events", unit="{event}"
        )
        self.database_operations = selected_meter.create_counter(
            "clearinghouse.database.operations", unit="{operation}"
        )
        self.auth_abuse = selected_meter.create_counter(
            "clearinghouse.auth.abuse", unit="{attempt}"
        )
        self.exposure_open_utilization = selected_meter.create_gauge(
            "clearinghouse.exposure.open.utilization", unit="{basis_point}"
        )
        self.exposure_pending = selected_meter.create_gauge(
            "clearinghouse.exposure.pending.utilization", unit="{basis_point}"
        )
        self.exposure_unresolved = selected_meter.create_gauge(
            "clearinghouse.exposure.unresolved.utilization", unit="{basis_point}"
        )
        self.pending_reservations = selected_meter.create_gauge(
            "clearinghouse.exposure.pending.reservations", unit="{reservation}"
        )
        self.unresolved_reservations = selected_meter.create_gauge(
            "clearinghouse.exposure.unresolved.reservations", unit="{reservation}"
        )
        self.consumer_lag = selected_meter.create_gauge(
            "clearinghouse.metering.consumer.lag", unit="{message}"
        )
        self.consumer_heartbeat_age = selected_meter.create_gauge(
            "clearinghouse.metering.consumer.heartbeat.age", unit="s"
        )
        self.consumer_dropped = selected_meter.create_counter(
            "clearinghouse.metering.consumer.dropped", unit="{message}"
        )
        self.consumer_gaps = selected_meter.create_counter(
            "clearinghouse.metering.consumer.gaps", unit="{gap}"
        )
        self.settlement_failures = selected_meter.create_counter(
            "clearinghouse.metering.settlement.failures", unit="{failure}"
        )
        self.pending_oldest_age = selected_meter.create_gauge(
            "clearinghouse.metering.settlement.pending.oldest.age", unit="s"
        )
        self.unresolved_oldest_age = selected_meter.create_gauge(
            "clearinghouse.metering.settlement.unresolved.oldest.age", unit="s"
        )
        self.adapter_health = selected_meter.create_gauge("clearinghouse.adapter.health", unit="1")

    @classmethod
    def configure(cls, config: TelemetryConfig) -> Telemetry:
        """Build OTLP/HTTP trace and metric exporters, or a local no-op capability."""
        if config.endpoint is None:
            return cls()
        if any(isinstance(handler, LoggingHandler) for handler in logging.getLogger().handlers):
            raise RuntimeError("clearinghouse OTLP logging is already configured")
        resource = Resource.create({"service.name": config.service_name})
        endpoint = config.endpoint.rstrip("/")
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=dict(config.headers))
            )
        )
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=dict(config.headers)),
            export_interval_millis=config.export_interval_millis,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", headers=dict(config.headers))
            )
        )
        logging_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        logging_handler.addFilter(TelemetryLogFilter())
        logging.getLogger().addHandler(logging_handler)
        return cls(
            tracer=tracer_provider.get_tracer("clearinghouse"),
            meter=meter_provider.get_meter("clearinghouse"),
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            logger_provider=logger_provider,
            logging_handler=logging_handler,
        )

    def instrument_fastapi(self, app: FastAPI) -> None:
        """Install safe HTTP telemetry without recording raw request targets."""
        app.add_middleware(TelemetryMiddleware, telemetry=self)

    def instrument_sqlalchemy(self, engine: Any) -> None:
        """Trace SQLAlchemy calls without values, connection URLs, or SQL comments."""
        SQLAlchemyInstrumentor().instrument(
            engine=getattr(engine, "sync_engine", engine),
            tracer_provider=self._tracer_provider,
            enable_commenter=False,
        )

    def record_auth(self, flow: str, outcome: Outcome, reason: Reason = Reason.NONE) -> None:
        self.auth_attempts.add(
            1,
            {
                "component": Component.AUTH.value,
                "adapter": _auth_flow(flow),
                "outcome": outcome.value,
                "reason": reason.value,
            },
        )
        if reason is Reason.RATE_LIMITED:
            self.auth_abuse.add(
                1,
                {
                    "component": Component.AUTH.value,
                    "adapter": _auth_flow(flow),
                    "outcome": Outcome.DENIED.value,
                    "reason": Reason.RATE_LIMITED.value,
                },
            )

    def record_signer(self, outcome: Outcome, reason: Reason = Reason.NONE) -> None:
        self.signer_decisions.add(
            1,
            {
                "component": Component.SIGNER.value,
                "adapter": "remote_signer",
                "outcome": outcome.value,
                "reason": reason.value,
            },
        )

    def record_metering(self, outcome: Outcome, reason: Reason = Reason.NONE) -> None:
        self.metering_events.add(
            1,
            {
                "component": Component.METERING.value,
                "adapter": "redpanda",
                "outcome": outcome.value,
                "reason": reason.value,
            },
        )
        attributes = {
            "component": Component.METERING.value,
            "adapter": "redpanda",
            "outcome": outcome.value,
            "reason": reason.value,
        }
        if reason is Reason.GAP:
            self.consumer_gaps.add(1, attributes)
        if outcome is Outcome.INVALID:
            self.consumer_dropped.add(1, attributes)
        if outcome in {Outcome.INVALID, Outcome.ERROR, Outcome.UNAVAILABLE}:
            self.settlement_failures.add(1, attributes)

    def record_database(self, outcome: Outcome, reason: Reason = Reason.NONE) -> None:
        self.database_operations.add(
            1,
            {
                "component": Component.DATABASE.value,
                "adapter": "postgresql",
                "outcome": outcome.value,
                "reason": reason.value,
            },
        )

    def record_exposure_snapshot(
        self,
        *,
        open_utilization_bps: int,
        pending_utilization_bps: int,
        unresolved_utilization_bps: int,
        pending_count: int,
        unresolved_count: int,
    ) -> None:
        """Record aggregate exposure without exporting monetary values or identities."""
        utilizations = (
            open_utilization_bps,
            pending_utilization_bps,
            unresolved_utilization_bps,
        )
        if any(value < 0 or value > 10_000 for value in utilizations):
            raise ValueError("utilization must be between zero and 10000 basis points")
        if pending_count < 0 or unresolved_count < 0:
            raise ValueError("exposure counts cannot be negative")
        attributes = {
            "component": Component.SIGNER.value,
            "adapter": "postgresql",
            "outcome": Outcome.SUCCESS.value,
            "reason": Reason.NONE.value,
        }
        self.exposure_open_utilization.set(open_utilization_bps, attributes)
        self.exposure_pending.set(pending_utilization_bps, attributes)
        self.exposure_unresolved.set(unresolved_utilization_bps, attributes)
        self.pending_reservations.set(pending_count, attributes)
        self.unresolved_reservations.set(unresolved_count, attributes)

    def record_consumer_snapshot(self, *, aggregate_lag: int, heartbeat_age_seconds: int) -> None:
        """Record one cross-partition consumer snapshot without Kafka coordinates."""
        self.record_consumer_lag(aggregate_lag)
        self.record_consumer_heartbeat(heartbeat_age_seconds)

    def record_consumer_lag(self, aggregate_lag: int) -> None:
        """Record aggregate lag without topic or partition cardinality."""
        if aggregate_lag < 0:
            raise ValueError("consumer lag cannot be negative")
        attributes = {
            "component": Component.METERING.value,
            "adapter": "redpanda",
            "outcome": Outcome.SUCCESS.value,
            "reason": Reason.NONE.value,
        }
        self.consumer_lag.set(aggregate_lag, attributes)

    def record_consumer_heartbeat(self, heartbeat_age_seconds: int) -> None:
        """Record age of the exact configured consumer's durable heartbeat."""
        if heartbeat_age_seconds < 0:
            raise ValueError("consumer heartbeat age cannot be negative")
        attributes = {
            "component": Component.METERING.value,
            "adapter": "redpanda",
            "outcome": Outcome.SUCCESS.value,
            "reason": Reason.NONE.value,
        }
        self.consumer_heartbeat_age.set(heartbeat_age_seconds, attributes)

    def record_settlement_age(self, *, pending_seconds: int, unresolved_seconds: int) -> None:
        """Record oldest outstanding settlement ages without receipt identifiers."""
        if pending_seconds < 0 or unresolved_seconds < 0:
            raise ValueError("settlement age cannot be negative")
        attributes = {
            "component": Component.METERING.value,
            "adapter": "postgresql",
        }
        self.pending_oldest_age.set(pending_seconds, attributes)
        self.unresolved_oldest_age.set(unresolved_seconds, attributes)

    def record_adapter_health(self, component: Component, adapter: str, *, healthy: bool) -> None:
        """Record health for one statically registered adapter name."""
        safe_adapter = (
            adapter if adapter in {"postgresql", "redpanda", "remote_signer"} else "other"
        )
        self.adapter_health.set(
            1 if healthy else 0,
            {
                "component": component.value,
                "adapter": safe_adapter,
            },
        )

    def shutdown(self) -> None:
        """Flush bounded SDK providers during graceful process shutdown."""
        if self._meter_provider is not None:
            self._meter_provider.shutdown()
        if self._tracer_provider is not None:
            self._tracer_provider.shutdown()
        if self._logging_handler is not None:
            logging.getLogger().removeHandler(self._logging_handler)
        if self._logger_provider is not None:
            self._logger_provider.shutdown()


class TelemetryLogFilter(logging.Filter):
    """Redact stdlib and third-party records immediately before OTLP export."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_text(record.getMessage())
        record.args = ()
        # Exception messages and stack traces are unbounded and may contain
        # credentials or PII. The enclosing span/status retains operability.
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        for key, value in tuple(vars(record).items()):
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in _SENSITIVE_KEYS):
                setattr(record, key, "[redacted]")
            elif isinstance(value, str):
                setattr(record, key, _redact_text(value))
        return True


class TelemetryMiddleware:
    """Pure ASGI middleware recording only route templates and bounded outcomes."""

    def __init__(self, app: Any, telemetry: Telemetry) -> None:
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = _method(scope.get("method"))
        request_id = _request_id(scope.get("headers", []))
        started = time.perf_counter()
        status_code = 500
        if request_id is not None:
            structlog.contextvars.bind_contextvars(request_id=request_id)

        async def observe(message: MutableMapping[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        with self.telemetry.tracer.start_as_current_span(
            f"HTTP {method}", kind=SpanKind.SERVER, record_exception=False
        ) as span:
            span.set_attribute("http.request.method", method)
            if request_id is not None:
                span.set_attribute("request.id", request_id)
            try:
                await self.app(scope, receive, observe)
            except BaseException:
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                route = _route_template(scope)
                family = _status_family(status_code)
                attributes = {"route": route, "method": method, "status_family": family}
                self.telemetry.http_requests.add(1, attributes)
                self.telemetry.http_duration.record(time.perf_counter() - started, attributes)
                span.set_attribute("http.route", route)
                span.set_attribute("http.response.status_code", status_code)
                _record_boundary_metric(self.telemetry, route, status_code)
                if request_id is not None:
                    structlog.contextvars.unbind_contextvars("request_id")


def add_trace_context(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Add request/trace correlation to structured logs without business IDs."""
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event_dict["trace_id"] = format(context.trace_id, "032x")
        event_dict["span_id"] = format(context.span_id, "016x")
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if isinstance(request_id, str) and _SAFE_REQUEST_ID.fullmatch(request_id):
        event_dict["request_id"] = request_id
    return event_dict


def redact_telemetry(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Fail closed when secrets or common PII reach the logging boundary."""
    for key, value in tuple(event_dict.items()):
        normalized = key.lower().replace("-", "_")
        if any(part in normalized for part in _SENSITIVE_KEYS):
            event_dict[key] = "[redacted]"
        elif isinstance(value, str):
            event_dict[key] = _redact_text(value)
    return event_dict


def _record_boundary_metric(telemetry: Telemetry, route: str, status: int) -> None:
    outcome, reason = _http_outcome(status)
    if route == "/v1/auth" or route.startswith("/v1/auth/"):
        telemetry.record_auth(_auth_route(route), outcome, reason)
    elif route == "/v1/authorize":
        telemetry.record_signer(outcome, reason)
    elif route.startswith(("/v1/usage", "/v1/charges", "/v1/open-reservations")):
        telemetry.record_metering(outcome, reason)


def _http_outcome(status: int) -> tuple[Outcome, Reason]:
    if status < 400:
        return Outcome.SUCCESS, Reason.NONE
    if status == 401:
        return Outcome.DENIED, Reason.UNAUTHENTICATED
    if status == 403:
        return Outcome.DENIED, Reason.UNAUTHORIZED
    if status == 402:
        return Outcome.DENIED, Reason.INSUFFICIENT_FUNDS
    if status == 409:
        return Outcome.CONFLICT, Reason.OTHER
    if status == 429:
        return Outcome.DENIED, Reason.RATE_LIMITED
    if status >= 500:
        return Outcome.UNAVAILABLE, Reason.STORAGE
    return Outcome.INVALID, Reason.INVALID_INPUT


def _route_template(scope: Mapping[str, Any]) -> str:
    route = scope.get("route")
    template = getattr(route, "path", None)
    if not isinstance(template, str) or not template.startswith("/") or len(template) > 160:
        return "unmatched"
    if not re.fullmatch(r"/[A-Za-z0-9_{}./-]*", template):
        return "unmatched"
    return template


def _method(value: object) -> str:
    method = value if isinstance(value, str) else "OTHER"
    return method if method in {"DELETE", "GET", "OPTIONS", "PATCH", "POST", "PUT"} else "OTHER"


def _status_family(status: int) -> str:
    return f"{status // 100}xx" if 100 <= status <= 599 else "other"


def _request_id(headers: object) -> str | None:
    if not isinstance(headers, list):
        return None
    for raw_name, raw_value in headers:
        if raw_name.lower() == b"x-request-id":
            value = raw_value.decode("ascii", errors="ignore")
            return value if _SAFE_REQUEST_ID.fullmatch(value) else None
    return None


def _auth_route(route: str) -> str:
    if "/email/code" in route:
        return "email_code"
    if "/email/verify" in route:
        return "email_verify"
    if "/oauth/" in route:
        return "oauth"
    return "session"


def _auth_flow(flow: str) -> str:
    return flow if flow in {"email_code", "email_verify", "oauth", "session"} else "other"


def _redact_text(value: str) -> str:
    return _EMAIL.sub(
        "[redacted-email]",
        _CLEARINGHOUSE_SECRET.sub("[redacted-token]", _BEARER.sub("Bearer [redacted]", value)),
    )


def safe_reason(value: object) -> Reason:
    """Translate boundary results to a finite reason vocabulary."""
    if not isinstance(value, str):
        return Reason.OTHER
    aliases = {
        "duplicate": Reason.DUPLICATE,
        "fork": Reason.FORK,
        "gap": Reason.GAP,
        "invalid_schema": Reason.POISON,
        "key_mismatch": Reason.POISON,
        "payload_too_large": Reason.POISON,
        "fee_mismatch": Reason.FEE_MISMATCH,
        "lineage_mismatch": Reason.LINEAGE,
        "unknown_reservation": Reason.UNKNOWN_RESERVATION,
        "sequence_gap": Reason.GAP,
        "forked_observation": Reason.FORK,
        "transport_divergence": Reason.FORK,
        "kill_switch": Reason.KILL_SWITCH,
        "kill_switch_active": Reason.KILL_SWITCH,
        "unknown_credential": Reason.UNKNOWN_CREDENTIAL,
        "ledger_unavailable": Reason.LEDGER_UNAVAILABLE,
    }
    return aliases.get(value, Reason.OTHER)


def safe_outcome(value: object) -> Outcome:
    """Translate boundary results to a finite outcome vocabulary."""
    normalized = str(getattr(value, "value", value)).lower()
    if normalized in {"processed", "settled", "accepted", "duplicate", "ignored"}:
        return Outcome.SUCCESS
    if normalized in {"denied", "rejected"}:
        return Outcome.DENIED
    if normalized in {"fork", "gap", "quarantined", "invalid"}:
        return Outcome.INVALID
    return Outcome.ERROR


_telemetry: Telemetry = Telemetry()


def set_telemetry(telemetry: Telemetry) -> None:
    """Set the process capability once at the composition root."""
    global _telemetry
    _telemetry = telemetry


def get_telemetry() -> Telemetry:
    return _telemetry


def instrument_database_engine(engine: Any) -> None:
    get_telemetry().instrument_sqlalchemy(engine)


def record_database_result(success: bool) -> None:
    get_telemetry().record_database(
        Outcome.SUCCESS if success else Outcome.UNAVAILABLE,
        Reason.NONE if success else Reason.STORAGE,
    )
