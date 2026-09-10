"""Authentication composition-root tests."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from clearinghouse.domain.auth import AuthProvider
from clearinghouse.infrastructure.config import Settings
from clearinghouse.infrastructure.database import PostgresStore
from clearinghouse.main import _create_auth_service


@pytest.mark.asyncio
async def test_auth_composition_enables_only_fully_configured_providers() -> None:
    default_settings = Settings(environment="test", _env_file=None)
    default_store = PostgresStore.from_settings(default_settings)
    default_service = _create_auth_service(default_settings, default_store)
    assert default_service.enabled_providers == (AuthProvider.EMAIL.value,)
    await default_store.close()

    enabled_settings = Settings(
        environment="test",
        auth_google_enabled=True,
        auth_google_client_id="google-client",
        auth_google_client_secret=SecretStr("google-secret"),
        auth_google_redirect_uri="https://api.example.test/google/callback",
        auth_github_enabled=True,
        auth_github_client_id="github-client",
        auth_github_client_secret=SecretStr("github-secret"),
        auth_github_redirect_uri="https://api.example.test/github/callback",
        _env_file=None,
    )
    enabled_store = PostgresStore.from_settings(enabled_settings)
    enabled_service = _create_auth_service(enabled_settings, enabled_store)
    assert enabled_service.enabled_providers == ("email", "google", "github")
    await enabled_store.close()
