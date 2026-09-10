"""Canonical metering observations and settlement results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MeteringOutcome(StrEnum):
    SETTLED = "settled"
    DUPLICATE = "duplicate"
    QUARANTINED = "quarantined"
    IGNORED = "ignored"


class QuarantineReason(StrEnum):
    INVALID_SCHEMA = "invalid_schema"
    UNKNOWN_RESERVATION = "unknown_reservation"
    FEE_MISMATCH = "fee_mismatch"
    SEQUENCE_GAP = "sequence_gap"
    FORKED_OBSERVATION = "forked_observation"
    LINEAGE_MISMATCH = "lineage_mismatch"
    KEY_MISMATCH = "key_mismatch"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    TRANSPORT_DIVERGENCE = "transport_divergence"


@dataclass(frozen=True, slots=True)
class TransportRecord:
    topic: str
    partition: int
    offset: int
    key: bytes | None
    payload: bytes | None
    received_at: datetime
    beginning_offset: int = 0


@dataclass(frozen=True, slots=True)
class SignedTicketEvent:
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


@dataclass(frozen=True, slots=True)
class ProcessResult:
    outcome: MeteringOutcome
    reason: QuarantineReason | None = None
    usage_event_id: str | None = None
    charge_id: str | None = None
    next_offset: int | None = None


@dataclass(frozen=True, slots=True)
class UsageEvent:
    id: str
    reservation_id: str
    lease_id: str
    tenant_id: str
    account_id: str
    principal_id: str
    capability: str
    model: str | None
    quantity: int
    quantity_unit: str
    rate_card_id: str
    rate_numerator: int
    rate_denominator: int
    charge_unit: str
    manifest_id: str
    producer_id: str
    transport_event_id: str
    sequence_number: int
    ticket_count: int
    occurred_at: datetime
    occurred_at_text: str
    occurred_at_ns: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Charge:
    id: str
    usage_event_id: str
    reservation_id: str
    lease_id: str
    tenant_id: str
    account_id: str
    amount: int
    unit: str
    rate_card_id: str
    rate_numerator: int
    rate_denominator: int
    quantity_unit: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReconciliationCase:
    id: str
    reservation_id: str | None
    tenant_id: str | None
    account_id: str | None
    kind: str
    status: str
    reason: str
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class OpenReservation:
    id: str
    lease_id: str
    tenant_id: str
    account_id: str
    status: str
    reserved_amount: int
    unit: str
    sequence_number: int
    signer_confirmed_at: datetime | None
    created_at: datetime
