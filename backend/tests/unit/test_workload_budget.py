from datetime import UTC, datetime

import pytest

from clearinghouse.application.workloads import SignerState
from clearinghouse.domain.core import (
    AccountId,
    AuthorizationId,
    ExactPrice,
    PaymentAuthorization,
    PriceObservationId,
    UserId,
    Workload,
    WorkloadId,
    WorkloadStatus,
)

NOW = datetime(2026, 9, 13, tzinfo=UTC)


def state(job_type: str, last_update_ns: int | None) -> SignerState:
    return SignerState("state", 0, "0x1", 1, 1, job_type=job_type, last_update_ns=last_update_ns)


def test_signer_state_conservatively_reproduces_pinned_billable_units() -> None:
    assert state("fixed", 1).billable_quantity(None) == 1
    assert state("live", 1).billable_quantity(None) == 10
    assert state("lv2v", 1).billable_quantity(None) == 1_658_880_000
    assert state("live", 2_100_000_001).billable_quantity(1_000_000_000) == 2
    assert state("lv2v", 1_000_000_001).billable_quantity(1_000_000_000) == 1
    assert state("live", None).billable_quantity(None) is None
    assert state("fixed", None).billable_quantity(None) is None
    assert state("live", 1).billable_quantity(1) is None


def test_budget_domain_values_require_positive_signed_amounts() -> None:
    price = ExactPrice(1, 1, "wei", "fixed")
    base = (
        WorkloadId("work"),
        AccountId("account"),
        UserId("user"),
        "fixed",
        None,
        PriceObservationId("offer"),
        price,
        WorkloadStatus.ACTIVE,
        NOW,
        NOW,
    )
    with pytest.raises(ValueError, match="positive signed 64-bit"):
        Workload(*base, max_spend_wei=0)
    with pytest.raises(ValueError, match="positive signed 64-bit"):
        Workload(*base, max_spend_wei=1 << 63)
    with pytest.raises(ValueError, match="non-negative"):
        PaymentAuthorization(
            AuthorizationId("auth"),
            WorkloadId("work"),
            "signer",
            "state",
            0,
            "0x1",
            price,
            NOW,
            authorized_fee=-1,
        )
