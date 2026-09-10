"""Exact signer-session, lease, and authorization domain values."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class DenialReason(StrEnum):
    LEASE_EXHAUSTED = "lease_exhausted"
    LEASE_EXPIRED = "lease_expired"
    ACCOUNT_SUSPENDED = "account_suspended"
    TENANT_SUSPENDED = "tenant_suspended"
    UNKNOWN_CREDENTIAL = "unknown_credential"
    CAPABILITY_DENIED = "capability_denied"
    UNSUPPORTED_PAYMENT_SHAPE = "unsupported_payment_shape"
    REPLAYED_STATE = "replayed_state"
    OUT_OF_ORDER_STATE = "out_of_order_state"
    STATE_FORK = "state_fork"
    KILL_SWITCH_ACTIVE = "kill_switch_active"
    LEDGER_UNAVAILABLE = "ledger_unavailable"
    IDENTITY_UNAVAILABLE = "identity_unavailable"


@dataclass(frozen=True, slots=True)
class Lease:
    id: str
    cap: int
    available: int
    pending: int
    settled: int
    unit: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SignerSession:
    id: str
    token: str
    signer_url: str
    discovery_url: str
    expires_at: datetime
    lease: Lease


@dataclass(frozen=True, slots=True)
class PaymentState:
    state_id: str
    pm_session_id: str
    last_update: str
    orchestrator_address: str
    app: str
    auth_expiry: int
    sender_nonce: int
    balance: str
    initial_price_per_unit: int
    initial_pixels_per_unit: int
    payment_type: str
    sequence_number: int
    auth_id: str


@dataclass(frozen=True, slots=True)
class Admission:
    allowed: bool
    reason: DenialReason | None = None
    session_id: str | None = None
    tenant_id: str | None = None
    account_id: str | None = None
    principal_id: str | None = None
    lease: Lease | None = None
    reserved_amount: int | None = None
    rate_numerator: int | None = None
    rate_denominator: int | None = None
    quantity_unit: str | None = None


_RFC3339_NANO = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$"
)


def epoch_nanoseconds(value: str) -> int:
    """Parse RFC3339 while retaining all nine fractional digits."""
    match = _RFC3339_NANO.fullmatch(value)
    if match is None:
        raise ValueError("LastUpdate must be RFC3339Nano")
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    fraction = (match.group(7) or "").ljust(9, "0")
    zone = match.group(8)
    normalized = f"{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}{zone}"
    instant = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    delta = instant.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    seconds = delta.days * 86_400 + delta.seconds
    return seconds * 1_000_000_000 + int(fraction)


def reservation_quantity(payment_type: str, elapsed_ns: int) -> tuple[int, str]:
    """Return conservative quantity and canonical unit for a supported shape."""
    if payment_type == "fixed":
        return 1, "fixed"
    if payment_type == "live":
        duration = elapsed_ns if elapsed_ns > 0 else 10_000_000_000
        return (duration + 999_999_999) // 1_000_000_000, "seconds"
    if payment_type == "lv2v":
        duration = elapsed_ns if elapsed_ns > 0 else 60_000_000_000
        pixels_per_second = 720 * 1280 * 30
        return (duration * pixels_per_second + 999_999_999) // 1_000_000_000, "720p-pixel-seconds"
    raise ValueError("unsupported payment shape")


def ceil_ratio(quantity: int, numerator: int, denominator: int) -> int:
    if quantity < 0 or numerator < 0 or denominator <= 0:
        raise ValueError("invalid exact rate")
    return (quantity * numerator + denominator - 1) // denominator


def state_digest(state: PaymentState) -> str:
    payload = json.dumps(
        {
            "app": state.app,
            "auth_expiry": state.auth_expiry,
            "auth_id": state.auth_id,
            "balance": state.balance,
            "initial_pixels_per_unit": state.initial_pixels_per_unit,
            "initial_price_per_unit": state.initial_price_per_unit,
            "last_update": state.last_update,
            "orchestrator_address": state.orchestrator_address,
            "payment_type": state.payment_type,
            "pm_session_id": state.pm_session_id,
            "sender_nonce": state.sender_nonce,
            "sequence_number": state.sequence_number,
            "state_id": state.state_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
