"""Explicit verified-identity linking and zero-operator bootstrap workflows."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from clearinghouse.domain.accounts import PrincipalContext, Role
from clearinghouse.domain.onboarding import (
    IdentityInvitation,
    IdentityLink,
    IssuedIdentityInvitation,
    OnboardingForbidden,
    OperatorBootstrap,
)


class OnboardingRepository(Protocol):
    """Atomic persistence capabilities for security-sensitive onboarding."""

    async def create_identity_invitation(
        self,
        *,
        invitation_id: str,
        source_principal_id: str,
        principal_id: str,
        secret_prefix: str,
        secret_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
        reason: str,
        actor: PrincipalContext,
    ) -> IdentityInvitation: ...

    async def redeem_identity_invitation(
        self,
        *,
        secret_prefix: str,
        secret_hash: bytes,
        now: datetime,
        actor: PrincipalContext,
    ) -> IdentityLink: ...

    async def bootstrap_operator(
        self, *, normalized_email: str, configuration_hash: str
    ) -> OperatorBootstrap: ...


class OnboardingService:
    """Issue invitations, link verified identities, and bootstrap once."""

    def __init__(
        self,
        repository: OnboardingRepository,
        pepper: str,
        *,
        invitation_ttl_seconds: int = 86_400,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(pepper) < 16:
            raise ValueError("onboarding pepper must be at least 16 characters")
        if not 300 <= invitation_ttl_seconds <= 604_800:
            raise ValueError("invitation lifetime must be between five minutes and seven days")
        self._repository = repository
        self._pepper = pepper.encode()
        self._invitation_ttl_seconds = invitation_ttl_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def _digest(self, purpose: str, value: str) -> bytes:
        return hmac.new(self._pepper, f"{purpose}\0{value}".encode(), hashlib.sha256).digest()

    async def issue_invitation(
        self,
        source_principal_id: str,
        principal_id: str,
        reason: str,
        actor: PrincipalContext,
    ) -> IssuedIdentityInvitation:
        """Return a high-entropy invitation secret exactly once."""
        if not (actor.is_operator or Role.TENANT_ADMIN in actor.roles):
            raise OnboardingForbidden("principal invitation administration denied")
        secret = f"och_inv_{secrets.token_urlsafe(32)}"
        now = self._clock()
        invitation = await self._repository.create_identity_invitation(
            invitation_id=f"invite_{secrets.token_urlsafe(18)}",
            source_principal_id=source_principal_id,
            principal_id=principal_id,
            secret_prefix=secret[:20],
            secret_hash=self._digest("identity-invitation", secret),
            created_at=now,
            expires_at=now + timedelta(seconds=self._invitation_ttl_seconds),
            reason=reason,
            actor=actor,
        )
        return IssuedIdentityInvitation(invitation, secret)

    async def redeem_invitation(self, secret: str, actor: PrincipalContext) -> IdentityLink:
        """Link the actor's one verified identity and revoke placeholder sessions."""
        if actor.roles or actor.tenant_id is not None or actor.account_id is not None:
            raise OnboardingForbidden("only an unscoped identity may redeem an invitation")
        return await self._repository.redeem_identity_invitation(
            secret_prefix=secret[:20],
            secret_hash=self._digest("identity-invitation", secret),
            now=self._clock(),
            actor=actor,
        )

    async def bootstrap_operator(
        self, normalized_email: str, bootstrap_secret: str
    ) -> OperatorBootstrap:
        """Apply the configured bootstrap without retaining either config secret."""
        if len(bootstrap_secret) < 32:
            raise ValueError("operator bootstrap secret must be at least 32 characters")
        fingerprint = hmac.new(
            bootstrap_secret.encode(),
            f"operator-bootstrap\0{normalized_email}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return await self._repository.bootstrap_operator(
            normalized_email=normalized_email,
            configuration_hash=fingerprint,
        )
