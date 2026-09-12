"""Composition root for the simplified Clearinghouse distribution."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from contracts.ports.v2.protocols import NetworkDiscoveryProvider
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from clearinghouse.adapters.http.access import create_access_router
from clearinghouse.adapters.http.core import create_core_router
from clearinghouse.application.access import AccessService, EmailSender
from clearinghouse.application.core_store import CoreStore
from clearinghouse.application.discovery import DiscoveryService
from clearinghouse.application.usage import UsageService
from clearinghouse.application.workloads import WorkloadService
from clearinghouse.infrastructure.consumer import MeteringConsumer
from clearinghouse.infrastructure.discovery import (
    CompositeNetworkDiscovery,
    ConfiguredLv2vDiscovery,
    HttpNetworkDiscovery,
)
from clearinghouse.infrastructure.oauth import AuthlibOAuthProviderClient, OAuthClientConfig
from clearinghouse.infrastructure.resend_email import ResendEmailCodeSender
from clearinghouse.infrastructure.signed_ticket import decode_signed_ticket
from clearinghouse.infrastructure.simple_config import CoreSettings, get_core_settings
from clearinghouse.infrastructure.sqlite import SqliteStore


def _discovery_urls(settings: CoreSettings) -> tuple[str, ...]:
    """Use signer discovery even when the SDK receives a static orchestrator list.

    The static list controls runner selection in livepeer-python-gateway. The
    pinned signer remains the price/capability source used to create immutable
    Clearinghouse offers for those orchestrators.
    """
    return settings.discovery_url_values


def create_app(
    settings: CoreSettings | None = None,
    store: CoreStore | None = None,
    sender: EmailSender | None = None,
    discovery_provider: NetworkDiscoveryProvider | None = None,
    *,
    consume_kafka: bool = True,
) -> FastAPI:
    configured = settings or get_core_settings()
    core_store = store or SqliteStore(
        configured.database_path, busy_timeout_ms=configured.database_busy_timeout_ms
    )
    email_sender = sender or ResendEmailCodeSender(
        api_key=configured.auth_resend_api_key.get_secret_value(),
        api_url=configured.auth_resend_api_url,
        from_address=configured.auth_resend_from,
    )
    oauth = AuthlibOAuthProviderClient(
        OAuthClientConfig(
            configured.auth_google_client_id,
            configured.auth_google_client_secret.get_secret_value()
            if configured.auth_google_client_secret
            else None,
            configured.auth_github_client_id,
            configured.auth_github_client_secret.get_secret_value()
            if configured.auth_github_client_secret
            else None,
        )
    )
    access = AccessService(
        core_store,
        email_sender,
        pepper=configured.auth_pepper.get_secret_value(),
        admin_email=configured.admin_email,
        enabled_oauth=configured.enabled_oauth,
        oauth_client=oauth,
    )
    provider: NetworkDiscoveryProvider
    if discovery_provider is not None:
        provider = discovery_provider
    else:
        providers: list[object] = [HttpNetworkDiscovery(_discovery_urls(configured))]
        if configured.lv2v_offers_file is not None:
            providers.append(ConfiguredLv2vDiscovery(configured.lv2v_offers_file))
        provider = CompositeNetworkDiscovery(providers)
    discovery = DiscoveryService(
        core_store,
        provider,
        observation_ttl_seconds=configured.discovery_ttl_seconds,
    )
    workloads = WorkloadService(
        core_store,
        pepper=configured.workload_pepper.get_secret_value(),
        signer_id=configured.signer_id,
        public_signer_url=configured.public_url,
        public_discovery_url=f"{configured.public_url.rstrip('/')}/v1/discovery",
    )
    usage = UsageService(core_store, signer_id=configured.signer_id)
    consumer = MeteringConsumer(
        usage,
        decode_signed_ticket,
        bootstrap_servers=configured.kafka_bootstrap_servers,
        topic=configured.kafka_metering_topic,
        group_id=configured.kafka_metering_group_id,
        retention_ms=configured.kafka_retention_ms,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await core_store.initialize()
        task = asyncio.create_task(consumer.run()) if consume_kafka else None
        try:
            yield
        finally:
            consumer.stop()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await core_store.close()

    app = FastAPI(
        title="Livepeer Clearinghouse Core API",
        version="0.2.0",
        description="Authentication, Livepeer discovery, workload authorization, and metering.",
        lifespan=lifespan,
    )
    app.state.settings = configured
    app.state.store = core_store
    app.state.access = access
    app.state.discovery = discovery
    app.state.workloads = workloads
    app.state.usage = usage
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(configured.origin_values),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
    )

    @app.middleware("http")
    async def private_cache_control(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        if not await core_store.readiness() or (consume_kafka and not consumer.connected):
            raise HTTPException(503, "core dependencies unavailable")
        return {"status": "ready"}

    app.include_router(create_access_router(access, configured))
    app.include_router(create_core_router(discovery, workloads, usage, configured))
    return app


def run() -> None:
    settings = get_core_settings()
    uvicorn.run(
        "clearinghouse.simple_main:create_app",
        factory=True,
        host=settings.http_host,
        port=settings.http_port,
        log_level=settings.log_level.lower(),
    )
