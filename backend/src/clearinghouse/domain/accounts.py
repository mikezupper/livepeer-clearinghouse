"""Account, credential, catalog, and ledger domain values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import NewType

TenantId = NewType("TenantId", str)
AccountId = NewType("AccountId", str)
PrincipalId = NewType("PrincipalId", str)


class Status(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Role(StrEnum):
    OPERATOR = "operator"
    TENANT_ADMIN = "tenant_admin"
    CREDENTIAL_HOLDER = "credential_holder"


class GrantKind(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"
    ADJUSTMENT = "adjustment"


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    """Authenticated actor produced by the identity adapter."""

    id: PrincipalId
    roles: frozenset[Role]
    tenant_id: TenantId | None = None
    account_id: AccountId | None = None

    @property
    def is_operator(self) -> bool:
        return Role.OPERATOR in self.roles

    def can_access_tenant(self, tenant_id: str) -> bool:
        return self.is_operator or self.tenant_id == tenant_id

    def can_access_account(self, tenant_id: str, account_id: str) -> bool:
        if self.is_operator:
            return True
        return self.tenant_id == tenant_id and (
            Role.TENANT_ADMIN in self.roles or self.account_id == account_id
        )


@dataclass(frozen=True, slots=True)
class Tenant:
    id: str
    display_name: str
    status: Status
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    tenant_id: str
    display_name: str
    unit: str
    exposure_cap: int
    status: Status
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Principal:
    id: str
    tenant_id: str | None
    account_id: str | None
    display_name: str | None
    status: Status
    roles: frozenset[Role]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Credential:
    id: str
    account_id: str
    principal_id: str
    prefix: str
    label: str
    status: str
    created_at: datetime
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    credential: Credential
    secret: str


@dataclass(frozen=True, slots=True)
class Grant:
    id: str
    account_id: str
    kind: GrantKind
    amount: int
    unit: str
    reason: str
    actor_id: str
    created_at: datetime
    external_reference: str | None = None


@dataclass(frozen=True, slots=True)
class Balance:
    account_id: str
    posted: int
    open_lease_exposure: int
    available: int
    unit: str


@dataclass(frozen=True, slots=True)
class ExactRate:
    numerator: int
    denominator: int
    charge_unit: str
    quantity_unit: str

    def __post_init__(self) -> None:
        if self.numerator < 0 or self.denominator <= 0:
            raise ValueError("rate must be a non-negative exact ratio")


@dataclass(frozen=True, slots=True)
class RateCard:
    id: str
    version: int
    capability: str
    model: str | None
    rate: ExactRate
    effective_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    capability: str
    model: str | None
    rate: ExactRate
    available: bool


def utc_now() -> datetime:
    return datetime.now(UTC)


def grant_delta(kind: GrantKind, amount: int) -> int:
    """Convert an API grant amount to its payer-ledger signed amount."""
    if kind is GrantKind.CREDIT:
        if amount <= 0:
            raise ValueError("credit amount must be positive")
        return amount
    if kind is GrantKind.DEBIT:
        if amount <= 0:
            raise ValueError("debit amount must be positive")
        return -amount
    if amount == 0:
        raise ValueError("adjustment amount must be non-zero")
    return amount
