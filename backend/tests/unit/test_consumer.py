from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiokafka.errors import TopicAlreadyExistsError  # type: ignore[import-untyped]

from clearinghouse.infrastructure import consumer as consumer_module
from clearinghouse.infrastructure.consumer import MeteringConsumer


class Admin:
    instances: list[Admin] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.start = AsyncMock()
        self.close = AsyncMock()
        self.create_topics = AsyncMock()
        self.alter_configs = AsyncMock()
        self.instances.append(self)


class Records:
    def __init__(self) -> None:
        self.values = iter((SimpleNamespace(value=None), SimpleNamespace(value=b"event")))
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.commit = AsyncMock()

    def __aiter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __anext__(self):  # type: ignore[no-untyped-def]
        try:
            return next(self.values)
        except StopIteration as error:
            raise StopAsyncIteration from error


@pytest.mark.asyncio
async def test_consumer_commits_empty_and_decoded_records(monkeypatch: pytest.MonkeyPatch) -> None:
    Admin.instances.clear()
    records = Records()
    monkeypatch.setattr(consumer_module, "AIOKafkaConsumer", lambda *args, **kwargs: records)
    monkeypatch.setattr(consumer_module, "AIOKafkaAdminClient", Admin)
    service = SimpleNamespace(ingest=AsyncMock())

    def decoder(payload: bytes) -> str | None:
        return "decoded" if payload == b"event" else None

    consumer = MeteringConsumer(
        cast(Any, service),
        cast(Any, decoder),
        bootstrap_servers="broker:9092",
        topic="events",
        group_id="core",
    )
    await consumer._consume()
    records.start.assert_awaited_once()
    records.stop.assert_awaited_once()
    assert records.commit.await_count == 2
    service.ingest.assert_awaited_once_with("decoded")
    assert not consumer.connected
    admin = Admin.instances[0]
    admin.start.assert_awaited_once()
    admin.create_topics.assert_awaited_once()
    admin.alter_configs.assert_awaited_once()
    admin.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_consumer_supervision_retries_and_stops(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    service = SimpleNamespace(ingest=AsyncMock())
    consumer = MeteringConsumer(
        cast(Any, service),
        cast(Any, lambda _payload: None),
        bootstrap_servers="broker:9092",
        topic="events",
        group_id="core",
    )
    attempts = 0
    real_sleep = asyncio.sleep

    async def consume() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("broker unavailable")
        consumer.stop()

    async def no_wait(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(consumer, "_consume", consume)
    monkeypatch.setattr("clearinghouse.infrastructure.consumer.asyncio.sleep", no_wait)
    await consumer.run()
    assert attempts == 2 and not consumer.running
    assert "metering consumer failed; retrying" in caplog.text


@pytest.mark.asyncio
async def test_consumer_commits_deterministic_poison_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Admin.instances.clear()
    records = Records()
    records.values = iter((SimpleNamespace(value=b"malformed"),))
    monkeypatch.setattr(consumer_module, "AIOKafkaConsumer", lambda *args, **kwargs: records)
    monkeypatch.setattr(consumer_module, "AIOKafkaAdminClient", Admin)
    service = SimpleNamespace(ingest=AsyncMock())

    def reject(_payload: bytes) -> None:
        raise ValueError("invalid event")

    consumer = MeteringConsumer(
        cast(Any, service),
        cast(Any, reject),
        bootstrap_servers="broker:9092",
        topic="events",
        group_id="core",
    )
    await consumer._consume()
    records.commit.assert_awaited_once()
    service.ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_topic_policy_converges_when_topic_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Existing(Admin):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self.create_topics = AsyncMock(side_effect=TopicAlreadyExistsError())

    Admin.instances.clear()
    monkeypatch.setattr(consumer_module, "AIOKafkaAdminClient", Existing)
    consumer = MeteringConsumer(
        cast(Any, SimpleNamespace()),
        cast(Any, lambda _payload: None),
        bootstrap_servers="broker:9092",
        topic="events",
        group_id="core",
        retention_ms=700_000_000,
    )
    await consumer._ensure_topic()
    admin = Admin.instances[0]
    call = admin.alter_configs.await_args
    assert call is not None
    resource = call.args[0][0]
    assert resource.configs == {"cleanup.policy": "delete", "retention.ms": "700000000"}
