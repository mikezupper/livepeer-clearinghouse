"""Persistence contract for the deliberately small clearinghouse core."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from clearinghouse.application.pagination import KeysetPage
from clearinghouse.domain.core import (
    AccountId,
    AuthorizationId,
    CapabilityOffer,
    CredentialId,
    PaymentAuthorization,
    UsageEvent,
    UserId,
    Workload,
    WorkloadId,
)


@dataclass(frozen=True, slots=True)
class AccessIdentity:
    """Authenticated user and their directly accessible personal account."""

    user_id: UserId
    account_id: AccountId
    email: str
    is_admin: bool


@dataclass(frozen=True, slots=True)
class StoredChallenge:
    id: str
    email: str
    code_digest: bytes
    expires_at: datetime
    attempts: int


@dataclass(frozen=True, slots=True)
class StoredSession:
    id: str
    token_digest: bytes
    csrf_digest: bytes
    identity: AccessIdentity
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StoredCredential:
    id: CredentialId
    account_id: AccountId
    user_id: UserId
    name: str
    token_digest: bytes
    created_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class StoredOAuthFlow:
    provider: str
    state_digest: bytes
    verifier: str
    nonce_digest: bytes | None
    redirect_uri: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GlobalStop:
    enabled: bool
    reason: str
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class UsageAggregate:
    workload_id: WorkloadId
    measured_quantity: int
    measured_unit: str | None
    computed_fee: int
    event_count: int


@dataclass(frozen=True, slots=True)
class AccountSummary:
    offers: int
    credentials: int
    workloads: int
    active_workloads: int
    usage_events: int
    computed_fee: int


@dataclass(frozen=True, slots=True)
class AdminSummary:
    users: int
    workloads: int
    active_workloads: int
    usage_events: int
    unmatched_usage: int
    computed_fee: int


class CoreTransaction(Protocol):
    """Atomic mutations required by the core application."""

    async def put_challenge(self, challenge: StoredChallenge) -> None: ...
    async def get_challenge(self, challenge_id: str) -> StoredChallenge | None: ...
    async def increment_challenge_attempts(self, challenge_id: str) -> None: ...
    async def delete_challenge(self, challenge_id: str) -> None: ...
    async def resolve_identity(self, email: str, *, admin_email: str | None) -> AccessIdentity: ...
    async def list_identities(
        self, *, limit: int = 50, after: tuple[str, ...] = ()
    ) -> KeysetPage[AccessIdentity]: ...
    async def put_session(self, session: StoredSession) -> None: ...
    async def get_session(self, token_digest: bytes) -> StoredSession | None: ...
    async def delete_session(self, token_digest: bytes) -> None: ...
    async def put_oauth_flow(self, flow: StoredOAuthFlow) -> None: ...
    async def pop_oauth_flow(
        self, provider: str, state_digest: bytes, *, now: datetime
    ) -> StoredOAuthFlow | None: ...
    async def put_credential(self, credential: StoredCredential) -> None: ...
    async def get_credential(self, token_digest: bytes) -> StoredCredential | None: ...
    async def list_credentials(
        self, account_id: AccountId, *, limit: int = 50, after: tuple[str, ...] = ()
    ) -> KeysetPage[StoredCredential]: ...
    async def revoke_credential(
        self, account_id: AccountId, credential_id: CredentialId, at: datetime
    ) -> bool: ...
    async def replace_offers(self, offers: Sequence[CapabilityOffer]) -> None: ...
    async def list_offers(
        self,
        *,
        now: datetime,
        limit: int = 50,
        after: tuple[str, ...] = (),
        capability: str | None = None,
        model: str | None = None,
    ) -> KeysetPage[CapabilityOffer]: ...
    async def offer_generation(self) -> str: ...
    async def get_offer(self, offer_id: str, *, now: datetime) -> CapabilityOffer | None: ...
    async def put_workload(self, workload: Workload, token_digest: bytes) -> None: ...
    async def get_workload(self, workload_id: WorkloadId) -> Workload | None: ...
    async def get_workload_by_token(self, token_digest: bytes) -> Workload | None: ...
    async def list_workloads(
        self,
        account_id: AccountId | None = None,
        *,
        limit: int = 50,
        after: tuple[str, ...] = (),
    ) -> KeysetPage[Workload]: ...
    async def usage_aggregates(
        self, workload_ids: Sequence[WorkloadId]
    ) -> Sequence[UsageAggregate]: ...
    async def revoke_workload(self, workload_id: WorkloadId, *, at: datetime) -> bool: ...
    async def bind_workload(
        self,
        workload_id: WorkloadId,
        *,
        state_id: str,
        manifest_id: str | None,
        payment_session_id: str | None,
    ) -> None: ...
    async def put_authorization(self, authorization: PaymentAuthorization) -> bool: ...
    async def get_authorization(
        self, authorization_id: AuthorizationId
    ) -> PaymentAuthorization | None: ...
    async def put_usage(self, usage: UsageEvent) -> bool: ...
    async def list_usage(
        self,
        account_id: AccountId | None = None,
        *,
        limit: int = 50,
        after: tuple[str, ...] = (),
    ) -> KeysetPage[UsageEvent]: ...
    async def account_summary(self, account_id: AccountId, *, now: datetime) -> AccountSummary: ...
    async def admin_summary(self, *, now: datetime) -> AdminSummary: ...
    async def get_global_stop(self) -> GlobalStop: ...
    async def set_global_stop(self, value: GlobalStop) -> None: ...


class CoreStore(Protocol):
    """Replaceable storage boundary; SQLite is the bundled implementation."""

    def transaction(self) -> AbstractAsyncContextManager[CoreTransaction]: ...
    async def initialize(self) -> None: ...
    async def readiness(self) -> bool: ...
    async def close(self) -> None: ...
