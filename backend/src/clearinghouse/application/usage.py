"""Signer-event attribution and workload cost aggregation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from clearinghouse.application.core_store import AccessIdentity, CoreStore
from clearinghouse.application.pagination import KeysetPage
from clearinghouse.domain.core import (
    AuthorizationId,
    ExactPrice,
    SignedTicketEvent,
    UsageEvent,
    UsageEventId,
    UsageStatus,
    Workload,
)

NANOSECONDS_PER_SECOND = 1_000_000_000
NANOSECONDS_PER_HOUR = 3_600 * NANOSECONDS_PER_SECOND
MAX_SIGNED_NANOSECONDS = (1 << 63) - 1


@dataclass(frozen=True, slots=True)
class WorkloadCost:
    workload: Workload
    measured_quantity: int
    measured_unit: str
    quoted_fee: int
    computed_fee: int
    currency: str
    event_count: int


def normalized_quantity(event: SignedTicketEvent, price: ExactPrice | None) -> tuple[int, str]:
    unit = price.quantity_unit.lower() if price else ""
    if unit == "fixed":
        return 1, "fixed"
    if unit in {"seconds", "second", "hour", "hours"}:
        return _billable_nanoseconds(event.billable_seconds), "nanosecond"
    if event.pixels > 0:
        return event.pixels, "pixel"
    return max(0, event.current_time_ns - event.previous_time_ns), "nanosecond"


def _billable_nanoseconds(value: str) -> int:
    try:
        seconds = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("billable seconds must be a decimal number") from error
    nanoseconds = seconds * NANOSECONDS_PER_SECOND
    if not seconds.is_finite() or seconds < 0:
        raise ValueError("billable seconds must be finite and non-negative")
    if nanoseconds != nanoseconds.to_integral_value():
        raise ValueError("billable seconds must not exceed nanosecond precision")
    result = int(nanoseconds)
    if result > MAX_SIGNED_NANOSECONDS:
        raise ValueError("billable seconds exceed the signed nanosecond range")
    return result


def quoted_fee(quantity: int, measured_unit: str, price: ExactPrice) -> int:
    divisor = price.denominator
    price_unit = price.quantity_unit.lower()
    if measured_unit == "nanosecond" and price_unit in {"seconds", "second"}:
        divisor *= NANOSECONDS_PER_SECOND
    elif measured_unit == "nanosecond" and price_unit in {"hour", "hours"}:
        divisor *= NANOSECONDS_PER_HOUR
    numerator = quantity * price.numerator
    return (numerator + divisor - 1) // divisor


class UsageService:
    def __init__(self, store: CoreStore, *, signer_id: str) -> None:
        self.store = store
        self.signer_id = signer_id

    async def ingest(self, event: SignedTicketEvent) -> bool:
        authorization_id = AuthorizationId(event.auth_id)
        async with self.store.transaction() as transaction:
            authorization = await transaction.get_authorization(authorization_id)
            workload = (
                await transaction.get_workload(authorization.workload_id)
                if authorization is not None
                else None
            )
            quantity, unit = normalized_quantity(
                event, workload.max_price if workload is not None else None
            )
            matched = (
                authorization is not None
                and workload is not None
                and authorization.signer_id == self.signer_id
                and authorization.state_id == event.state_id
                and (
                    not event.orchestrator_address
                    or authorization.orchestrator_address == event.orchestrator_address
                )
            )
            identifier = hashlib.sha256(
                f"{self.signer_id}\0{event.transport_event_id}".encode()
            ).hexdigest()[:24]
            usage = UsageEvent(
                UsageEventId(f"usage_{identifier}"),
                event.transport_event_id,
                authorization_id if matched else None,
                workload.account_id if matched and workload else None,
                workload.user_id if matched and workload else None,
                workload.id if matched and workload else None,
                self.signer_id,
                event.state_id,
                event.sequence_number,
                event.manifest_id,
                event.pm_session_id,
                workload.capability if matched and workload else event.app or event.pipeline,
                quantity,
                unit,
                event.computed_fee,
                "wei",
                event.ticket_count,
                event.occurred_at,
                UsageStatus.MATCHED if matched else UsageStatus.UNMATCHED,
            )
            return await transaction.put_usage(usage)

    async def events(
        self,
        identity: AccessIdentity | None = None,
        *,
        limit: int = 50,
        after: tuple[str, ...] = (),
    ) -> KeysetPage[UsageEvent]:
        async with self.store.transaction() as transaction:
            return await transaction.list_usage(
                identity.account_id if identity else None, limit=limit, after=after
            )

    async def costs(
        self,
        identity: AccessIdentity | None = None,
        *,
        limit: int = 50,
        after: tuple[str, ...] = (),
    ) -> KeysetPage[WorkloadCost]:
        async with self.store.transaction() as transaction:
            workload_page = await transaction.list_workloads(
                identity.account_id if identity else None, limit=limit, after=after
            )
            aggregates = await transaction.usage_aggregates(
                tuple(workload.id for workload in workload_page.items)
            )
        grouped = {aggregate.workload_id: aggregate for aggregate in aggregates}
        result: list[WorkloadCost] = []
        for workload in workload_page.items:
            observed = grouped.get(workload.id)
            quantity = observed.measured_quantity if observed else 0
            unit = (
                observed.measured_unit
                if observed and observed.measured_unit
                else _base_unit(workload.max_price)
            )
            result.append(
                WorkloadCost(
                    workload,
                    quantity,
                    unit,
                    quoted_fee(quantity, unit, workload.max_price),
                    observed.computed_fee if observed else 0,
                    workload.max_price.currency,
                    observed.event_count if observed else 0,
                )
            )
        return KeysetPage(tuple(result), workload_page.next_key)


def _base_unit(price: ExactPrice) -> str:
    return (
        "nanosecond"
        if price.quantity_unit.lower() in {"second", "seconds", "hour", "hours"}
        else price.quantity_unit
    )
