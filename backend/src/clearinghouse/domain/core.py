"""Small, storage-independent domain for signer access and usage metering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType

UserId = NewType("UserId", str)
AccountId = NewType("AccountId", str)
CredentialId = NewType("CredentialId", str)
WorkloadId = NewType("WorkloadId", str)
PriceObservationId = NewType("PriceObservationId", str)
AuthorizationId = NewType("AuthorizationId", str)
UsageEventId = NewType("UsageEventId", str)
MAX_SIGNED_AMOUNT = (1 << 63) - 1


class LifecycleStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class WorkloadStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    ENDED = "ended"
    REVOKED = "revoked"


class UsageStatus(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"


@dataclass(frozen=True, slots=True)
class ExactPrice:
    """Advertised rational price with explicit currency and quantity unit."""

    numerator: int
    denominator: int
    currency: str
    quantity_unit: str

    def __post_init__(self) -> None:
        if self.numerator < 0 or self.denominator <= 0:
            raise ValueError("price must be a non-negative exact ratio")
        if not self.currency or not self.quantity_unit:
            raise ValueError("price currency and quantity unit are required")


@dataclass(frozen=True, slots=True)
class CapabilityOffer:
    id: PriceObservationId
    orchestrator_url: str
    orchestrator_address: str | None
    capability: str
    model: str | None
    constraints: tuple[tuple[str, str], ...]
    price: ExactPrice
    observed_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Workload:
    id: WorkloadId
    account_id: AccountId
    user_id: UserId
    capability: str
    model: str | None
    offer_id: PriceObservationId
    max_price: ExactPrice
    status: WorkloadStatus
    expires_at: datetime
    created_at: datetime
    runner_session_id: str | None = None
    manifest_id: str | None = None
    payment_session_id: str | None = None
    client_reference: str | None = None
    max_spend_wei: int | None = None

    def __post_init__(self) -> None:
        if self.max_spend_wei is not None and not 0 < self.max_spend_wei <= MAX_SIGNED_AMOUNT:
            raise ValueError("maximum workload spend must be a positive signed 64-bit integer")

    def effective_status(self, at: datetime) -> WorkloadStatus:
        """Return the access state at ``at`` without rewriting immutable history."""

        if self.status is WorkloadStatus.ACTIVE and self.expires_at <= at:
            return WorkloadStatus.EXPIRED
        return self.status


@dataclass(frozen=True, slots=True)
class PaymentAuthorization:
    id: AuthorizationId
    workload_id: WorkloadId
    signer_id: str
    state_id: str
    sequence_number: int
    orchestrator_address: str
    advertised_price: ExactPrice
    authorized_at: datetime
    signer_last_update_ns: int | None = None
    authorized_fee: int = 0

    def __post_init__(self) -> None:
        if self.signer_last_update_ns is not None and self.signer_last_update_ns < 0:
            raise ValueError("signer timestamp must be non-negative")
        if self.authorized_fee < 0:
            raise ValueError("authorized fee must be non-negative")


@dataclass(frozen=True, slots=True)
class UsageEvent:
    id: UsageEventId
    transport_event_id: str
    authorization_id: AuthorizationId | None
    account_id: AccountId | None
    user_id: UserId | None
    workload_id: WorkloadId | None
    signer_id: str
    state_id: str
    sequence_number: int
    manifest_id: str
    payment_session_id: str
    capability: str
    quantity: int
    quantity_unit: str
    computed_fee: int
    currency: str
    ticket_count: int
    occurred_at: datetime
    status: UsageStatus


@dataclass(frozen=True, slots=True)
class SignedTicketEvent:
    """Normalized subset of a go-livepeer create_signed_ticket event."""

    transport_event_id: str
    occurred_at: datetime
    occurred_at_text: str
    gateway: str
    state_id: str
    session_status: str
    app: str
    pipeline: str
    request_id: str
    orchestrator_address: str
    manifest_id: str
    pm_session_id: str
    current_time: str
    current_time_ns: int
    current_time_unix_ms: int
    previous_time: str
    previous_time_ns: int
    previous_time_unix_ms: int
    billable_seconds: str
    pixels: int
    session_balance: int
    cost: str
    computed_fee: int
    sequence_number: int
    ticket_count: int
    auth_id: str


def exact_cost(quantity: int, price: ExactPrice) -> int:
    """Return the conservative integer ceiling for an exact advertised price."""

    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    return (quantity * price.numerator + price.denominator - 1) // price.denominator


def total_computed_fee(events: tuple[UsageEvent, ...], currency: str = "wei") -> int:
    """Sum matched signer-reported fees in one explicit currency."""

    return sum(
        event.computed_fee
        for event in events
        if event.status is UsageStatus.MATCHED and event.currency == currency
    )
