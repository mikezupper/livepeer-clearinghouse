"""Deterministic integrations for every distinct core billing lifecycle.

These tests deliberately stop at the Clearinghouse signer/event boundaries. A
public orchestrator cannot reliably be instructed to fail, reprice, delay, or
duplicate an event, so those behaviors belong to a controlled qualification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

from clearinghouse.application.core_store import AccessIdentity
from clearinghouse.application.usage import UsageService
from clearinghouse.application.workloads import IssuedWorkload, SignerState, WorkloadService
from clearinghouse.domain.core import CapabilityOffer, ExactPrice, PriceObservationId, UsageStatus
from clearinghouse.infrastructure.signed_ticket import decode_signed_ticket
from clearinghouse.infrastructure.sqlite import SqliteStore

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
ORCH = "0x0000000000000000000000000000000000000001"
SIGNER = "signer_default0000"


@dataclass(slots=True)
class Harness:
    store: SqliteStore
    identity: AccessIdentity
    workloads: WorkloadService
    usage: UsageService
    issued: IssuedWorkload


async def harness(
    tmp_path: Path,
    *,
    unit: str,
    numerator: int,
    denominator: int = 1,
    capability: str = "qualification/app",
) -> Harness:
    store = SqliteStore(tmp_path / f"{unit}.db")
    await store.initialize()
    offer = CapabilityOffer(
        PriceObservationId(f"price_{unit}"),
        "https://runner.example/session",
        ORCH,
        capability,
        None,
        (("orchestrator_url", "https://orch.example"),),
        ExactPrice(numerator, denominator, "wei", unit),
        NOW,
        NOW + timedelta(minutes=5),
    )
    async with store.transaction() as transaction:
        identity = await transaction.resolve_identity("qualification@example.com", admin_email=None)
        await transaction.replace_offers([offer])
    workloads = WorkloadService(
        store,
        pepper="qualification-workload-pepper",
        signer_id=SIGNER,
        public_signer_url="https://clearinghouse.example",
        public_discovery_url="https://clearinghouse.example/v1/discovery",
        clock=lambda: NOW,
        secret_factory=iter((f"token-{unit}", f"id-{unit}")).__next__,
    )
    issued = await workloads.create(identity, offer_id=offer.id)
    return Harness(store, identity, workloads, UsageService(store, signer_id=SIGNER), issued)


def payload(
    issued: IssuedWorkload,
    *,
    event_name: str,
    sequence: int,
    billable_seconds: int = 0,
    pixels: int = 0,
    computed_fee: int,
    state_id: str = "state-qualification",
    auth_id: str | None = None,
) -> bytes:
    event_id: UUID = uuid5(UUID("0d4cb398-170d-4393-9e45-f44e5d6cbfac"), event_name)
    current = NOW + timedelta(seconds=sequence + 1)
    previous = NOW + timedelta(seconds=sequence)
    return json.dumps(
        {
            "id": str(event_id),
            "type": "create_signed_ticket",
            "timestamp": str(int(current.timestamp() * 1000)),
            "gateway": "qualification-gateway",
            "data": {
                "session_id": state_id,
                "session_status": "continuing",
                "app": issued.workload.capability,
                "pipeline": issued.workload.capability,
                "request_id": event_name,
                "orch_address": ORCH,
                "manifest_id": "manifest-qualification",
                "pm_session_id": "payment-qualification",
                "current_time": current.isoformat().replace("+00:00", "Z"),
                "current_time_unix": int(current.timestamp() * 1000),
                "previous_time": previous.isoformat().replace("+00:00", "Z"),
                "previous_time_unix": int(previous.timestamp() * 1000),
                "billable_secs": billable_seconds,
                "pixels": pixels,
                "session_balance": "1000",
                "computed_fee": str(computed_fee),
                "cost": str(computed_fee),
                "sequence_number": sequence,
                "num_tickets": 1,
                "auth_id": auth_id or str(issued.workload.id),
            },
        },
        separators=(",", ":"),
    ).encode()


async def authorize(
    value: Harness,
    *,
    numerator: int,
    denominator: int,
    job_type: str,
    state_id: str = "state-qualification",
) -> None:
    decision = await value.workloads.authorize(
        value.issued.access.token,
        SignerState(
            state_id,
            0,
            ORCH,
            numerator,
            denominator,
            app=value.issued.workload.capability,
            job_type=job_type,
            manifest_id="manifest-qualification",
            payment_session_id="payment-qualification",
        ),
    )
    assert decision.allowed, decision.reason


async def test_fixed_success_charged_failure_and_prepayment_failure(tmp_path: Path) -> None:
    value = await harness(tmp_path, unit="fixed", numerator=70)
    await authorize(value, numerator=70, denominator=1, job_type="fixed")
    event = decode_signed_ticket(
        payload(value.issued, event_name="fixed-paid", sequence=0, computed_fee=70)
    )
    assert event is not None and await value.usage.ingest(event)

    # The application's eventual outcome does not erase an already signed fixed charge.
    simulated_application_outcome = "failed"
    costs = list(await value.usage.costs(value.identity))
    assert simulated_application_outcome == "failed"
    assert (costs[0].measured_quantity, costs[0].measured_unit) == (1, "fixed")
    assert (costs[0].quoted_fee, costs[0].computed_fee, costs[0].event_count) == (70, 70, 1)

    before = len(await value.usage.events(value.identity))
    rejected = await value.workloads.authorize(
        "invalid-before-payment",
        SignerState(
            "never-signed", 0, ORCH, 70, 1, app=value.issued.workload.capability, job_type="fixed"
        ),
    )
    assert (rejected.allowed, rejected.reason) == (False, "unknown_credential")
    assert len(await value.usage.events(value.identity)) == before
    await value.store.close()


async def test_persistent_multi_cycle_and_interruption(tmp_path: Path) -> None:
    value = await harness(tmp_path, unit="seconds", numerator=2)
    await authorize(value, numerator=2, denominator=1, job_type="live")
    for sequence in range(3):
        event = decode_signed_ticket(
            payload(
                value.issued,
                event_name=f"cycle-{sequence}",
                sequence=sequence,
                billable_seconds=10,
                computed_fee=20,
            )
        )
        assert event is not None and await value.usage.ingest(event)
    cost = (await value.usage.costs(value.identity))[0]
    assert (cost.event_count, cost.measured_quantity, cost.quoted_fee, cost.computed_fee) == (
        3,
        30_000_000_000,
        60,
        60,
    )

    await value.workloads.revoke(value.identity, value.issued.workload.id)
    rejected = await value.workloads.authorize(
        value.issued.access.token,
        SignerState(
            "new-state", 3, ORCH, 2, 1, app=value.issued.workload.capability, job_type="live"
        ),
    )
    assert (rejected.allowed, rejected.reason) == (False, "workload_inactive")
    assert (await value.usage.costs(value.identity))[0].event_count == 3
    await value.store.close()


async def test_runtime_price_policy_uses_exact_rational_comparison(tmp_path: Path) -> None:
    for name, numerator, denominator in (("lower", 4, 2), ("equal", 10, 4)):
        value = await harness(tmp_path / name, unit="seconds", numerator=5, denominator=2)
        decision = await value.workloads.authorize(
            value.issued.access.token,
            SignerState(
                f"state-{name}",
                0,
                ORCH,
                numerator,
                denominator,
                app=value.issued.workload.capability,
                job_type="live",
            ),
        )
        assert decision.allowed
        await value.store.close()

    value = await harness(tmp_path / "higher", unit="seconds", numerator=5, denominator=2)
    decision = await value.workloads.authorize(
        value.issued.access.token,
        SignerState(
            "state-higher", 0, ORCH, 8, 3, app=value.issued.workload.capability, job_type="live"
        ),
    )
    assert (decision.allowed, decision.reason) == (False, "price_exceeds_quote")
    assert await value.usage.events(value.identity) == []
    await value.store.close()


async def test_duplicate_delayed_and_foreign_events(tmp_path: Path) -> None:
    value = await harness(tmp_path, unit="seconds", numerator=3)
    await authorize(value, numerator=3, denominator=1, job_type="live")
    first_payload = payload(
        value.issued,
        event_name="original",
        sequence=0,
        billable_seconds=1,
        computed_fee=3,
    )
    first = decode_signed_ticket(first_payload)
    duplicate = decode_signed_ticket(first_payload)
    assert first is not None and duplicate is not None
    assert await value.usage.ingest(first)
    assert not await value.usage.ingest(duplicate)

    await value.workloads.revoke(value.identity, value.issued.workload.id)
    delayed = decode_signed_ticket(
        payload(
            value.issued,
            event_name="delayed",
            sequence=1,
            billable_seconds=1,
            computed_fee=3,
        )
    )
    foreign = decode_signed_ticket(
        payload(
            value.issued,
            event_name="foreign",
            sequence=2,
            billable_seconds=1,
            computed_fee=3,
            auth_id="foreign-auth-id",
        )
    )
    assert delayed is not None and foreign is not None
    assert await value.usage.ingest(delayed)
    assert await value.usage.ingest(foreign)
    costs = (await value.usage.costs(value.identity))[0]
    assert (costs.event_count, costs.quoted_fee, costs.computed_fee) == (2, 6, 6)
    events = await value.usage.events()
    assert sum(event.status is UsageStatus.MATCHED for event in events) == 2
    assert sum(event.status is UsageStatus.UNMATCHED for event in events) == 1
    await value.store.close()


async def test_lv2v_pixel_accounting_and_unit_rejection(tmp_path: Path) -> None:
    value = await harness(
        tmp_path,
        unit="720p-pixel-seconds",
        numerator=7,
        denominator=3,
        capability="live-video-to-video/qualification",
    )
    mismatch = await value.workloads.authorize(
        value.issued.access.token,
        SignerState(
            "wrong-unit", 0, ORCH, 7, 3, app=value.issued.workload.capability, job_type="live"
        ),
    )
    assert (mismatch.allowed, mismatch.reason) == (False, "price_unit_mismatch")
    await authorize(value, numerator=7, denominator=3, job_type="lv2v")
    event = decode_signed_ticket(
        payload(
            value.issued,
            event_name="lv2v-pixels",
            sequence=0,
            pixels=921_600,
            computed_fee=2_150_400,
        )
    )
    assert event is not None and await value.usage.ingest(event)
    cost = (await value.usage.costs(value.identity))[0]
    assert (cost.measured_quantity, cost.measured_unit) == (921_600, "pixel")
    assert (cost.quoted_fee, cost.computed_fee, cost.event_count) == (2_150_400, 2_150_400, 1)
    await value.store.close()
