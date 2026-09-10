"""Ports required by authentication workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from clearinghouse.domain.auth import (
    AuthProvider,
    BrowserSession,
    EmailChallenge,
    OAuthIdentity,
    OAuthTransaction,
    Principal,
)


class AuthRepository(Protocol):
    """Atomic persistence operations required by authentication."""

    async def consume_rate_limit(
        self,
        scope: str,
        key_hash: str,
        *,
        limit: int,
        window_seconds: int,
        now: datetime,
    ) -> int | None: ...

    async def replace_email_challenge(self, challenge: EmailChallenge) -> None: ...

    async def verify_email_challenge(
        self, email_hash: str, code_hash: str, now: datetime
    ) -> bool: ...

    async def resolve_identity(
        self,
        provider: AuthProvider,
        provider_subject: str,
        email: str | None,
    ) -> Principal: ...

    async def save_oauth_transaction(self, transaction: OAuthTransaction) -> None: ...

    async def consume_oauth_transaction(
        self, provider: AuthProvider, state_hash: str, now: datetime
    ) -> OAuthTransaction | None: ...

    async def create_session(self, session: BrowserSession) -> None: ...

    async def use_session(
        self, token_hash: str, now: datetime, inactivity_seconds: int
    ) -> BrowserSession | None: ...

    async def renew_session(
        self,
        token_hash: str,
        *,
        now: datetime,
        inactivity_seconds: int,
        replacement_id: str,
        replacement_token_hash: str,
        replacement_csrf_hash: str,
        ttl_seconds: int,
    ) -> BrowserSession | None: ...

    async def revoke_session(self, session_id: str) -> None: ...


class EmailCodeSender(Protocol):
    """Deliver a one-time sign-in code without owning auth state."""

    async def send_code(self, email: str, code: str, expires_in_minutes: int) -> None: ...


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
