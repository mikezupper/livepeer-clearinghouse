from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from clearinghouse.domain.core import (
    AccountId,
    AuthorizationId,
    ExactPrice,
    PriceObservationId,
    UsageEvent,
    UsageEventId,
    UsageStatus,
    UserId,
    Workload,
    WorkloadId,
    WorkloadStatus,
    exact_cost,
    total_computed_fee,
)


def usage(event_id: str, fee: int, status: UsageStatus, currency: str = "wei") -> UsageEvent:
    return UsageEvent(
        id=UsageEventId(event_id),
        transport_event_id=f"transport-{event_id}",
        authorization_id=(
            AuthorizationId("authorization-1") if status is UsageStatus.MATCHED else None
        ),
        account_id=AccountId("account-1") if status is UsageStatus.MATCHED else None,
        user_id=UserId("user-1") if status is UsageStatus.MATCHED else None,
        workload_id=WorkloadId("workload-1") if status is UsageStatus.MATCHED else None,
        signer_id="signer-1",
        state_id="state-1",
        sequence_number=1,
        manifest_id="manifest-1",
        payment_session_id="payment-1",
        capability="live-video-to-video",
        quantity=1,
        quantity_unit="seconds",
        computed_fee=fee,
        currency=currency,
        ticket_count=1,
        occurred_at=datetime(2026, 9, 11, tzinfo=UTC),
        status=status,
    )


def test_exact_cost_uses_integer_ceiling() -> None:
    assert exact_cost(3, ExactPrice(5, 2, "wei", "seconds")) == 8
    assert exact_cost(0, ExactPrice(5, 2, "wei", "seconds")) == 0


@pytest.mark.parametrize(
    "price",
    [
        (-1, 1, "wei", "seconds"),
        (1, 0, "wei", "seconds"),
        (1, 1, "", "seconds"),
        (1, 1, "wei", ""),
    ],
)
def test_exact_price_rejects_invalid_terms(price: tuple[int, int, str, str]) -> None:
    with pytest.raises(ValueError):
        ExactPrice(*price)


def test_exact_cost_rejects_negative_quantity() -> None:
    with pytest.raises(ValueError):
        exact_cost(-1, ExactPrice(1, 1, "wei", "fixed"))


def test_total_computed_fee_includes_only_matched_currency() -> None:
    events = (
        usage("usage-1", 4, UsageStatus.MATCHED),
        usage("usage-2", 100, UsageStatus.UNMATCHED),
        usage("usage-3", 8, UsageStatus.MATCHED, "usd-micros"),
    )
    assert total_computed_fee(events) == 4
    assert total_computed_fee(events, "usd-micros") == 8


def test_workload_effective_status_distinguishes_expiry_from_terminal_outcome() -> None:
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    workload = Workload(
        WorkloadId("workload-1"),
        AccountId("account-1"),
        UserId("user-1"),
        "live",
        None,
        PriceObservationId("price-1"),
        ExactPrice(1, 1, "wei", "fixed"),
        WorkloadStatus.ACTIVE,
        now + timedelta(minutes=1),
        now,
    )
    assert workload.effective_status(now) is WorkloadStatus.ACTIVE
    assert workload.effective_status(now + timedelta(minutes=1)) is WorkloadStatus.EXPIRED
    revoked = replace(workload, status=WorkloadStatus.REVOKED)
    assert revoked.effective_status(now + timedelta(days=1)) is WorkloadStatus.REVOKED
