"""Real-broker qualification for durable metering offset progression."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer  # type: ignore[import-untyped]
from aiokafka.admin import AIOKafkaAdminClient, RecordsToDelete  # type: ignore[import-untyped]
from aiokafka.structs import ConsumerRecord, TopicPartition  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clearinghouse.adapters.kafka import KafkaMeteringConsumer, decode_go_livepeer
from clearinghouse.application.metering import MeteringService
from clearinghouse.domain.metering import TransportRecord
from clearinghouse.infrastructure.metering import PostgresMeteringRepository

DATABASE_URL = os.getenv("CLEARINGHOUSE_TEST_DATABASE_URL")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("CLEARINGHOUSE_TEST_KAFKA_BOOTSTRAP_SERVERS")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None or KAFKA_BOOTSTRAP_SERVERS is None,
    reason="live PostgreSQL and Redpanda are not configured",
)


class _FiniteConsumer(KafkaMeteringConsumer):
    """Use the production consumer for a bounded number of broker records."""

    record_count = 1

    async def _messages(self) -> AsyncIterator[ConsumerRecord[Any, bytes | None]]:
        for _ in range(self.record_count):
            yield await asyncio.wait_for(self.consumer.getone(), timeout=15)


def _payload(index: int) -> bytes:
    return json.dumps(
        {
            "id": str(uuid4()),
            "type": "network_capabilities",
            "timestamp": "0",
            "gateway": "",
            "data": [index],
        },
        separators=(",", ":"),
    ).encode()


async def _produce(topic: str, count: int) -> list[tuple[int, int, bytes]]:
    assert KAFKA_BOOTSTRAP_SERVERS is not None
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        client_id=f"{topic}-producer",
    )
    records: list[tuple[int, int, bytes]] = []
    await producer.start()
    try:
        for index in range(count):
            payload = _payload(index)
            metadata = await producer.send_and_wait(
                topic, payload, key=f"mixed-topic-event-{index}".encode()
            )
            records.append((metadata.partition, metadata.offset, payload))
    finally:
        await producer.stop()
    return records


async def _broker_offset(group: str, topic: str, partition: int) -> int | None:
    assert KAFKA_BOOTSTRAP_SERVERS is not None
    verifier = AIOKafkaConsumer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=group,
        client_id=f"{group}-verifier",
        enable_auto_commit=False,
    )
    await verifier.start()
    try:
        return cast(int | None, await verifier.committed(TopicPartition(topic, partition)))
    finally:
        await verifier.stop()


async def _commit_broker_offset(group: str, topic: str, partition: int, offset: int) -> None:
    assert KAFKA_BOOTSTRAP_SERVERS is not None
    committer = AIOKafkaConsumer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=group,
        client_id=f"{group}-committer",
        enable_auto_commit=False,
    )
    await committer.start()
    try:
        topic_partition = TopicPartition(topic, partition)
        committer.assign([topic_partition])
        await committer.commit({topic_partition: offset})
    finally:
        await committer.stop()


async def test_redpanda_commit_matches_durable_database_checkpoint() -> None:
    assert DATABASE_URL is not None
    assert KAFKA_BOOTSTRAP_SERVERS is not None
    suffix = uuid4().hex
    topic = f"clearinghouse-metering-test-{suffix}"
    group = f"clearinghouse-metering-test-{suffix}"
    partition, offset, _payload_value = (await _produce(topic, 1))[0]

    engine = create_async_engine(DATABASE_URL)
    repository = PostgresMeteringRepository(
        async_sessionmaker(engine, expire_on_commit=False),
        consumer_group=group,
        topic=topic,
        signer_id="signer_redpanda_test",
    )
    service = MeteringService(repository, decode_go_livepeer)
    consumer = _FiniteConsumer(
        service,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        topic=topic,
        group_id=group,
        client_id=f"{group}-consumer",
    )

    try:
        await consumer.run()
        expected_next_offset = offset + 1
        async with engine.connect() as connection:
            observation = (
                await connection.execute(
                    text("""
                    SELECT outcome,kafka_offset FROM metering_observations
                    WHERE consumer_group=:group AND topic=:topic AND partition=:partition
                    """),
                    {"group": group, "topic": topic, "partition": partition},
                )
            ).one()
            database_offset = await connection.scalar(
                text("""
                SELECT next_offset FROM metering_checkpoints
                WHERE consumer_group=:group AND topic=:topic AND partition=:partition
                """),
                {"group": group, "topic": topic, "partition": partition},
            )
        broker_offset = await _broker_offset(group, topic, partition)

        assert observation.outcome == "ignored"
        assert observation.kafka_offset == offset
        assert database_offset == expected_next_offset
        assert broker_offset == database_offset
    finally:
        await engine.dispose()


async def test_database_checkpoint_rewinds_an_ahead_broker_group() -> None:
    assert DATABASE_URL is not None
    assert KAFKA_BOOTSTRAP_SERVERS is not None
    suffix = uuid4().hex
    topic = f"clearinghouse-metering-rewind-{suffix}"
    group = f"clearinghouse-metering-rewind-{suffix}"
    records = await _produce(topic, 4)
    partition = records[0][0]
    assert [(record_partition, offset) for record_partition, offset, _ in records] == [
        (partition, 0),
        (partition, 1),
        (partition, 2),
        (partition, 3),
    ]

    engine = create_async_engine(DATABASE_URL)
    repository = PostgresMeteringRepository(
        async_sessionmaker(engine, expire_on_commit=False),
        consumer_group=group,
        topic=topic,
        signer_id="signer_redpanda_rewind",
    )
    service = MeteringService(repository, decode_go_livepeer)
    first = records[0]
    seeded = await service.process(
        TransportRecord(
            topic,
            first[0],
            first[1],
            b"mixed-topic-event-0",
            first[2],
            datetime.now(UTC),
            0,
        )
    )
    assert seeded.next_offset == 1
    await _commit_broker_offset(group, topic, partition, 3)
    assert await _broker_offset(group, topic, partition) == 3

    consumer = _FiniteConsumer(
        service,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        topic=topic,
        group_id=group,
        client_id=f"{group}-consumer",
    )
    consumer.record_count = 4
    try:
        await consumer.run()
        async with engine.connect() as connection:
            observed_offsets = (
                (
                    await connection.execute(
                        text("""
                    SELECT kafka_offset FROM metering_observations
                    WHERE consumer_group=:group AND topic=:topic AND partition=:partition
                    ORDER BY kafka_offset
                    """),
                        {"group": group, "topic": topic, "partition": partition},
                    )
                )
                .scalars()
                .all()
            )
            database_offset = await connection.scalar(
                text("""
                SELECT next_offset FROM metering_checkpoints
                WHERE consumer_group=:group AND topic=:topic AND partition=:partition
                """),
                {"group": group, "topic": topic, "partition": partition},
            )
        assert list(observed_offsets) == [0, 1, 2, 3]
        assert database_offset == 4
        assert await _broker_offset(group, topic, partition) == database_offset
    finally:
        await engine.dispose()


async def test_redpanda_retention_gap_is_durable_and_visible() -> None:
    assert DATABASE_URL is not None
    assert KAFKA_BOOTSTRAP_SERVERS is not None
    suffix = uuid4().hex
    topic = f"clearinghouse-metering-gap-{suffix}"
    group = f"clearinghouse-metering-gap-{suffix}"
    records = await _produce(topic, 3)
    partition = records[0][0]
    topic_partition = TopicPartition(topic, partition)

    admin = AIOKafkaAdminClient(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS, client_id=f"{group}-admin"
    )
    await admin.start()
    try:
        low_watermarks = await admin.delete_records(
            {topic_partition: RecordsToDelete(before_offset=2)}
        )
    finally:
        await admin.close()
    assert low_watermarks[topic_partition] == 2

    engine = create_async_engine(DATABASE_URL)
    repository = PostgresMeteringRepository(
        async_sessionmaker(engine, expire_on_commit=False),
        consumer_group=group,
        topic=topic,
        signer_id="signer_redpanda_gap",
    )
    service = MeteringService(repository, decode_go_livepeer)
    consumer = _FiniteConsumer(
        service,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        topic=topic,
        group_id=group,
        client_id=f"{group}-consumer",
    )
    try:
        await consumer.run()
        async with engine.connect() as connection:
            gap = (
                await connection.execute(
                    text("""
                    SELECT expected_offset,observed_offset,reason FROM metering_transport_gaps
                    WHERE consumer_group=:group AND topic=:topic AND partition=:partition
                    """),
                    {"group": group, "topic": topic, "partition": partition},
                )
            ).one()
            case = (
                await connection.execute(
                    text("""
                    SELECT kind,status,reason FROM reconciliation_cases
                    WHERE kind='transport_gap' AND evidence->>'gap_id' IN (
                      SELECT id FROM metering_transport_gaps
                      WHERE consumer_group=:group AND topic=:topic AND partition=:partition
                    )
                    """),
                    {"group": group, "topic": topic, "partition": partition},
                )
            ).one()
            database_offset = await connection.scalar(
                text("""
                SELECT next_offset FROM metering_checkpoints
                WHERE consumer_group=:group AND topic=:topic AND partition=:partition
                """),
                {"group": group, "topic": topic, "partition": partition},
            )
        assert tuple(gap) == (0, 2, "retention_gap")
        assert tuple(case) == ("transport_gap", "open", "transport_gap")
        assert database_offset == 3
        assert await _broker_offset(group, topic, partition) == database_offset
    finally:
        await engine.dispose()
