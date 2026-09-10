"""Signer-session and synchronous reservation workflows."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from clearinghouse.domain.accounts import PrincipalContext, Role
from clearinghouse.domain.signer import Admission, PaymentState, SignerSession


class SignerConflict(ValueError):
    """A safe retry cannot reproduce a one-time signer-session secret."""


class SignerRepository(Protocol):
    signer_id: str

    async def initialize(self) -> None: ...
    async def create_session(
        self,
        *,
        principal: PrincipalContext,
        credential_id: str | None,
        capability: str,
        model: str | None,
        app: str,
        operation_key: str,
        request_hash: str,
        requested_cap: int,
        unit: str,
        ttl_seconds: int,
        token_hash: bytes,
        now: datetime,
    ) -> SignerSession: ...
    async def list_sessions(self, principal: PrincipalContext) -> Sequence[SignerSession]: ...
    async def list_leases(self, principal: PrincipalContext) -> Sequence[SignerSession]: ...
    async def revoke_session(
        self, principal: PrincipalContext, session_id: str, now: datetime
    ) -> bool: ...
    async def authorize(
        self, *, bearer_hash: bytes, signer_id: str, state: PaymentState, now: datetime
    ) -> Admission: ...
    async def refresh_session(
        self,
        *,
        principal: PrincipalContext,
        session_id: str,
        requested_cap: int | None,
        credential_id: str | None,
        operation_key: str,
        request_hash: str,
        token_hash: bytes,
        now: datetime,
    ) -> SignerSession: ...
    async def set_kill_switch(
        self, enabled: bool, reason: str, actor: PrincipalContext, now: datetime
    ) -> dict[str, object]: ...
    async def get_kill_switch(self) -> dict[str, object]: ...


class SignerService:
    def __init__(
        self,
        repository: SignerRepository,
        pepper: str,
        webhook_secret: str,
        *,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if len(pepper) < 16:
            raise ValueError("signer pepper must be at least 16 characters")
        self.repository = repository
        self.pepper = pepper.encode()
        self.webhook_hash = self.digest("webhook", webhook_secret)
        self.signer_id = repository.signer_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(32))

    def digest(self, purpose: str, value: str) -> bytes:
        return hmac.new(self.pepper, f"{purpose}\0{value}".encode(), hashlib.sha256).digest()

    def verify_webhook(self, supplied: str) -> bool:
        return hmac.compare_digest(self.webhook_hash, self.digest("webhook", supplied))

    async def initialize(self) -> None:
        await self.repository.initialize()

    async def create_session(
        self,
        principal: PrincipalContext,
        capability: str,
        model: str | None,
        app: str,
        requested_cap: int,
        unit: str,
        ttl_seconds: int,
        operation_key: str,
        credential_id: str | None = None,
    ) -> SignerSession:
        if principal.tenant_id is None or principal.account_id is None:
            raise ValueError("account-scoped principal required")
        if Role.CREDENTIAL_HOLDER not in principal.roles:
            raise PermissionError("credential holder role required")
        if requested_cap <= 0 or unit != "wei":
            raise ValueError("a positive wei cap is required")
        if not 16 <= len(operation_key) <= 256:
            raise ValueError("Idempotency-Key must be 16 to 256 characters")
        request_hash = hashlib.sha256(
            json.dumps(
                [capability, model, app, requested_cap, unit, ttl_seconds, credential_id],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        token = f"och_ss_{self.token_factory()}"
        value = await self.repository.create_session(
            principal=principal,
            credential_id=credential_id,
            capability=capability,
            model=model,
            app=app,
            operation_key=operation_key,
            request_hash=request_hash,
            requested_cap=requested_cap,
            unit=unit,
            ttl_seconds=ttl_seconds,
            token_hash=self.digest("session", token),
            now=self.clock(),
        )
        return SignerSession(
            value.id, token, value.signer_url, value.discovery_url, value.expires_at, value.lease
        )

    async def authorize(self, bearer: str, signer_id: str, state: PaymentState) -> Admission:
        return await self.repository.authorize(
            bearer_hash=self.digest("session", bearer),
            signer_id=signer_id,
            state=state,
            now=self.clock(),
        )

    async def refresh_session(
        self,
        principal: PrincipalContext,
        session_id: str,
        requested_cap: int | None,
        operation_key: str,
        credential_id: str | None = None,
    ) -> SignerSession:
        if Role.CREDENTIAL_HOLDER not in principal.roles:
            raise PermissionError("credential holder role required")
        if not 16 <= len(operation_key) <= 256:
            raise ValueError("Idempotency-Key must be 16 to 256 characters")
        request_hash = hashlib.sha256(
            json.dumps([session_id, requested_cap, credential_id], separators=(",", ":")).encode()
        ).hexdigest()
        token = f"och_ss_{self.token_factory()}"
        value = await self.repository.refresh_session(
            principal=principal,
            session_id=session_id,
            requested_cap=requested_cap,
            credential_id=credential_id,
            operation_key=operation_key,
            request_hash=request_hash,
            token_hash=self.digest("session", token),
            now=self.clock(),
        )
        return SignerSession(
            value.id, token, value.signer_url, value.discovery_url, value.expires_at, value.lease
        )

    async def list_sessions(self, principal: PrincipalContext) -> Sequence[SignerSession]:
        return await self.repository.list_sessions(principal)

    async def list_leases(self, principal: PrincipalContext) -> Sequence[SignerSession]:
        return await self.repository.list_leases(principal)

    async def revoke_session(self, principal: PrincipalContext, session_id: str) -> None:
        if not await self.repository.revoke_session(principal, session_id, self.clock()):
            raise ValueError("signer session not found or already inactive")

    async def set_kill_switch(
        self, enabled: bool, reason: str, actor: PrincipalContext
    ) -> dict[str, object]:
        if not actor.is_operator:
            raise PermissionError("operator role required")
        return await self.repository.set_kill_switch(enabled, reason, actor, self.clock())

    async def get_kill_switch(self, actor: PrincipalContext) -> dict[str, object]:
        if not actor.is_operator:
            raise PermissionError("operator role required")
        return await self.repository.get_kill_switch()
