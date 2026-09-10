"""Google OIDC and GitHub OAuth clients built on Authlib."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import httpx2
from authlib.integrations.httpx_client import AsyncOAuth2Client  # type: ignore[import-untyped]
from joserfc import jwk, jwt
from joserfc.errors import JoseError

from clearinghouse.domain.auth import (
    AuthenticationUnavailable,
    AuthProvider,
    InvalidAuthentication,
    OAuthIdentity,
    OAuthTransaction,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 -- public endpoint
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105 -- public endpoint
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


@dataclass(frozen=True, slots=True)
class OAuthClientConfig:
    """Validated optional provider credentials."""

    google_client_id: str | None = None
    google_client_secret: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None


class AuthlibOAuthProviderClient:
    """Run PKCE OAuth and cryptographically validate Google OIDC identity."""

    def __init__(self, config: OAuthClientConfig, *, timeout_seconds: float = 10.0) -> None:
        self._config = config
        self._timeout = timeout_seconds

    def _credentials(self, provider: AuthProvider) -> tuple[str, str]:
        if provider is AuthProvider.GOOGLE:
            pair = (self._config.google_client_id, self._config.google_client_secret)
        elif provider is AuthProvider.GITHUB:
            pair = (self._config.github_client_id, self._config.github_client_secret)
        else:
            raise InvalidAuthentication("unsupported oauth provider")
        if not pair[0] or not pair[1]:
            raise InvalidAuthentication("oauth provider is not configured")
        return pair[0], pair[1]

    def authorization_url(
        self,
        provider: AuthProvider,
        *,
        state: str,
        code_challenge: str,
        nonce: str | None,
        redirect_uri: str,
    ) -> str:
        client_id, _ = self._credentials(provider)
        client = AsyncOAuth2Client(client_id=client_id, redirect_uri=redirect_uri)
        if provider is AuthProvider.GOOGLE:
            url, _ = client.create_authorization_url(
                GOOGLE_AUTH_URL,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method="S256",
                nonce=nonce,
                scope="openid email",
            )
        else:
            url, _ = client.create_authorization_url(
                GITHUB_AUTH_URL,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method="S256",
                scope="read:user user:email",
            )
        return cast(str, url)

    async def exchange(self, transaction: OAuthTransaction, *, code: str) -> OAuthIdentity:
        """Exchange a one-time code and validate the provider's stable subject."""
        client_id, client_secret = self._credentials(transaction.provider)
        token_url = (
            GOOGLE_TOKEN_URL if transaction.provider is AuthProvider.GOOGLE else GITHUB_TOKEN_URL
        )
        try:
            async with AsyncOAuth2Client(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=transaction.redirect_uri,
                timeout=self._timeout,
            ) as client:
                token = await client.fetch_token(
                    token_url,
                    code=code,
                    code_verifier=transaction.pkce_verifier,
                )
                if transaction.provider is AuthProvider.GOOGLE:
                    return await self._google_identity(token, client_id)
                return await self._github_identity(client)
        except (httpx2.HTTPError, JoseError, KeyError, ValueError) as error:
            if isinstance(error, (JoseError, KeyError, ValueError)):
                raise InvalidAuthentication("oauth identity validation failed") from error
            raise AuthenticationUnavailable("oauth provider unavailable") from error

    async def _google_identity(self, token: dict[str, Any], client_id: str) -> OAuthIdentity:
        id_token = token.get("id_token")
        if not isinstance(id_token, str):
            raise InvalidAuthentication("google did not return an id token")
        async with httpx2.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(GOOGLE_JWKS_URL)
            response.raise_for_status()
            jwks = response.json()
        token_value = jwt.decode(
            id_token,
            jwk.KeySet.import_key_set(jwks),
            algorithms=["RS256"],
        )
        claims = token_value.claims
        jwt.JWTClaimsRegistry(
            leeway=30,
            iss={"essential": True, "values": ["https://accounts.google.com"]},
            aud={"essential": True, "value": client_id},
            sub={"essential": True},
            nonce={"essential": True},
            exp={"essential": True},
            iat={"essential": True},
        ).validate(claims)
        email_value = claims.get("email") if claims.get("email_verified") is True else None
        return OAuthIdentity(
            provider=AuthProvider.GOOGLE,
            subject=str(claims["sub"]),
            email=str(email_value) if email_value else None,
            nonce=str(claims["nonce"]),
        )

    async def _github_identity(self, client: AsyncOAuth2Client) -> OAuthIdentity:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "clearinghouse"}
        user_response = await client.get(GITHUB_USER_URL, headers=headers)
        user_response.raise_for_status()
        user = cast(dict[str, Any], user_response.json())
        if "id" not in user:
            raise InvalidAuthentication("github identity has no stable subject")
        email = user.get("email") if isinstance(user.get("email"), str) else None
        if email is None:
            email_response = await client.get(GITHUB_EMAILS_URL, headers=headers)
            email_response.raise_for_status()
            emails = cast(list[dict[str, Any]], email_response.json())
            email = next(
                (
                    str(item["email"])
                    for item in emails
                    if item.get("primary") is True
                    and item.get("verified") is True
                    and isinstance(item.get("email"), str)
                ),
                None,
            )
        return OAuthIdentity(provider=AuthProvider.GITHUB, subject=str(user["id"]), email=email)
