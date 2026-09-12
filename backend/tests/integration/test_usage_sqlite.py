import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from clearinghouse.application.usage import UsageService, normalized_quantity, quoted_fee
from clearinghouse.application.workloads import SignerState, WorkloadService
from clearinghouse.domain.core import CapabilityOffer, ExactPrice, PriceObservationId, UsageStatus
from clearinghouse.infrastructure.signed_ticket import decode_signed_ticket
from clearinghouse.infrastructure.sqlite import SqliteStore


def event_payload(
    *,
    auth_id: str,
    event_id: str = "71e9c70b-5ea1-4ff4-af4c-c18a07155450",
    state_id: str = "state-1",
    orchestrator_address: str = "0x0000000000000000000000000000000000000001",
    current_time: str = "2026-09-11T14:00:01Z",
    current_time_unix: int = 1789135201000,
    previous_time: str = "2026-09-11T14:00:00Z",
    previous_time_unix: int = 1789135200000,
    billable_secs: int | float = 1.0,
) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": "create_signed_ticket",
            "timestamp": "1789135200000",
            "gateway": "gateway.example.com",
            "data": {
                "session_id": state_id,
                "session_status": "continuing",
                "app": "live",
                "pipeline": "live",
                "request_id": "request-1",
                "orch_address": orchestrator_address,
                "orch_url": "https://orch.example.com",
                "manifest_id": "manifest-1",
                "pm_session_id": "pm-1",
                "current_time": current_time,
                "current_time_unix": current_time_unix,
                "previous_time": previous_time,
                "previous_time_unix": previous_time_unix,
                "billable_secs": billable_secs,
                "pixels": 0,
                "session_balance": "100",
                "computed_fee": "3",
                "cost": "3.0000000000",
                "sequence_number": 0,
                "num_tickets": 1,
                "auth_id": auth_id,
            },
        },
        separators=(",", ":"),
    ).encode()


async def test_signed_ticket_is_attributed_and_costed_against_quote(tmp_path) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 9, 11, 14, tzinfo=UTC)
    store = SqliteStore(tmp_path / "usage.db")
    await store.initialize()
    offer = CapabilityOffer(
        PriceObservationId("price_seconds"),
        "https://runner.example.com",
        "0x0000000000000000000000000000000000000001",
        "live",
        None,
        (),
        ExactPrice(5, 2, "wei", "seconds"),
        now,
        now + timedelta(minutes=5),
    )
    async with store.transaction() as transaction:
        identity = await transaction.resolve_identity("meter@example.com", admin_email=None)
        await transaction.replace_offers([offer])
    workloads = WorkloadService(
        store,
        pepper="workload-pepper-long-enough",
        signer_id="signer_default0000",
        public_signer_url="https://signer.example.com",
        public_discovery_url="https://discovery.example.com",
        clock=lambda: now,
        secret_factory=iter(("token", "id")).__next__,
    )
    issued = await workloads.create(identity, offer_id=offer.id)
    assert (
        await workloads.authorize(
            issued.access.token,
            SignerState(
                "state-1",
                0,
                offer.orchestrator_address or "",
                5,
                2,
                app="live",
                job_type="live",
            ),
        )
    ).allowed
    event = decode_signed_ticket(event_payload(auth_id=issued.workload.id))
    assert event is not None
    usage = UsageService(store, signer_id="signer_default0000")
    assert await usage.ingest(event)
    assert not await usage.ingest(event)
    mismatched = decode_signed_ticket(
        event_payload(
            auth_id=issued.workload.id,
            event_id="8ef85f25-ae15-4db7-8898-f1f4c976fc21",
            state_id="other-state",
        )
    )
    assert mismatched is not None
    assert await usage.ingest(mismatched)
    observed = list(await usage.events(identity))
    assert observed[0].status is UsageStatus.MATCHED
    assert (observed[0].quantity, observed[0].quantity_unit) == (1_000_000_000, "nanosecond")
    costs = list(await usage.costs(identity))
    assert (costs[0].quoted_fee, costs[0].computed_fee, costs[0].event_count) == (3, 3, 1)
    assert sum(value.status is UsageStatus.UNMATCHED for value in await usage.events()) == 1
    await store.close()


async def test_unmatched_events_are_retained_and_other_types_ignored(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = SqliteStore(tmp_path / "unmatched.db")
    await store.initialize()
    event = decode_signed_ticket(event_payload(auth_id="unknown"))
    assert event is not None
    service = UsageService(store, signer_id="signer_default0000")
    assert await service.ingest(event)
    assert (await service.events())[0].status is UsageStatus.UNMATCHED
    assert decode_signed_ticket(b'{"id":"any","type":"heartbeat"}') is None
    await store.close()


def test_quantity_and_quoted_fee_units() -> None:
    event = decode_signed_ticket(event_payload(auth_id="unknown"))
    assert event is not None
    assert normalized_quantity(event, ExactPrice(1, 1, "wei", "fixed")) == (1, "fixed")
    assert quoted_fee(1, "fixed", ExactPrice(7, 1, "wei", "fixed")) == 7
    assert quoted_fee(3_600_000_000_000, "nanosecond", ExactPrice(2, 1, "wei", "hour")) == 2


def test_time_quantity_uses_billable_seconds_when_ticket_timestamps_match() -> None:
    event = decode_signed_ticket(
        event_payload(
            auth_id="unknown",
            current_time="2026-09-11T14:00:01Z",
            current_time_unix=1789135201000,
            previous_time="2026-09-11T14:00:01Z",
            previous_time_unix=1789135201000,
            billable_secs=10,
        )
    )
    assert event is not None
    assert normalized_quantity(event, ExactPrice(1, 1, "wei", "seconds")) == (
        10_000_000_000,
        "nanosecond",
    )


@pytest.mark.parametrize("billable", [-1, float("inf"), 0.0000000001])
def test_time_quantity_rejects_invalid_billable_seconds(billable: float) -> None:
    event = decode_signed_ticket(event_payload(auth_id="unknown"))
    assert event is not None
    event = replace(event, billable_seconds=str(billable))
    with pytest.raises(ValueError, match="billable seconds"):
        normalized_quantity(event, ExactPrice(1, 1, "wei", "seconds"))
