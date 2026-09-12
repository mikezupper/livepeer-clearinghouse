from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
import resend
from joserfc import jwk, jwt
from resend.exceptions import ResendError

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

NOW = datetime(2026, 9, 11, tzinfo=UTC)


def client() -> AuthlibOAuthProviderClient:
    return AuthlibOAuthProviderClient(
        OAuthClientConfig("google-id", "google-secret", "github-id", "github-secret")
    )


def flow(provider: AuthProvider) -> OAuthTransaction:
    return OAuthTransaction(
        "flow",
        provider,
        "state",
        "verifier",
        "nonce" if provider is AuthProvider.GOOGLE else None,
        "https://app.example/callback",
        NOW,
    )


def test_oauth_authorization_urls_and_configuration() -> None:
    google = client().authorization_url(
        AuthProvider.GOOGLE,
        state="state",
        code_challenge="challenge",
        nonce="nonce",
        redirect_uri="https://app.example/callback",
    )
    github = client().authorization_url(
        AuthProvider.GITHUB,
        state="state",
        code_challenge="challenge",
        nonce=None,
        redirect_uri="https://app.example/callback",
    )
    assert google.startswith(oauth_module.GOOGLE_AUTH_URL)
    assert "code_challenge=challenge" in google and "nonce=nonce" in google
    assert github.startswith(oauth_module.GITHUB_AUTH_URL)
    with pytest.raises(InvalidAuthentication):
        client().authorization_url(
            AuthProvider.EMAIL,
            state="s",
            code_challenge="c",
            nonce=None,
            redirect_uri="https://app.example/callback",
        )
    with pytest.raises(InvalidAuthentication):
        AuthlibOAuthProviderClient(OAuthClientConfig()).authorization_url(
            AuthProvider.GOOGLE,
            state="s",
            code_challenge="c",
            nonce=None,
            redirect_uri="https://app.example/callback",
        )


@pytest.mark.asyncio
async def test_oauth_exchange_maps_success_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = AsyncMock()
    inner.fetch_token.return_value = {"id_token": "signed"}
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=inner)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(oauth_module, "AsyncOAuth2Client", MagicMock(return_value=context))
    oauth = client()
    expected = OAuthIdentity(AuthProvider.GOOGLE, "subject", "user@example.com", "nonce")
    monkeypatch.setattr(oauth, "_google_identity", AsyncMock(return_value=expected))
    assert await oauth.exchange(flow(AuthProvider.GOOGLE), code="code") is expected

    github = OAuthIdentity(AuthProvider.GITHUB, "42", "user@example.com")
    monkeypatch.setattr(oauth, "_github_identity", AsyncMock(return_value=github))
    assert await oauth.exchange(flow(AuthProvider.GITHUB), code="code") is github

    inner.fetch_token.side_effect = httpx2.ConnectError("offline")
    with pytest.raises(AuthenticationUnavailable):
        await oauth.exchange(flow(AuthProvider.GITHUB), code="code")
    inner.fetch_token.side_effect = ValueError("invalid")
    with pytest.raises(InvalidAuthentication):
        await oauth.exchange(flow(AuthProvider.GITHUB), code="code")


@pytest.mark.asyncio
async def test_provider_identity_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock()
    response.json.return_value = {"keys": []}
    response.raise_for_status.return_value = None
    transport = MagicMock()
    transport.__aenter__ = AsyncMock(
        return_value=SimpleNamespace(get=AsyncMock(return_value=response))
    )
    transport.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(httpx2, "AsyncClient", MagicMock(return_value=transport))
    monkeypatch.setattr(jwk.KeySet, "import_key_set", MagicMock(return_value=cast(Any, object())))
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "google-id",
        "sub": "subject",
        "nonce": "nonce",
        "email": "user@example.com",
        "email_verified": True,
        "exp": 4_000_000_000,
        "iat": 1,
    }
    monkeypatch.setattr(jwt, "decode", MagicMock(return_value=SimpleNamespace(claims=claims)))
    validator = MagicMock()
    monkeypatch.setattr(jwt, "JWTClaimsRegistry", MagicMock(return_value=validator))
    assert await client()._google_identity({"id_token": "signed"}, "google-id") == OAuthIdentity(
        AuthProvider.GOOGLE, "subject", "user@example.com", "nonce"
    )
    with pytest.raises(InvalidAuthentication):
        await client()._google_identity({}, "google-id")

    user = MagicMock()
    user.json.return_value = {"id": 42, "email": None}
    email = MagicMock()
    email.json.return_value = [{"email": "user@example.com", "primary": True, "verified": True}]
    github_client = AsyncMock()
    github_client.get.side_effect = [user, email]
    assert (await client()._github_identity(github_client)).email == "user@example.com"
    user.json.return_value = {}
    github_client.get.side_effect = [user]
    with pytest.raises(InvalidAuthentication):
        await client()._github_identity(github_client)


@pytest.mark.asyncio
async def test_resend_custom_url_and_failure_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    send = AsyncMock(return_value={"id": "email-id"})
    monkeypatch.setattr(resend.Emails, "send_async", send)
    sender = ResendEmailCodeSender(
        api_key="resend-key",
        api_url="https://resend.example/base/",
        from_address="auth@example.com",
    )
    assert resend.api_url == "https://resend.example/base"
    await sender.send_code("user@example.com", "123456", 10)
    assert send.await_args is not None
    assert "123456" in send.await_args.args[0]["text"]
    send.side_effect = ResendError(500, "offline", "server_error", "retry")
    with pytest.raises(AuthenticationUnavailable):
        await sender.send_code("user@example.com", "123456", 10)
