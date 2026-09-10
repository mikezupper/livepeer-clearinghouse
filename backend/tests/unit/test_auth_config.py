"""Authentication configuration fail-closed tests."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from clearinghouse.infrastructure.config import Settings


def production_values() -> dict[str, object]:
    return {
        "environment": "production",
        "auth_pepper": SecretStr("a" * 32),
        "credential_pepper": SecretStr("b" * 32),
        "signer_webhook_secret": SecretStr("0123456789abcdefghijklmnopqrstuv"),
        "signer_session_pepper": SecretStr("zyxwvutsrqponmlkjihgfedcba987654"),
        "signer_url": "https://signer.example.org",
        "signer_discovery_url": "https://signer.example.org",
        "auth_resend_api_key": SecretStr("re_" + "c" * 32),
        "auth_resend_from": "auth@livepeer.org",
        "auth_allowed_origins": "https://admin.example.org,https://app.example.org/",
        "auth_success_redirect_url": "https://app.example.org/signed-in",
    }


def test_complete_optional_providers_and_origin_normalization() -> None:
    values = production_values()
    values.update(
        {
            "auth_google_enabled": True,
            "auth_google_client_id": "google-client",
            "auth_google_client_secret": SecretStr("google-secret"),
            "auth_google_redirect_uri": "https://api.example.org/v1/auth/oauth/google/callback",
            "auth_github_enabled": True,
            "auth_github_client_id": "github-client",
            "auth_github_client_secret": SecretStr("github-secret"),
            "auth_github_redirect_uri": "https://api.example.org/v1/auth/oauth/github/callback",
        }
    )
    settings = Settings.model_validate(values)
    assert settings.auth_origins == frozenset(
        {"https://admin.example.org", "https://app.example.org"}
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_google_enabled": True},
        {"auth_github_client_id": "partial"},
        {"auth_session_ttl_seconds": 7200, "auth_session_absolute_ttl_seconds": 3600},
        {"auth_allowed_origins": ""},
        {"auth_allowed_origins": "https://app.example.org/path"},
        {"auth_resend_api_url": "ftp://resend.example.org"},
        {"auth_success_redirect_url": "https://user:secret@app.example.org"},
    ],
)
def test_invalid_auth_configuration_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_pepper": SecretStr("short")},
        {"credential_pepper": SecretStr("short")},
        {"auth_resend_api_key": SecretStr("re_replace_me")},
        {"auth_resend_from": "auth@your-domain.example"},
        {"auth_cookie_secure": False},
        {"auth_resend_api_url": "http://resend.example.org"},
        {"auth_success_redirect_url": "http://app.example.org"},
        {"auth_allowed_origins": "http://app.example.org"},
    ],
)
def test_production_auth_configuration_is_strict(overrides: dict[str, object]) -> None:
    values = production_values()
    values.update(overrides)
    with pytest.raises(ValidationError):
        Settings.model_validate(values)
