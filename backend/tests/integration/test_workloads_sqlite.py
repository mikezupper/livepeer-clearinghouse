import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from clearinghouse.application.usage import UsageService
from clearinghouse.application.workloads import SignerState, WorkloadService
from clearinghouse.domain.core import CapabilityOffer, ExactPrice, PriceObservationId
from clearinghouse.infrastructure.sqlite import SqliteStore


async def test_quoted_workload_authorizes_once_and_enforces_price_and_orchestrator(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    store = SqliteStore(tmp_path / "workloads.db")
    await store.initialize()
    offer = CapabilityOffer(
        PriceObservationId("price_1"),
        "https://runner.example.com",
        "0x0000000000000000000000000000000000000001",
        "live-video-to-video",
        "noop",
        (("orchestrator_url", "https://orch.example.com"),),
        ExactPrice(2, 1, "wei", "720p-pixel-seconds"),
        now,
        now + timedelta(minutes=5),
    )
    async with store.transaction() as transaction:
        identity = await transaction.resolve_identity("user@example.com", admin_email=None)
        await transaction.replace_offers([offer])
    secrets = iter(("token", "id", "token-two", "id-two"))
    service = WorkloadService(
        store,
        pepper="workload-pepper-long-enough",
        signer_id="signer_default0000",
        public_signer_url="https://clearinghouse.example.com/",
        public_discovery_url="https://clearinghouse.example.com/v1/discovery",
        clock=lambda: now,
        secret_factory=secrets.__next__,
    )
    issued = await service.create(identity, offer_id=offer.id, client_reference=" client-job-1 ")
    assert issued.access.signer_url == "https://clearinghouse.example.com"
    assert issued.access.orchestrators == ("https://orch.example.com",)
    assert issued.workload.client_reference == "client-job-1"
    assert list(await service.list(identity)) == [issued.workload]

    state = SignerState(
        "state-1",
        0,
        offer.orchestrator_address or "",
        2,
        1,
        app="live-video-to-video",
        job_type="lv2v",
        manifest_id="manifest-1",
        payment_session_id="pm-1",
    )
    decision = await service.authorize(issued.access.token, state)
    assert decision.allowed and decision.auth_id == issued.workload.id
    assert (await service.authorize(issued.access.token, state)).allowed
    rebound = await service.authorize(
        issued.access.token,
        SignerState(
            "state-2",
            0,
            state.orchestrator_address,
            2,
            1,
            app="live-video-to-video",
            job_type="lv2v",
        ),
    )
    assert (rebound.status, rebound.reason) == (402, "workload_already_bound")

    conflicting = await service.create(identity, offer_id=offer.id)
    conflict = await service.authorize(conflicting.access.token, state)
    assert (conflict.status, conflict.reason) == (409, "authorization_conflict")
    async with store.transaction() as transaction:
        unchanged = await transaction.get_workload(conflicting.workload.id)
    assert unchanged is not None and unchanged.runner_session_id is None

    await service.revoke(identity, issued.workload.id)
    assert (await service.authorize(issued.access.token, state)).reason == "workload_inactive"
    await store.close()


async def test_authorization_rejects_unknown_expensive_and_wrong_orchestrator(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    store = SqliteStore(tmp_path / "policy.db")
    await store.initialize()
    offer = CapabilityOffer(
        PriceObservationId("price_2"),
        "https://runner.example.com",
        "0x0000000000000000000000000000000000000001",
        "live",
        None,
        (),
        ExactPrice(2, 1, "wei", "seconds"),
        now,
        now + timedelta(minutes=5),
    )
    async with store.transaction() as transaction:
        identity = await transaction.resolve_identity("other@example.com", admin_email=None)
        await transaction.replace_offers([offer])
    service = WorkloadService(
        store,
        pepper="workload-pepper-long-enough",
        signer_id="signer_default0000",
        public_signer_url="https://signer.example.com",
        public_discovery_url="https://discovery.example.com",
        clock=lambda: now,
        secret_factory=iter(("token", "id")).__next__,
    )
    issued = await service.create(identity, offer_id=offer.id)
    assert (
        await service.authorize(
            "bad", SignerState("s", 0, "0x0", 1, 1, app="live", job_type="live")
        )
    ).status == 401
    expensive = await service.authorize(
        issued.access.token,
        SignerState("s", 0, offer.orchestrator_address or "", 3, 1, app="live", job_type="live"),
    )
    assert expensive.reason == "price_exceeds_quote"
    unsupported = await service.authorize(
        issued.access.token,
        SignerState("s", 0, offer.orchestrator_address or "", 2, 1, app="live"),
    )
    assert unsupported.reason == "unsupported_job_type"
    wrong_capability = await service.authorize(
        issued.access.token,
        SignerState("s", 0, offer.orchestrator_address or "", 2, 1, app="fixed", job_type="live"),
    )
    assert wrong_capability.reason == "capability_mismatch"
    wrong_unit = await service.authorize(
        issued.access.token,
        SignerState("s", 0, offer.orchestrator_address or "", 2, 1, app="live", job_type="fixed"),
    )
    assert wrong_unit.reason == "price_unit_mismatch"
    wrong = await service.authorize(
        issued.access.token,
        SignerState(
            "s",
            0,
            "0x0000000000000000000000000000000000000002",
            2,
            1,
            app="live",
            job_type="live",
        ),
    )
    assert wrong.reason == "orchestrator_mismatch"
    await store.close()


async def test_spend_ceiling_reserves_exact_fees_and_rejects_concurrent_forks(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    store = SqliteStore(tmp_path / "spend-ceiling.db")
    await store.initialize()
    offer = CapabilityOffer(
        PriceObservationId("price_fixed"),
        "https://runner.example.com",
        "0x0000000000000000000000000000000000000001",
        "image-to-video",
        None,
        (),
        ExactPrice(3, 1, "wei", "fixed"),
        now,
        now + timedelta(minutes=5),
    )
    async with store.transaction() as transaction:
        identity = await transaction.resolve_identity("budget@example.com", admin_email=None)
        await transaction.replace_offers([offer])
    service = WorkloadService(
        store,
        pepper="workload-pepper-long-enough",
        signer_id="signer_default0000",
        public_signer_url="https://signer.example.com",
        public_discovery_url="https://discovery.example.com",
        clock=lambda: now,
        secret_factory=iter(("token", "id")).__next__,
    )
    issued = await service.create(identity, offer_id=offer.id, max_spend_wei=7)
    initial = SignerState(
        "state-budget",
        0,
        offer.orchestrator_address or "",
        3,
        1,
        app=offer.capability,
        job_type="fixed",
        last_update_ns=1_000_000_000,
    )
    assert (await service.authorize(issued.access.token, initial)).allowed
    assert (await service.authorize(issued.access.token, initial)).allowed

    results = await asyncio.gather(
        service.authorize(
            issued.access.token,
            SignerState(
                "state-budget",
                1,
                initial.orchestrator_address,
                3,
                1,
                app=offer.capability,
                job_type="fixed",
                last_update_ns=2_000_000_000,
            ),
        ),
        service.authorize(
            issued.access.token,
            SignerState(
                "state-budget",
                1,
                initial.orchestrator_address,
                3,
                1,
                app=offer.capability,
                job_type="fixed",
                last_update_ns=3_000_000_000,
            ),
        ),
    )
    assert sorted(result.status for result in results) == [200, 409]
    denied = await service.authorize(
        issued.access.token,
        SignerState(
            "state-budget",
            2,
            initial.orchestrator_address,
            3,
            1,
            app=offer.capability,
            job_type="fixed",
            last_update_ns=4_000_000_000,
        ),
    )
    assert (denied.status, denied.reason) == (402, "spend_ceiling_exceeded")

    costs = await UsageService(store, signer_id="signer_default0000").costs(identity)
    assert len(costs.items) == 1
    assert costs.items[0].authorized_fee == 6
    assert costs.items[0].pending_fee == 6
    assert costs.items[0].remaining_spend == 1
    await store.close()
