from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from clearinghouse.infrastructure.simple_config import CoreSettings
from clearinghouse.simple_main import _discovery_urls, create_app


@pytest.mark.parametrize(
    "values",
    (
        {"public_url": "ftp://invalid.example"},
        {"discovery_urls": "relative"},
        {"auth_google_enabled": True},
        {"auth_github_client_secret": SecretStr("partial")},
        {"lv2v_offers_file": Path("/does/not/exist")},
        {"environment": "production"},
        {
            "environment": "production",
            "auth_pepper": SecretStr("a" * 40),
            "workload_pepper": SecretStr("b" * 40),
            "signer_webhook_secret": SecretStr("c" * 40),
            "public_url": "http://app.example.com",
            "cookie_secure": True,
        },
        {
            "environment": "production",
            "auth_pepper": SecretStr("a" * 40),
            "workload_pepper": SecretStr("b" * 40),
            "signer_webhook_secret": SecretStr("c" * 40),
            "public_url": "https://app.example.com",
            "cookie_secure": True,
        },
        {
            "environment": "production",
            "auth_pepper": SecretStr("a" * 40),
            "workload_pepper": SecretStr("b" * 40),
            "signer_webhook_secret": SecretStr("c" * 40),
            "auth_resend_api_key": SecretStr("re_configured"),
            "public_url": "https://app.example.com",
            "allowed_origins": "http://app.example.com",
            "cookie_secure": True,
        },
    ),
)
def test_invalid_core_configuration_fails_closed(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CoreSettings.model_validate(values)


def test_configuration_normalizes_lists_and_static_discovery() -> None:
    settings = CoreSettings(
        _env_file=None,
        allowed_origins="https://one.example/, https://two.example",
        discovery_urls="https://one.example/discovery, https://two.example/discovery",
        auth_google_enabled=True,
        auth_google_client_id="id",
        auth_google_client_secret=SecretStr("secret"),
    )
    assert settings.origin_values == {"https://one.example", "https://two.example"}
    assert len(settings.discovery_url_values) == 2
    assert _discovery_urls(settings) == settings.discovery_url_values
    assert settings.enabled_oauth == {"google"}


def test_development_defaults_accept_both_local_browser_origins() -> None:
    settings = CoreSettings(_env_file=None, lv2v_offers_file="")

    assert settings.public_url == "http://localhost:8080"
    assert settings.origin_values == {
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    }
    assert settings.discovery_ttl_seconds == 3600
    assert settings.lv2v_offers_file is None


@pytest.mark.asyncio
async def test_composition_lifespan_health_and_cache_headers(tmp_path: Path) -> None:
    class Sender:
        async def send_code(self, email: str, code: str, expires_in_minutes: int) -> None:
            del email, code, expires_in_minutes

    class Discovery:
        async def discover(self, capability, model):  # type: ignore[no-untyped-def]
            del capability, model
            return []

    settings = CoreSettings(_env_file=None, database_path=tmp_path / "app.db")
    app = create_app(settings, sender=Sender(), discovery_provider=Discovery(), consume_kafka=False)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/health/live")).json() == {"status": "ok"}
        ready = await client.get("/health/ready")
        assert ready.json() == {"status": "ready"}
        assert (await client.get("/v1/discovery")).headers["cache-control"] == "no-store"

    unavailable = MagicMock()
    unavailable.initialize = AsyncMock()
    unavailable.close = AsyncMock()
    unavailable.readiness = AsyncMock(return_value=False)
    app = create_app(
        settings,
        store=unavailable,
        sender=Sender(),
        discovery_provider=Discovery(),
        consume_kafka=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/health/ready")).status_code == 503
