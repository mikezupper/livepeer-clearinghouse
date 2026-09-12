"""Ports required by authentication workflows."""

from __future__ import annotations

from typing import Protocol

from clearinghouse.domain.auth import AuthProvider, OAuthIdentity, OAuthTransaction


class OAuthProviderClient(Protocol):
    """Standards-aware OAuth/OIDC client boundary."""

    def authorization_url(
        self,
        provider: AuthProvider,
        *,
        state: str,
        code_challenge: str,
        nonce: str | None,
        redirect_uri: str,
    ) -> str: ...

    async def exchange(
        self,
        transaction: OAuthTransaction,
        *,
        code: str,
    ) -> OAuthIdentity: ...
