"""Small executable invariants shared by implementation and contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

NANOSECONDS_PER_SECOND = 1_000_000_000
LV2V_PIXELS_PER_SECOND = 720 * 1280 * 30


@dataclass(frozen=True, slots=True)
class LedgerPosting:
    account: str
    amount: int
    unit: str


@dataclass(frozen=True, slots=True)
class ChargeLineage:
    charge_id: str
    usage_event_id: str
    usage_lease_id: str
    charge_lease_id: str
    lease_account_id: str
    charge_account_id: str


class ReservationStatus(StrEnum):
    PENDING = "pending"
    SETTLED = "settled"
    UNRESOLVED = "unresolved"
    QUARANTINED = "quarantined"


_ALLOWED_TRANSITIONS = {
    ReservationStatus.PENDING: frozenset(
        {
            ReservationStatus.SETTLED,
            ReservationStatus.UNRESOLVED,
            ReservationStatus.QUARANTINED,
        }
    ),
    ReservationStatus.UNRESOLVED: frozenset(
        {ReservationStatus.SETTLED, ReservationStatus.QUARANTINED}
    ),
    ReservationStatus.SETTLED: frozenset(),
    ReservationStatus.QUARANTINED: frozenset(),
}


def assert_ledger_balanced(postings: tuple[LedgerPosting, ...]) -> None:
    """Require at least two postings and zero net movement in every unit."""
    if len(postings) < 2:
        raise ValueError("a ledger transaction requires at least two postings")
    totals: dict[str, int] = {}
    for posting in postings:
        if not posting.account or not posting.unit:
            raise ValueError("posting account and unit are required")
        totals[posting.unit] = totals.get(posting.unit, 0) + posting.amount
    if any(total != 0 for total in totals.values()):
        raise ValueError("ledger postings must balance independently in every unit")


def assert_charge_lineage(lineage: ChargeLineage) -> None:
    """Require one complete charge -> usage -> lease -> account lineage."""
    if not all(
        (
            lineage.charge_id,
            lineage.usage_event_id,
            lineage.usage_lease_id,
            lineage.charge_lease_id,
            lineage.lease_account_id,
            lineage.charge_account_id,
        )
    ):
        raise ValueError("charge lineage identifiers are required")
    if lineage.usage_lease_id != lineage.charge_lease_id:
        raise ValueError("charge and usage must cite the same lease")
    if lineage.lease_account_id != lineage.charge_account_id:
        raise ValueError("charge and lease must cite the same account")


def assert_reservation_transition(previous: ReservationStatus, current: ReservationStatus) -> None:
    """Reject release/reopen transitions, including transitions caused by expiry."""
    if current not in _ALLOWED_TRANSITIONS[previous]:
        raise ValueError(f"invalid reservation transition: {previous} -> {current}")


def ceil_ratio(numerator: int, denominator: int) -> int:
    """Return the mathematical ceiling for a non-negative rational."""
    if numerator < 0 or denominator <= 0:
        raise ValueError("ceil_ratio requires a non-negative numerator and positive denominator")
    return (numerator + denominator - 1) // denominator


def conservative_reservation_units(payment_type: str, elapsed_ns: int) -> int:
    """Mirror supported signer shapes while conservatively covering float variance."""
    if payment_type == "fixed":
        return 1
    if payment_type == "live":
        if elapsed_ns <= 0:
            return 10
        return ceil_ratio(elapsed_ns, NANOSECONDS_PER_SECOND)
    if payment_type == "lv2v":
        if elapsed_ns <= 0:
            return LV2V_PIXELS_PER_SECOND * 60
        return ceil_ratio(LV2V_PIXELS_PER_SECOND * elapsed_ns, NANOSECONDS_PER_SECOND)
    raise ValueError("unsupported payment type")


def conservative_reservation_amount(
    units: int, price_numerator: int, price_denominator: int
) -> int:
    """Reserve the ceiling of the exact initial-price rational."""
    if units <= 0 or price_numerator <= 0:
        raise ValueError("units and price numerator must be positive")
    return ceil_ratio(units * price_numerator, price_denominator)
