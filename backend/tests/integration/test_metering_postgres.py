"""Live PostgreSQL metering settlement, replay, and invariant tests."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clearinghouse.adapters.kafka import decode_go_livepeer
from clearinghouse.application.accounts import AccountService
from clearinghouse.application.metering import MeteringService
from clearinghouse.application.signer import SignerService
from clearinghouse.domain.accounts import (
    AccountId,
    ExactRate,
    GrantKind,
    PrincipalContext,
    PrincipalId,
    Role,
    TenantId,
)
from clearinghouse.domain.metering import MeteringOutcome, QuarantineReason, TransportRecord
from clearinghouse.domain.signer import PaymentState
from clearinghouse.infrastructure.accounts import PostgresAccountsRepository
from clearinghouse.infrastructure.metering import PostgresMeteringRepository
from clearinghouse.infrastructure.signer import PostgresSignerRepository

DATABASE_URL = os.getenv("CLEARINGHOUSE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="live PostgreSQL not configured")
NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
SIGNED_TIME = "2026-09-09T12:00:00.000000001Z"


def gateway_event(event_id: str, *, fee: str = "1", billable: object = -0.1) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": "create_signed_ticket",
            "timestamp": "0",
            "gateway": "",
            "data": {
                "session_id": "state_metering",
                "session_status": "new",
                "app": "preflight",
                "pipeline": "fixed",
                "request_id": "request_metering",
                "orch_address": "0x" + "a" * 40,
                "orch_url": "",
                "manifest_id": "manifest_metering",
                "pm_session_id": "pm_state_metering",
                "current_time": SIGNED_TIME,
                "current_time_unix": 1788955200000,
                "previous_time": SIGNED_TIME,
                "previous_time_unix": 1788955200000,
                "billable_secs": billable,
                "pixels": 0,
                "session_balance": "0",
                "computed_fee": fee,
                "cost": "9.0000000000",
                "sequence_number": 0,
                "num_tickets": 1,
                "auth_id": "replaced",
            },
        },
        separators=(",", ":"),
    ).encode()


async def test_metering_settles_exactly_and_survives_transport_replays() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    accounts = AccountService(PostgresAccountsRepository(engine), b"integration-credential-pepper")
    suffix = uuid4().hex
    operator_id = f"principal_operator_{suffix}"
    operator = PrincipalContext(PrincipalId(operator_id), frozenset({Role.OPERATOR}))
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO principals(id) VALUES (:id)"), {"id": operator_id}
        )
        await connection.execute(
            text("INSERT INTO principal_roles(principal_id,role) VALUES (:id,'operator')"),
            {"id": operator_id},
        )
    tenant = await accounts.create_tenant(f"Metering {suffix}", operator)
    account = await accounts.create_account(tenant.id, "Payer", "wei", 10, operator)
    principal = await accounts.create_principal(
        tenant.id, account.id, "Holder", frozenset({Role.CREDENTIAL_HOLDER}), operator
    )
    holder = PrincipalContext(
        PrincipalId(principal.id),
        principal.roles,
        TenantId(tenant.id),
        AccountId(account.id),
    )
    await accounts.create_grant(
        account.id, GrantKind.CREDIT, 10, "wei", "fund", None, f"fund-{suffix}", operator
    )
    await accounts.create_rate_card(
        "fixed", None, ExactRate(1, 2, "wei", "fixed"), NOW - timedelta(hours=1), operator
    )
    await accounts.set_capability_policy(account.id, "fixed", None, True, "allow", operator)
    signer_repository = PostgresSignerRepository(
        async_sessionmaker(engine, expire_on_commit=False),
        global_cap=1000,
        signer_id="signer_metering",
        signer_url="https://signer.example",
        discovery_url="https://signer.example",
    )
    signer = SignerService(
        signer_repository,
        "integration-signer-session-pepper",
        "integration-webhook-secret",
        clock=lambda: NOW,
    )
    await signer.initialize()
    session = await signer.create_session(
        holder, "fixed", None, "preflight", 4, "wei", 60, f"session-{suffix}"
    )
    state_name = f"state_{suffix}"
    pm_name = f"pm_{state_name}"
    payment = PaymentState(
        state_name,
        pm_name,
        SIGNED_TIME,
        "0x" + "a" * 40,
        "preflight",
        0,
        1,
        "0",
        1,
        2,
        "fixed",
        0,
        "",
    )
    admission = await signer.authorize(session.token, "signer_metering", payment)
    assert admission.allowed and admission.reserved_amount == 1

    consumer_group = f"metering-{suffix}"
    repository = PostgresMeteringRepository(
        async_sessionmaker(engine, expire_on_commit=False),
        consumer_group=consumer_group,
        topic="gateway-events",
        signer_id="signer_metering",
    )
    service = MeteringService(repository, decode_go_livepeer)
    open_before = await service.list_open_reservations(holder, None, 50, None)
    assert len(open_before) == 1 and open_before[0].reserved_amount == 1
    first_id = str(uuid4())
    payload = (
        gateway_event(first_id)
        .replace(b"state_metering", state_name.encode())
        .replace(b"pm_state_metering", pm_name.encode())
        .replace(b'"replaced"', ('"' + session.id + '"').encode())
    )
    settled = await service.process(
        TransportRecord("gateway-events", 0, 0, first_id.encode(), payload, NOW)
    )
    assert settled.outcome is MeteringOutcome.SETTLED and settled.next_offset == 1
    canonical_replay = await service.process(
        TransportRecord("gateway-events", 5, 0, first_id.encode(), payload, NOW)
    )
    assert canonical_replay.outcome is MeteringOutcome.DUPLICATE
    canonical_fork = await service.process(
        TransportRecord(
            "gateway-events",
            6,
            0,
            first_id.encode(),
            payload.replace(b'"request_metering"', b'"request_changed"'),
            NOW,
        )
    )
    assert canonical_fork.reason is QuarantineReason.FORKED_OBSERVATION

    second_id = str(uuid4())
    duplicate_payload = (
        gateway_event(second_id)
        .replace(b"state_metering", state_name.encode())
        .replace(b"pm_state_metering", pm_name.encode())
        .replace(b'"replaced"', ('"' + session.id + '"').encode())
    )
    duplicate = await service.process(
        TransportRecord("gateway-events", 0, 1, second_id.encode(), duplicate_payload, NOW)
    )
    assert duplicate.outcome is MeteringOutcome.DUPLICATE
    semantic_fork_id = str(uuid4())
    semantic_fork_payload = (
        gateway_event(semantic_fork_id)
        .replace(b'"num_tickets":1', b'"num_tickets":2')
        .replace(b"state_metering", state_name.encode())
        .replace(b"pm_state_metering", pm_name.encode())
        .replace(b'"replaced"', ('"' + session.id + '"').encode())
    )
    semantic_fork = await service.process(
        TransportRecord(
            "gateway-events", 7, 0, semantic_fork_id.encode(), semantic_fork_payload, NOW
        )
    )
    assert semantic_fork.reason is QuarantineReason.FORKED_OBSERVATION

    fork_id = str(uuid4())
    fork_payload = (
        gateway_event(fork_id, fee="0")
        .replace(b"state_metering", state_name.encode())
        .replace(b"pm_state_metering", pm_name.encode())
        .replace(b'"replaced"', ('"' + session.id + '"').encode())
    )
    fork = await service.process(
        TransportRecord("gateway-events", 0, 2, fork_id.encode(), fork_payload, NOW)
    )
    assert fork.outcome is MeteringOutcome.QUARANTINED
    assert fork.reason is QuarantineReason.FEE_MISMATCH
    divergence = await service.process(
        TransportRecord("gateway-events", 0, 2, fork_id.encode(), b"different", NOW)
    )
    assert divergence.reason is QuarantineReason.TRANSPORT_DIVERGENCE
    poison = await service.process(TransportRecord("gateway-events", 2, 0, None, None, NOW))
    assert poison.reason is QuarantineReason.INVALID_SCHEMA
    ignored_payload = json.dumps(
        {"id": str(uuid4()), "type": "discovery_results", "timestamp": "0", "data": []}
    ).encode()
    ignored = await service.process(
        TransportRecord("gateway-events", 3, 0, None, ignored_payload, NOW)
    )
    assert ignored.outcome is MeteringOutcome.IGNORED
    unknown_id = str(uuid4())
    unknown_payload = (
        gateway_event(unknown_id)
        .replace(b"state_metering", b"state_unknown")
        .replace(b'"replaced"', ('"' + session.id + '"').encode())
    )
    unknown = await service.process(
        TransportRecord("gateway-events", 4, 0, unknown_id.encode(), unknown_payload, NOW)
    )
    assert unknown.reason is QuarantineReason.SEQUENCE_GAP

    async with engine.connect() as connection:
        receipt = (
            await connection.execute(
                text("SELECT id,status FROM authorization_receipts WHERE session_id=:id"),
                {"id": session.id},
            )
        ).one()
        lease = (
            await connection.execute(
                text("SELECT available,pending,settled FROM leases WHERE session_id=:id"),
                {"id": session.id},
            )
        ).one()
        postings = (
            (
                await connection.execute(
                    text(
                        "SELECT p.amount FROM ledger_postings p JOIN charges c "
                        "ON c.ledger_transaction_id=p.transaction_id "
                        "WHERE c.reservation_id=:receipt"
                    ),
                    {"receipt": receipt.id},
                )
            )
            .scalars()
            .all()
        )
        occurred_ns = await connection.scalar(text("SELECT occurred_at_ns FROM usage_events"))
        checkpoint = await connection.scalar(
            text("SELECT next_offset FROM metering_checkpoints WHERE consumer_group=:group"),
            {"group": consumer_group},
        )
        assert receipt.status == "settled"
        assert tuple(map(int, lease)) == (3, 0, 1)
        assert sorted(map(int, postings)) == [-1, 1]
        assert int(occurred_ns) % 1_000_000 == 1
        assert checkpoint == 3
        assert (
            await connection.scalar(text("SELECT count(*) FROM metering_position_conflicts")) == 1
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM metering_outbox WHERE published_at IS NULL")
            )
            >= 3
        )

    late_session = await signer.create_session(
        holder, "fixed", None, "preflight", 4, "wei", 60, f"late-session-{suffix}"
    )
    late_state = f"state_late_{suffix}"
    late_pm = f"pm_{late_state}"
    late_payment = PaymentState(
        late_state,
        late_pm,
        SIGNED_TIME,
        "0x" + "a" * 40,
        "preflight",
        0,
        2,
        "0",
        1,
        2,
        "fixed",
        0,
        "",
    )
    assert (await signer.authorize(late_session.token, "signer_metering", late_payment)).allowed
    assert await repository.reconcile_pending(NOW + timedelta(seconds=29)) == 0
    assert await repository.reconcile_pending(NOW + timedelta(seconds=61)) >= 1
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT status FROM authorization_receipts WHERE session_id=:id"),
                {"id": late_session.id},
            )
            == "unresolved"
        )
        assert (
            await connection.scalar(
                text("""SELECT count(*) FROM reconciliation_cases WHERE reservation_id IN
              (SELECT id FROM authorization_receipts WHERE session_id=:id) AND status='open'"""),
                {"id": late_session.id},
            )
            == 1
        )
    late_id = str(uuid4())
    late_payload = (
        gateway_event(late_id)
        .replace(b"state_metering", late_state.encode())
        .replace(b"pm_state_metering", late_pm.encode())
        .replace(b'"replaced"', ('"' + late_session.id + '"').encode())
    )
    late = await service.process(
        TransportRecord(
            "gateway-events", 1, 0, late_id.encode(), late_payload, NOW + timedelta(seconds=62)
        )
    )
    assert late.outcome is MeteringOutcome.SETTLED
    async with engine.connect() as connection:
        lease = (
            await connection.execute(
                text("SELECT available,pending,settled FROM leases WHERE session_id=:id"),
                {"id": late_session.id},
            )
        ).one()
        assert tuple(map(int, lease)) == (0, 0, 1)
        assert (
            await connection.scalar(
                text("""SELECT count(*) FROM reconciliation_cases WHERE reservation_id IN
              (SELECT id FROM authorization_receipts WHERE session_id=:id) AND status='open'"""),
                {"id": late_session.id},
            )
            == 0
        )
        assert (
            await connection.scalar(
                text("SELECT open_lease_exposure FROM account_exposures WHERE account_id=:id"),
                {"id": account.id},
            )
            == 0
        )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE principals SET display_name='forged' "
                    "WHERE id='principal_system_metering'"
                )
            )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO reconciliation_cases(id,reservation_id,kind,status,reason,
                  evidence,created_at) VALUES (:id,:receipt,'fee_mismatch','open','fee_mismatch',
                  '{}'::jsonb,now())"""),
                {"id": f"recon_forged_{uuid4().hex}", "receipt": receipt.id},
            )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO metering_observations(id,producer_id,consumer_group,topic,
                  partition,kafka_offset,broker_beginning_offset,payload_sha256,outcome,received_at,
                  processed_at) VALUES (:id,'signer_metering','forged-group','gateway-events',0,5,5,
                  :hash,'ignored',now(),now())"""),
                {"id": f"observation_forged_{uuid4().hex}", "hash": "a" * 64},
            )
            await connection.execute(
                text("""INSERT INTO metering_checkpoints(consumer_group,topic,partition,next_offset,
                  updated_at) VALUES ('forged-group','gateway-events',0,6,now())""")
            )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO metering_observations(id,producer_id,consumer_group,topic,
                  partition,kafka_offset,broker_beginning_offset,payload_sha256,outcome,received_at,
                  processed_at) VALUES (:id,'signer_metering','forged-retained','gateway-events',0,
                  5,0,:hash,'ignored',now(),now())"""),
                {"id": f"observation_retained_{uuid4().hex}", "hash": "b" * 64},
            )
            await connection.execute(
                text("""INSERT INTO metering_checkpoints(consumer_group,topic,partition,next_offset,
                  updated_at) VALUES ('forged-retained','gateway-events',0,6,now())""")
            )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO metering_observations(id,producer_id,consumer_group,topic,
                  partition,kafka_offset,broker_beginning_offset,payload_sha256,outcome,received_at,
                  processed_at) VALUES (:id,'signer_metering',:group,'gateway-events',0,5,0,
                  :hash,'ignored',now(),now())"""),
                {
                    "id": f"observation_update_{uuid4().hex}",
                    "group": consumer_group,
                    "hash": "c" * 64,
                },
            )
            await connection.execute(
                text("""UPDATE metering_checkpoints SET next_offset=6,updated_at=now()
                  WHERE consumer_group=:group AND topic='gateway-events' AND partition=0"""),
                {"group": consumer_group},
            )
    await engine.dispose()
