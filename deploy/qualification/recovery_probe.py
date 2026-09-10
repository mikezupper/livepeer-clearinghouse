"""In-network, bounded recovery probes using production metering adapters."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]
from aiokafka.admin import AIOKafkaAdminClient, RecordsToDelete  # type: ignore[import-untyped]
from aiokafka.structs import ConsumerRecord, TopicPartition  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clearinghouse.adapters.kafka import KafkaMeteringConsumer, decode_go_livepeer
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
from clearinghouse.domain.signer import PaymentState
from clearinghouse.infrastructure.accounts import PostgresAccountsRepository
from clearinghouse.infrastructure.config import Settings
from clearinghouse.infrastructure.metering import PostgresMeteringRepository
from clearinghouse.infrastructure.signer import PostgresSignerRepository


class FiniteConsumer(KafkaMeteringConsumer):
    """Run the production consumer against an exact, bounded record count."""

    def __init__(self, *args: Any, record_count: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.record_count = record_count

    async def _messages(self) -> AsyncIterator[ConsumerRecord[Any, bytes | None]]:
        for _index in range(self.record_count):
            yield await asyncio.wait_for(self.consumer.getone(), timeout=20)


def signed_event(event_id: str, state_id: str, auth_id: str, signed_time: str) -> bytes:
    """Construct the pinned create_signed_ticket wire shape without floats."""
    timestamp_ms = int(
        datetime.fromisoformat(signed_time.replace("Z", "+00:00")).timestamp() * 1000
    )
    return json.dumps(
        {
            "id": event_id,
            "type": "create_signed_ticket",
            "timestamp": str(timestamp_ms),
            "gateway": "",
            "data": {
                "session_id": state_id,
                "session_status": "new",
                "app": "qualification",
                "pipeline": "fixed",
                "request_id": "recovery-request",
                "orch_address": "0x" + "a" * 40,
                "orch_url": "",
                "manifest_id": "recovery-manifest",
                "pm_session_id": f"pm_{state_id}",
                "current_time": signed_time,
                "current_time_unix": timestamp_ms,
                "previous_time": signed_time,
                "previous_time_unix": timestamp_ms,
                "billable_secs": 0,
                "pixels": 0,
                "session_balance": "0",
                "computed_fee": "1",
                "cost": "1.0000000000",
                "sequence_number": 0,
                "num_tickets": 1,
                "auth_id": auth_id,
            },
        },
        separators=(",", ":"),
    ).encode()


async def produce(
    bootstrap: str, topic: str, records: list[tuple[bytes | None, bytes]]
) -> list[int]:
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap, client_id="recovery-qualification")
    await producer.start()
    try:
        offsets = []
        for key, value in records:
            metadata = await producer.send_and_wait(topic, value, key=key)
            if metadata.partition != 0:
                raise RuntimeError("qualification topic unexpectedly has multiple partitions")
            offsets.append(int(metadata.offset))
        return offsets
    finally:
        await producer.stop()


def metering(settings: Settings, engine: Any, topic: str, group: str) -> MeteringService:
    repository = PostgresMeteringRepository(
        async_sessionmaker(engine, expire_on_commit=False),
        consumer_group=group,
        topic=topic,
        signer_id=settings.signer_id,
    )
    return MeteringService(repository, decode_go_livepeer)


async def consume(
    settings: Settings, service: MeteringService, topic: str, group: str, count: int
) -> None:
    consumer = FiniteConsumer(
        service,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topic=topic,
        group_id=group,
        client_id=f"{group}-probe",
        record_count=count,
    )
    await consumer.run()


async def seed(settings: Settings, run_id: str) -> dict[str, object]:
    now = datetime.now(UTC).replace(microsecond=0)
    signed_time = now.isoformat(timespec="seconds").replace("+00:00", ".000000000Z")
    topic = f"qualification-recovery-{run_id}"
    group = f"qualification-recovery-{run_id}"
    engine = create_async_engine(settings.database_url)
    try:
        accounts = AccountService(PostgresAccountsRepository(engine), b"qualification-pepper")
        operator_id = f"principal_recovery_operator_{run_id}"
        operator = PrincipalContext(PrincipalId(operator_id), frozenset({Role.OPERATOR}))
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO principals(id) VALUES (:id)"), {"id": operator_id}
            )
            await connection.execute(
                text("INSERT INTO principal_roles(principal_id,role) VALUES (:id,'operator')"),
                {"id": operator_id},
            )
        tenant = await accounts.create_tenant(f"Recovery {run_id}", operator)
        account = await accounts.create_account(tenant.id, "Recovery payer", "wei", 10, operator)
        principal = await accounts.create_principal(
            tenant.id, account.id, "Recovery holder", frozenset({Role.CREDENTIAL_HOLDER}), operator
        )
        holder = PrincipalContext(
            PrincipalId(principal.id),
            principal.roles,
            tenant_id=TenantId(tenant.id),
            account_id=AccountId(account.id),
        )
        await accounts.create_grant(
            account.id,
            GrantKind.CREDIT,
            10,
            "wei",
            "qualification",
            None,
            f"fund-{run_id}",
            operator,
        )
        await accounts.create_rate_card(
            "fixed", None, ExactRate(1, 2, "wei", "fixed"), now - timedelta(minutes=1), operator
        )
        await accounts.set_capability_policy(
            account.id, "fixed", None, True, "qualification", operator
        )
        signer = SignerService(
            PostgresSignerRepository(
                async_sessionmaker(engine, expire_on_commit=False),
                global_cap=1000,
                signer_id=settings.signer_id,
                signer_url=settings.signer_url,
                discovery_url=settings.signer_discovery_url,
            ),
            "qualification-session-pepper",
            "qualification-webhook-secret",
            clock=lambda: now,
        )
        await signer.initialize()
        session = await signer.create_session(
            holder, "fixed", None, "qualification", 4, "wei", 300, f"session-{run_id}"
        )
        state_id = f"state_recovery_{run_id}"
        payment = PaymentState(
            state_id,
            f"pm_{state_id}",
            signed_time,
            "0x" + "a" * 40,
            "qualification",
            0,
            1,
            "0",
            1,
            2,
            "fixed",
            0,
            "",
        )
        admission = await signer.authorize(session.token, settings.signer_id, payment)
        if not admission.allowed:
            raise RuntimeError("fixture authorization was denied")
        event_id = str(uuid4())
        payload = signed_event(event_id, state_id, session.id, signed_time)
        padding = json.dumps(
            {
                "id": str(uuid4()),
                "type": "network_capabilities",
                "timestamp": "0",
                "gateway": "",
                "data": [],
            },
            separators=(",", ":"),
        ).encode()
        offsets = await produce(
            settings.kafka_bootstrap_servers,
            topic,
            [
                (event_id.encode(), payload),
                (event_id.encode(), payload),
                (b"poison", b"not-json"),
                (b"padding", padding),
            ],
        )
        await consume(settings, metering(settings, engine, topic, group), topic, group, 4)
        async with engine.connect() as connection:
            outcome_rows = list(
                (
                    await connection.execute(
                        text("""SELECT outcome,reason FROM metering_observations
                        WHERE consumer_group=:group AND topic=:topic ORDER BY kafka_offset"""),
                        {"group": group, "topic": topic},
                    )
                ).tuples()
            )
            outcomes = [str(row.outcome) for row in outcome_rows]
            reasons = [str(row.reason) if row.reason is not None else None for row in outcome_rows]
            charge_count = int(
                await connection.scalar(
                    text("SELECT count(*) FROM charges WHERE account_id=:account"),
                    {"account": account.id},
                )
                or 0
            )
            checkpoint = int(
                await connection.scalar(
                    text("""SELECT next_offset FROM metering_checkpoints
                    WHERE consumer_group=:group AND topic=:topic AND partition=0"""),
                    {"group": group, "topic": topic},
                )
                or -1
            )
        if offsets != [0, 1, 2, 3] or outcomes[:3] != [
            "settled",
            "duplicate",
            "quarantined",
        ]:
            raise RuntimeError(
                "settlement, replay, and poison outcomes were not exact: "
                f"offsets={offsets!r} outcomes={outcomes!r} reasons={reasons!r} "
                f"charge_count={charge_count} checkpoint={checkpoint}"
            )
        if charge_count != 1 or checkpoint not in {3, 4}:
            raise RuntimeError("charge idempotency or durable checkpoint invariant failed")
        return {
            "topic": topic,
            "group": group,
            "outcomes": outcomes,
            "reasons": reasons,
            "charge_count": charge_count,
            "checkpoint": checkpoint,
            "poison": "durably_quarantined",
        }
    finally:
        await engine.dispose()


async def verify(settings: Settings, run_id: str) -> dict[str, object]:
    topic = f"qualification-recovery-{run_id}"
    group = f"qualification-recovery-{run_id}"
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""SELECT count(*) FILTER (WHERE outcome='settled'),
                    count(*) FILTER (WHERE outcome='duplicate'),
                    count(*) FILTER (WHERE outcome='quarantined')
                    FROM metering_observations WHERE consumer_group=:group AND topic=:topic"""),
                    {"group": group, "topic": topic},
                )
            ).one()
            charge_count = int(
                await connection.scalar(
                    text("""SELECT count(*) FROM charges c JOIN accounts a ON a.id=c.account_id
                    WHERE a.display_name='Recovery payer'"""),
                )
                or 0
            )
            checkpoint = int(
                await connection.scalar(
                    text("""SELECT next_offset FROM metering_checkpoints
                    WHERE consumer_group=:group AND topic=:topic AND partition=0"""),
                    {"group": group, "topic": topic},
                )
                or -1
            )
        if tuple(row) != (1, 1, 1) or charge_count != 1 or checkpoint not in {3, 4}:
            raise RuntimeError("durable recovery changed financial or quarantine state")
        return {
            "outcomes": [int(value) for value in row],
            "charge_count": charge_count,
            "checkpoint": checkpoint,
        }
    finally:
        await engine.dispose()


