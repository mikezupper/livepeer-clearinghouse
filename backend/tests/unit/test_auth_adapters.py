"""Tests for external authentication adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
import resend
from joserfc import jwk, jwt
from joserfc.errors import JoseError

from clearinghouse.domain.auth import (
    AuthenticationUnavailable,
    AuthProvider,
    InvalidAuthentication,
    OAuthIdentity,
    OAuthTransaction,
)
from clearinghouse.infrastructure import oauth as oauth_module
from clearinghouse.infrastructure.oauth import AuthlibOAuthProviderClient, OAuthClientConfig
from clearinghouse.infrastructure.resend_email import ResendEmailCodeSender

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def oauth_client() -> AuthlibOAuthProviderClient:
    return AuthlibOAuthProviderClient(
        OAuthClientConfig(
            google_client_id="google-client",
            google_client_secret="google-secret",  # noqa: S106
            github_client_id="github-client",
            github_client_secret="github-secret",  # noqa: S106
        )
    )


def transaction(provider: AuthProvider) -> OAuthTransaction:
    return OAuthTransaction(
        "oauth_abcdefgh",
        provider,
        "state-hash",
        "verifier",
        "nonce-hash" if provider is AuthProvider.GOOGLE else None,
        "https://app.example/callback",
        NOW,
    )


def test_authorization_urls_enforce_provider_configuration() -> None:
    client = oauth_client()
    google = client.authorization_url(
        AuthProvider.GOOGLE,
        state="state",
        code_challenge="challenge",
        nonce="nonce",
        redirect_uri="https://app.example/callback",
    )
    assert google.startswith(oauth_module.GOOGLE_AUTH_URL)
    assert "code_challenge=challenge" in google
    assert "nonce=nonce" in google
    github = client.authorization_url(
        AuthProvider.GITHUB,
        state="state",
        code_challenge="challenge",
        nonce=None,
        redirect_uri="https://app.example/callback",
    )
    assert github.startswith(oauth_module.GITHUB_AUTH_URL)
    with pytest.raises(InvalidAuthentication):
        client.authorization_url(
            AuthProvider.EMAIL,
            state="state",
            code_challenge="challenge",
            nonce=None,
            redirect_uri="https://app.example/callback",
        )
    with pytest.raises(InvalidAuthentication):
        AuthlibOAuthProviderClient(OAuthClientConfig()).authorization_url(
            AuthProvider.GOOGLE,
            state="state",
            code_challenge="challenge",
            nonce="nonce",
            redirect_uri="https://app.example/callback",
        )


@pytest.mark.asyncio
async def test_exchange_uses_pkce_and_maps_google_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = AsyncMock()
    inner.fetch_token.return_value = {"id_token": "signed"}
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=inner)
    context.__aexit__ = AsyncMock(return_value=None)
    constructor = MagicMock(return_value=context)
    monkeypatch.setattr(oauth_module, "AsyncOAuth2Client", constructor)
    client = oauth_client()
    expected = OAuthIdentity(AuthProvider.GOOGLE, "subject", "user@example.com", "nonce")
    google_identity = AsyncMock(return_value=expected)
    monkeypatch.setattr(client, "_google_identity", google_identity)
    result = await client.exchange(transaction(AuthProvider.GOOGLE), code="provider-code")
    assert result is expected
    inner.fetch_token.assert_awaited_once_with(
        oauth_module.GOOGLE_TOKEN_URL, code="provider-code", code_verifier="verifier"
    )


@pytest.mark.asyncio
async def test_exchange_maps_provider_and_validation_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = AsyncMock()
    inner.fetch_token.side_effect = httpx2.ConnectError("offline")
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=inner)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(oauth_module, "AsyncOAuth2Client", MagicMock(return_value=context))
    with pytest.raises(AuthenticationUnavailable):
        await oauth_client().exchange(transaction(AuthProvider.GITHUB), code="code")
    inner.fetch_token.side_effect = ValueError("invalid")
    with pytest.raises(InvalidAuthentication):
        await oauth_client().exchange(transaction(AuthProvider.GITHUB), code="code")
    inner.fetch_token.side_effect = None
    inner.fetch_token.return_value = {"id_token": "signed"}
    client = oauth_client()
    monkeypatch.setattr(client, "_google_identity", AsyncMock(side_effect=JoseError("bad jwt")))
    with pytest.raises(InvalidAuthentication):
        await client.exchange(transaction(AuthProvider.GOOGLE), code="code")


@pytest.mark.asyncio
async def test_google_identity_validates_claims_and_verified_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = MagicMock()
    response.json.return_value = {"keys": []}
    response.raise_for_status.return_value = None
    http_client = MagicMock()
    http_client.__aenter__ = AsyncMock(
        return_value=SimpleNamespace(get=AsyncMock(return_value=response))
    )
    http_client.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(httpx2, "AsyncClient", MagicMock(return_value=http_client))
    monkeypatch.setattr(
        jwk.KeySet,
        "import_key_set",
        MagicMock(return_value=cast(Any, object())),
    )
    decoded = SimpleNamespace(
        claims={
            "iss": "https://accounts.google.com",
            "aud": "google-client",
            "sub": "subject",
            "nonce": "nonce",
            "email": "user@example.com",
            "email_verified": True,
            "exp": 4_000_000_000,
            "iat": 1,
        }
    )
    monkeypatch.setattr(jwt, "decode", MagicMock(return_value=decoded))
    validator = MagicMock()
    registry = MagicMock(return_value=validator)
    monkeypatch.setattr(jwt, "JWTClaimsRegistry", registry)
    identity = await oauth_client()._google_identity({"id_token": "signed"}, "google-client")
    assert identity == OAuthIdentity(AuthProvider.GOOGLE, "subject", "user@example.com", "nonce")
    validator.validate.assert_called_once_with(decoded.claims)
    with pytest.raises(InvalidAuthentication):
        await oauth_client()._google_identity({}, "google-client")


@pytest.mark.asyncio
async def test_github_identity_uses_verified_primary_email() -> None:
    user_response = MagicMock()
    user_response.json.return_value = {"id": 123, "email": None}
    email_response = MagicMock()
    email_response.json.return_value = [
        {"email": "other@example.com", "primary": False, "verified": True},
        {"email": "primary@example.com", "primary": True, "verified": True},
    ]
    client = AsyncMock()
    client.get.side_effect = [user_response, email_response]
    identity = await oauth_client()._github_identity(client)
    assert identity == OAuthIdentity(AuthProvider.GITHUB, "123", "primary@example.com")
    user_response.json.return_value = {}
    client.get.side_effect = [user_response]
    with pytest.raises(InvalidAuthentication):
        await oauth_client()._github_identity(client)

    user_response.json.return_value = {"id": 456, "email": "public@example.com"}
    client.get.side_effect = [user_response]
    direct = await oauth_client()._github_identity(client)
    assert direct.email == "public@example.com"

    user_response.json.return_value = {"id": 789, "email": None}
    email_response.json.return_value = [{"email": "unverified@example.com", "verified": False}]
    client.get.side_effect = [user_response, email_response]
    no_verified_email = await oauth_client()._github_identity(client)
    assert no_verified_email.email is None


@pytest.mark.asyncio
async def test_resend_sdk_uses_custom_url_and_maps_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = AsyncMock(return_value={"id": "email-id"})
    monkeypatch.setattr(resend.Emails, "send_async", send)
    sender = ResendEmailCodeSender(
        api_key="resend-key",  # noqa: S106
        api_url="https://resend.example.test/base/",
        from_address="auth@example.test",
    )
    assert resend.api_url == "https://resend.example.test/base"
    await sender.send_code("person@example.com", "123456", 10)
    assert send.await_args is not None
    payload = send.await_args.args[0]
    assert payload["to"] == ["person@example.com"]
    assert "123456" in payload["text"]

    from resend.exceptions import ResendError

    send.side_effect = ResendError(500, "offline", "server_error", "retry later")
    with pytest.raises(AuthenticationUnavailable):
        await sender.send_code("person@example.com", "123456", 10)


def test_jose_errors_are_classified_as_invalid() -> None:
    assert issubclass(JoseError, Exception)
