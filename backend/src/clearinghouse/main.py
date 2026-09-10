"""Process composition root."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from clearinghouse import __version__
from clearinghouse.adapters.http.accounts import (
    AccountProblem,
    account_problem_handler,
)
from clearinghouse.adapters.http.accounts import (
    router as accounts_router,
)
from clearinghouse.adapters.http.auth import create_auth_router
from clearinghouse.adapters.http.health import router as health_router
from clearinghouse.adapters.http.metering import router as metering_router
from clearinghouse.adapters.http.onboarding import router as onboarding_router
from clearinghouse.adapters.http.operations import router as operations_router
from clearinghouse.adapters.http.signer import (
    SignerProblem,
    signer_problem_handler,
)
from clearinghouse.adapters.http.signer import (
    router as signer_router,
)
from clearinghouse.adapters.kafka import decode_go_livepeer
from clearinghouse.application.accounts import AccountService
from clearinghouse.application.authentication import AuthPolicy, AuthService
from clearinghouse.application.metering import MeteringService
from clearinghouse.application.onboarding import OnboardingService
from clearinghouse.application.operations import OperationsService
from clearinghouse.application.ports import Store
from clearinghouse.application.signer import SignerService
from clearinghouse.domain.auth import AuthProvider
from clearinghouse.infrastructure.accounts import PostgresAccountsRepository
from clearinghouse.infrastructure.auth_repository import PostgresAuthRepository
from clearinghouse.infrastructure.config import Settings, get_settings
from clearinghouse.infrastructure.database import PostgresStore
from clearinghouse.infrastructure.logging import configure_logging
from clearinghouse.infrastructure.metering import PostgresMeteringRepository
from clearinghouse.infrastructure.oauth import AuthlibOAuthProviderClient, OAuthClientConfig
from clearinghouse.infrastructure.onboarding import PostgresOnboardingRepository
from clearinghouse.infrastructure.operations import (
    PostgresOperationsRepository,
    reference_adapter_manifest,
)
from clearinghouse.infrastructure.resend_email import ResendEmailCodeSender
from clearinghouse.infrastructure.signer import PostgresSignerRepository
from clearinghouse.infrastructure.telemetry import Telemetry, TelemetryConfig, set_telemetry

OPENAPI_DESCRIPTION = (
    "Contract for the production walking slice. Monetary amounts and quantities use "
    "integer or exact decimal representations; JSON floating-point values are never "
    "accepted for financial state."
)


def _operation_security(path: str) -> list[dict[str, list[str]]]:
    """Document the authentication mechanism enforced by each public operation."""
    if path.startswith("/health/") or path in {
        "/v1/auth/providers",
        "/v1/auth/email/code",
        "/v1/auth/email/verify",
        "/v1/auth/oauth/{provider}/start",
        "/v1/auth/oauth/{provider}/callback",
    }:
        return []
    if path in {"/v1/authorize", "/v1/compat/go-livepeer/authorize"}:
        return [{"signerSecret": []}]
    if path.startswith(("/v1/sessions", "/v1/leases")):
        return [{"bearerAuth": []}, {"cookieAuth": []}]
    return [{"cookieAuth": []}]


def runtime_openapi(app: FastAPI) -> dict[str, Any]:
    """Build the deterministic public schema from the actual FastAPI routes."""
    schema = get_openapi(
        title="Livepeer Open Clearinghouse API",
        version=__version__,
        description=OPENAPI_DESCRIPTION,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    if not isinstance(components, dict):
        raise TypeError("generated OpenAPI components must be an object")
    components["securitySchemes"] = {
        "cookieAuth": {"type": "apiKey", "in": "cookie", "name": "och_session"},
        "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "opaque"},
        "signerSecret": {
            "type": "http",
            "scheme": "bearer",
            "description": "Private deployment credential used only by the configured signer.",
        },
    }
    schemas = components.setdefault("schemas", {})
    if not isinstance(schemas, dict):
        raise TypeError("generated OpenAPI schemas must be an object")
    schemas["Problem"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "title", "status", "request_id"],
        "properties": {
            "type": {"type": "string"},
            "title": {"type": "string"},
            "status": {"type": "integer", "minimum": 400, "maximum": 599},
            "request_id": {"type": "string"},
        },
    }
    validation_problem = {
        "description": "Request validation failed",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
        },
    }
    paths = schema.get("paths", {})
    if not isinstance(paths, dict):
        raise TypeError("generated OpenAPI paths must be an object")
    for path, item in paths.items():
        if not isinstance(path, str) or not isinstance(item, dict):
            raise TypeError("generated OpenAPI path entries must be objects")
        for method, operation in item.items():
            if method in {"delete", "get", "head", "options", "patch", "post", "put", "trace"}:
                if not isinstance(operation, dict):
                    raise TypeError("generated OpenAPI operations must be objects")
                operation["security"] = _operation_security(path)
                responses = operation.get("responses")
                if not isinstance(responses, dict):
                    raise TypeError("generated OpenAPI responses must be objects")
                if responses.pop("422", None) is not None:
                    responses.setdefault("400", validation_problem)
    return schema


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    auth_service: AuthService | None = None,
    account_service: AccountService | None = None,
    onboarding_service: OnboardingService | None = None,
    signer_service: SignerService | None = None,
    metering_service: MeteringService | None = None,
    operations_service: OperationsService | None = None,
) -> FastAPI:
    """Wire validated settings and concrete adapters exactly once."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    telemetry = Telemetry.configure(
        TelemetryConfig(
            service_name="clearinghouse-api",
            endpoint=resolved_settings.otel_exporter_otlp_endpoint,
            export_interval_millis=resolved_settings.otel_metric_export_interval_millis,
        )
    )
    set_telemetry(telemetry)
    resolved_store = store or PostgresStore.from_settings(resolved_settings)
    if isinstance(resolved_store, PostgresStore):
        telemetry.instrument_sqlalchemy(resolved_store.engine)
    resolved_onboarding = onboarding_service
    if resolved_onboarding is None and isinstance(resolved_store, PostgresStore):
        resolved_onboarding = OnboardingService(
            PostgresOnboardingRepository(resolved_store.session_factory),
            resolved_settings.auth_pepper.get_secret_value(),
            invitation_ttl_seconds=resolved_settings.identity_invitation_ttl_seconds,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.store = resolved_store
        try:
            bootstrap_email = resolved_settings.operator_bootstrap_email_value
            bootstrap_secret = resolved_settings.operator_bootstrap_secret
            if bootstrap_email is not None:
                if resolved_onboarding is None or bootstrap_secret is None:
                    raise RuntimeError("operator bootstrap requires PostgreSQL onboarding")
                result = await resolved_onboarding.bootstrap_operator(
                    bootstrap_email, bootstrap_secret.get_secret_value()
                )
                structlog.get_logger().info("operator_bootstrap_applied", created=result.created)
            if resolved_signer is not None:
                await resolved_signer.initialize()
            structlog.get_logger().info("service_started", version=__version__)
            yield
        finally:
            try:
                await resolved_store.close()
                structlog.get_logger().info("service_stopped")
            finally:
                telemetry.shutdown()
                set_telemetry(Telemetry())

    app = FastAPI(
        title="Livepeer Open Clearinghouse API",
        version=__version__,
        description=OPENAPI_DESCRIPTION,
        lifespan=lifespan,
    )

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = runtime_openapi(app)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    telemetry.instrument_fastapi(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(resolved_settings.auth_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-CSRF-Token",
            "X-Request-ID",
        ],
    )

    @app.middleware("http")
    async def private_api_cache_control(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, _error: RequestValidationError) -> JSONResponse:
        request_id = request.headers.get("x-request-id") or "request-validation"
        return JSONResponse(
            status_code=400,
            media_type="application/problem+json",
            content={
                "type": "urn:livepeer:clearinghouse:invalid-request",
                "title": "Invalid request",
                "status": 400,
                "request_id": request_id,
            },
        )

    app.state.store = resolved_store
    app.add_exception_handler(AccountProblem, account_problem_handler)  # type: ignore[arg-type]
    app.add_exception_handler(SignerProblem, signer_problem_handler)  # type: ignore[arg-type]
    app.include_router(health_router)
    resolved_auth = auth_service
    if resolved_auth is None and isinstance(resolved_store, PostgresStore):
        resolved_auth = _create_auth_service(resolved_settings, resolved_store)
    if resolved_auth is not None:
        auth_router, auth_dependencies = create_auth_router(resolved_auth, resolved_settings)
        app.state.auth = auth_dependencies
        app.include_router(auth_router)
    resolved_accounts = account_service
    if resolved_accounts is None and isinstance(resolved_store, PostgresStore):
        resolved_accounts = AccountService(
            PostgresAccountsRepository(resolved_store.engine),
            resolved_settings.credential_pepper.get_secret_value().encode(),
        )
    if resolved_accounts is not None:
        app.state.account_service = resolved_accounts
        app.include_router(accounts_router)
    resolved_metering = metering_service
    if resolved_metering is None and isinstance(resolved_store, PostgresStore):
        resolved_metering = MeteringService(
            PostgresMeteringRepository(
                resolved_store.session_factory,
                consumer_group=resolved_settings.kafka_metering_group_id,
                topic=resolved_settings.kafka_metering_topic,
                signer_id=resolved_settings.signer_id,
                reconciliation_batch_size=resolved_settings.metering_reconciliation_batch_size,
                confirmation_grace_seconds=resolved_settings.metering_confirmation_grace_seconds,
                heartbeat_stale_seconds=resolved_settings.metering_heartbeat_stale_seconds,
            ),
            decode_go_livepeer,
            max_payload_bytes=resolved_settings.metering_max_payload_bytes,
        )
    if resolved_metering is not None:
        app.state.metering_service = resolved_metering
        app.include_router(metering_router)
    if resolved_onboarding is not None:
        app.state.onboarding_service = resolved_onboarding
        app.include_router(onboarding_router)
    resolved_signer = signer_service
    if (
        resolved_signer is None
        and isinstance(resolved_store, PostgresStore)
        and auth_service is None
        and account_service is None
        and onboarding_service is None
    ):
        resolved_signer = SignerService(
            PostgresSignerRepository(
                resolved_store.session_factory,
                global_cap=resolved_settings.signer_global_exposure_cap,
                signer_id=resolved_settings.signer_id,
                signer_url=resolved_settings.signer_url,
                discovery_url=resolved_settings.signer_discovery_url,
            ),
            resolved_settings.signer_session_pepper.get_secret_value(),
            resolved_settings.signer_webhook_secret.get_secret_value(),
        )
    if resolved_signer is not None:
        app.state.signer_service = resolved_signer
        app.include_router(signer_router)
    resolved_operations = operations_service
    if resolved_operations is None and isinstance(resolved_store, PostgresStore):
        ports: list[dict[str, object]] = []
        if resolved_auth is not None or resolved_onboarding is not None:
            ports.append(
                {
                    "name": "identity",
                    "contract_version": "1.0",
                    "capabilities": ["email_otp", "identity_link", "oauth_discovery"],
                }
            )
        if resolved_accounts is not None:
            ports.extend(
                [
                    {
                        "name": "pricing",
                        "contract_version": "1.0",
                        "capabilities": ["exact_rate_cards"],
                    },
                    {
                        "name": "collection",
                        "contract_version": "1.0",
                        "capabilities": ["double_entry_grants"],
                    },
                ]
            )
        if resolved_signer is not None:
            ports.append(
                {
                    "name": "signer",
                    "contract_version": "1.0",
                    "capabilities": ["bounded_sessions", "go_livepeer_authorize"],
                }
            )
        if resolved_metering is not None:
            ports.append(
                {
                    "name": "metering",
                    "contract_version": "1.0",
                    "capabilities": ["postgres_settlement", "redpanda_consumer"],
                }
            )
        operations_repository = PostgresOperationsRepository(
            resolved_store.session_factory,
            [reference_adapter_manifest(ports)],
            heartbeat_stale_seconds=resolved_settings.metering_heartbeat_stale_seconds,
            consumer_group=resolved_settings.kafka_metering_group_id,
            topic=resolved_settings.kafka_metering_topic,
            signer_id=resolved_settings.signer_id,
        )
        app.state.operational_readiness = operations_repository
        resolved_operations = OperationsService(operations_repository)
    if resolved_operations is not None:
        app.state.operations_service = resolved_operations
        app.include_router(operations_router)
    return app


def _create_auth_service(settings: Settings, store: PostgresStore) -> AuthService:
    """Wire authentication ports from validated process settings."""
    enabled = {AuthProvider.EMAIL}
    if settings.auth_google_enabled:
        enabled.add(AuthProvider.GOOGLE)
    if settings.auth_github_enabled:
        enabled.add(AuthProvider.GITHUB)
    sender = ResendEmailCodeSender(
        api_key=settings.auth_resend_api_key.get_secret_value(),
        api_url=settings.auth_resend_api_url,
        from_address=settings.auth_resend_from,
    )
    oauth = AuthlibOAuthProviderClient(
        OAuthClientConfig(
            google_client_id=settings.auth_google_client_id,
            google_client_secret=(
                settings.auth_google_client_secret.get_secret_value()
                if settings.auth_google_client_secret
                else None
            ),
            github_client_id=settings.auth_github_client_id,
            github_client_secret=(
                settings.auth_github_client_secret.get_secret_value()
                if settings.auth_github_client_secret
                else None
            ),
        )
    )
    return AuthService(
        PostgresAuthRepository.from_session_factory(store.session_factory),
        sender,
        oauth,
        AuthPolicy(
            pepper=settings.auth_pepper.get_secret_value(),
            otp_ttl_seconds=settings.auth_otp_ttl_seconds,
            otp_max_attempts=settings.auth_otp_max_attempts,
            otp_send_limit=settings.auth_otp_send_limit,
            otp_send_window_seconds=settings.auth_otp_send_window_seconds,
            otp_verify_limit=settings.auth_otp_verify_limit,
            otp_verify_window_seconds=settings.auth_otp_verify_window_seconds,
            oauth_attempt_limit=settings.auth_oauth_attempt_limit,
            oauth_attempt_window_seconds=settings.auth_oauth_attempt_window_seconds,
            session_ttl_seconds=settings.auth_session_ttl_seconds,
            session_absolute_ttl_seconds=settings.auth_session_absolute_ttl_seconds,
            session_inactivity_seconds=settings.auth_session_inactivity_seconds,
        ),
        enabled_providers=frozenset(enabled),
    )


def run() -> None:
    """Start the ASGI server using deployment configuration."""
    settings = get_settings()
    uvicorn.run(
        "clearinghouse.main:create_app",
        factory=True,
        host=settings.http_host,
        port=settings.http_port,
    )
