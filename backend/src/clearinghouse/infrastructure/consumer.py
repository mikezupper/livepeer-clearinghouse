"""Supervised Kafka consumer for the single-process SQLite deployment."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress

from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]
from aiokafka.admin import AIOKafkaAdminClient, NewTopic  # type: ignore[import-untyped]
from aiokafka.admin.config_resource import (  # type: ignore[import-untyped]
    ConfigResource,
    ConfigResourceType,
)
from aiokafka.errors import TopicAlreadyExistsError  # type: ignore[import-untyped]

from clearinghouse.application.usage import UsageService
from clearinghouse.domain.core import SignedTicketEvent

LOG = logging.getLogger(__name__)


class MeteringConsumer:
    def __init__(
        self,
        service: UsageService,
        decoder: Callable[[bytes], SignedTicketEvent | None],
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        retention_ms: int = 604_800_000,
    ) -> None:
        self.service = service
        self.decoder = decoder
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.group_id = group_id
        self.retention_ms = retention_ms
        self.running = False
        self.connected = False

    async def run(self) -> None:
        self.running = True
        while self.running:
            try:
                await self._consume()
            except asyncio.CancelledError:
                raise
            except Exception:
                if self.running:
                    LOG.exception("metering consumer failed; retrying")
                    await asyncio.sleep(2)

    async def _consume(self) -> None:
        await self._ensure_topic()
        consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await consumer.start()
        self.connected = True
        try:
            async for record in consumer:
                if record.value is None:
                    await consumer.commit()
                    continue
                try:
                    event = self.decoder(record.value)
                except ValueError:
                    # Strict decoding failures are deterministic poison records;
                    # commit them so one malformed producer event cannot stall a
                    # whole partition. Infrastructure/DB failures still escape
                    # and are retried without advancing the offset.
                    await consumer.commit()
                    continue
                if event is not None:
                    await self.service.ingest(event)
                await consumer.commit()
        finally:
            self.connected = False
            await consumer.stop()

    async def _ensure_topic(self) -> None:
        """Create and converge the single-partition signer evidence topic."""
        admin = AIOKafkaAdminClient(
            bootstrap_servers=self.bootstrap_servers,
            client_id="clearinghouse-core-topic-policy",
        )
        await admin.start()
        try:
            with suppress(TopicAlreadyExistsError):
                await admin.create_topics(
                    [
                        NewTopic(
                            self.topic,
                            num_partitions=1,
                            replication_factor=1,
                            topic_configs={
                                "cleanup.policy": "delete",
                                "retention.ms": str(self.retention_ms),
                            },
                        )
                    ]
                )
            await admin.alter_configs(
                [
                    ConfigResource(
                        ConfigResourceType.TOPIC,
                        self.topic,
                        configs={
                            "cleanup.policy": "delete",
                            "retention.ms": str(self.retention_ms),
                        },
                    )
                ]
            )
        finally:
            await admin.close()

    def stop(self) -> None:
        self.running = False