async def gap(settings: Settings, run_id: str) -> dict[str, object]:
    topic = f"qualification-gap-{run_id}"
    group = f"qualification-gap-{run_id}"
    payloads: list[tuple[bytes | None, bytes]] = [
        (
            f"gap-{index}".encode(),
            json.dumps(
                {
                    "id": str(uuid4()),
                    "type": "network_capabilities",
                    "timestamp": "0",
                    "gateway": "",
                    "data": [index],
                }
            ).encode(),
        )
        for index in range(3)
    ]
    offsets = await produce(settings.kafka_bootstrap_servers, topic, payloads)
    partition = TopicPartition(topic, 0)
    admin = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers, client_id=f"{group}-admin"
    )
    await admin.start()
    try:
        watermarks = await admin.delete_records({partition: RecordsToDelete(before_offset=2)})
    finally:
        await admin.close()
    if offsets != [0, 1, 2] or int(watermarks[partition]) != 2:
        raise RuntimeError("controlled broker retention gap was not established")
    engine = create_async_engine(settings.database_url)
    try:
        await consume(settings, metering(settings, engine, topic, group), topic, group, 1)
        async with engine.connect() as connection:
            gap_row = (
                await connection.execute(
                    text("""SELECT expected_offset,observed_offset,reason
                    FROM metering_transport_gaps WHERE consumer_group=:group AND topic=:topic"""),
                    {"group": group, "topic": topic},
                )
            ).one()
            case_row = (
                await connection.execute(
                    text("""SELECT kind,status,reason FROM reconciliation_cases
                    WHERE kind='transport_gap' AND evidence->>'gap_id' IN
                    (SELECT id FROM metering_transport_gaps
                     WHERE consumer_group=:group AND topic=:topic)"""),
                    {"group": group, "topic": topic},
                )
            ).one()
            checkpoint = int(
                await connection.scalar(
                    text("""SELECT next_offset FROM metering_checkpoints
                    WHERE consumer_group=:group AND topic=:topic AND partition=0"""),
                    {"group": group, "topic": topic},
                )
                or -1
            )
        if tuple(gap_row) != (0, 2, "retention_gap"):
            raise RuntimeError("transport gap was not durably recorded")
        if tuple(case_row) != ("transport_gap", "open", "transport_gap") or checkpoint != 3:
            raise RuntimeError("transport gap was not surfaced for explicit reconciliation")
        return {
            "expected_offset": 0,
            "observed_offset": 2,
            "checkpoint": checkpoint,
            "reconciliation": "open",
            "charge_count": 0,
        }
    finally:
        await engine.dispose()


async def run(command: str, run_id: str) -> dict[str, object]:
    settings = Settings()
    if command == "seed":
        return await seed(settings, run_id)
    if command == "verify":
        return await verify(settings, run_id)
    return await gap(settings, run_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "verify", "gap"))
    parser.add_argument("--run-id", required=True)
    arguments = parser.parse_args()
    if not arguments.run_id.isalnum() or len(arguments.run_id) > 20:
        parser.error("run-id must be 1..20 alphanumeric characters")
    print(json.dumps(asyncio.run(run(arguments.command, arguments.run_id)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
