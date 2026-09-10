"""Pinned go-livepeer Kafka decoding and manual-offset consumer."""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from typing import Any, Literal, cast
from uuid import UUID

from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]
from aiokafka.abc import ConsumerRebalanceListener  # type: ignore[import-untyped]
from aiokafka.structs import ConsumerRecord, TopicPartition  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from clearinghouse.application.metering import MeteringService
from clearinghouse.domain.metering import SignedTicketEvent, TransportRecord
from clearinghouse.domain.signer import epoch_nanoseconds
from clearinghouse.infrastructure.telemetry import (
    Component,
    Reason,
    get_telemetry,
    safe_outcome,
    safe_reason,
)

_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Data(_Strict):
    session_id: str = Field(min_length=1, max_length=256)
    session_status: Literal["new", "continuing"]
    app: str = Field(max_length=512)
    pipeline: Literal["", "live", "live-video-to-video", "fixed"]
    request_id: str = Field(min_length=1, max_length=256)
    orch_address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    orch_url: str = Field(max_length=2048)
    manifest_id: str = Field(min_length=1, max_length=512)
    pm_session_id: str = Field(min_length=1, max_length=512)
    current_time: str
    current_time_unix: int = Field(ge=-(2**63), le=2**63 - 1)
    previous_time: str
    previous_time_unix: int = Field(ge=-(2**63), le=2**63 - 1)
    billable_secs: int | Decimal
    pixels: int = Field(ge=0, le=2**63 - 1)
    session_balance: str = Field(pattern=r"^(0|[1-9][0-9]{0,77})$")
    computed_fee: str = Field(pattern=r"^(0|[1-9][0-9]{0,77})$")
    cost: str = Field(max_length=96, pattern=r"^(0|[1-9][0-9]*)\.\d{10}$")
    sequence_number: int = Field(ge=0, le=2**64 - 1)
    num_tickets: int = Field(ge=1, le=100)
    auth_id: str = Field(min_length=1, max_length=256)

    @field_validator("current_time", "previous_time")
    @classmethod
    def timestamp_is_rfc3339(cls, value: str) -> str:
        if len(value) > 35 or _RFC3339.fullmatch(value) is None:
            raise ValueError("invalid timestamp")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @field_validator("billable_secs")
    @classmethod
    def billable_is_decimal(cls, value: int | Decimal) -> int | Decimal:
        decimal = Decimal(value)
        adjusted = decimal.adjusted() if decimal else 0
        if (
            not decimal.is_finite()
            or adjusted < -324
            or adjusted > 9
            or len(str(decimal)) > 96
            or abs(decimal) > Decimal("9223372036.854775807")
        ):
            raise ValueError("invalid billable duration")
        return value


class _Envelope(_Strict):
    id: str = Field(min_length=36, max_length=36)
    type: Literal["create_signed_ticket"]
    timestamp: str = Field(pattern=r"^-?(0|[1-9][0-9]{0,18})$")
    gateway: str = Field(default="", max_length=512)
    data: _Data

    @field_validator("id")
    @classmethod
    def id_is_canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value.lower():
            raise ValueError("event id must be a canonical UUID")
        return value.lower()

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_int64(cls, value: str) -> str:
        if not -(2**63) <= int(value) <= 2**63 - 1:
            raise ValueError("timestamp outside signed int64")
        return value


class _GenericEnvelope(_Strict):
    id: str = Field(min_length=36, max_length=36)
    type: str = Field(min_length=1, max_length=128)
    timestamp: str = Field(pattern=r"^-?(0|[1-9][0-9]{0,18})$")
    gateway: str = Field(default="", max_length=512)
    data: object

    @field_validator("id")
    @classmethod
    def id_is_canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value.lower():
            raise ValueError("event id must be a canonical UUID")
        return value.lower()

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_int64(cls, value: str) -> str:
        if not -(2**63) <= int(value) <= 2**63 - 1:
            raise ValueError("timestamp outside signed int64")
        return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _reject_nonfinite(_value: str) -> object:
    raise ValueError("non-finite number")


def _canonical_decimal(value: int | Decimal) -> str:
    decimal = Decimal(value)
    if not decimal:
        return "0"
    rendered = format(decimal, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def decode_go_livepeer(payload: bytes) -> SignedTicketEvent | None:
    """Decode only the pinned event type; unrelated shared-topic events are ignored."""
    try:
        raw = json.loads(
            payload,
            parse_float=Decimal,
            parse_constant=_reject_nonfinite,
            object_pairs_hook=_reject_duplicate_keys,
        )
        if not isinstance(raw, dict):
            raise ValueError("invalid_schema")
        generic = _GenericEnvelope.model_validate(raw)
        if generic.type != "create_signed_ticket":
            return None
        envelope = _Envelope.model_validate(raw)
        current_time_ns = epoch_nanoseconds(envelope.data.current_time)
        previous_time_ns = epoch_nanoseconds(envelope.data.previous_time)
        if current_time_ns // 1_000_000 != envelope.data.current_time_unix:
            raise ValueError("invalid_schema")
        if previous_time_ns // 1_000_000 != envelope.data.previous_time_unix:
            raise ValueError("invalid_schema")
        occurred_at = datetime.fromisoformat(envelope.data.current_time.replace("Z", "+00:00"))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
        OverflowError,
        RecursionError,
        DecimalException,
    ) as error:
        raise ValueError("invalid_schema") from error
    data = envelope.data
    return SignedTicketEvent(
        envelope.id,
        occurred_at,
        data.current_time,
        envelope.gateway,
        data.session_id,
        data.session_status,
        data.app,
        data.pipeline,
        data.request_id,
        data.orch_address.lower(),
        data.manifest_id,
        data.pm_session_id,
        data.current_time,
        current_time_ns,
        data.current_time_unix,
        data.previous_time,
        previous_time_ns,
        data.previous_time_unix,
        _canonical_decimal(data.billable_secs),
        data.pixels,
        int(data.session_balance),
        data.cost,
        int(data.computed_fee),
        data.sequence_number,
        data.num_tickets,
        data.auth_id,
    )


class KafkaMeteringConsumer:
    """Process one record transactionally before advancing its broker offset."""

    def __init__(
        self,
        service: MeteringService,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        client_id: str,
        consumer_factory: Callable[..., AIOKafkaConsumer] = AIOKafkaConsumer,
    ) -> None:
        self.service = service
        self.topic = topic
        self.group_id = group_id
        self._seeking: dict[tuple[str, int], int] = {}
        self.consumer = consumer_factory(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            client_id=client_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            isolation_level="read_committed",
        )

    async def run(self) -> None:
        self.consumer.subscribe([self.topic], listener=_MeteringRebalance(self))
        await self.consumer.start()
        get_telemetry().record_adapter_health(Component.METERING, "redpanda", healthy=True)
        try:
            async for message in self._messages():
                partition = TopicPartition(message.topic, message.partition)
                checkpoint = await self.service.checkpoint(message.topic, message.partition)
                beginning = int((await self.consumer.beginning_offsets([partition]))[partition])
                identity = (message.topic, message.partition)
                desired = (
                    checkpoint if checkpoint is not None and checkpoint >= beginning else beginning
                )
                if message.offset != desired:
                    if self._seeking.get(identity) == desired:
                        raise RuntimeError(
                            "Kafka did not return the durable required offset after seek"
                        )
                    self.consumer.seek(partition, desired)
                    self._seeking[identity] = desired
                    continue
                self._seeking.pop(identity, None)
                try:
                    result = await self.service.process(
                        TransportRecord(
                            message.topic,
                            message.partition,
                            message.offset,
                            message.key,
                            message.value,
                            datetime.now(UTC),
                            beginning,
                        )
                    )
                except BaseException:
                    get_telemetry().record_metering(safe_outcome("error"), Reason.STORAGE)
                    raise
                get_telemetry().record_metering(
                    safe_outcome(result.outcome), safe_reason(result.reason)
                )
                if result.next_offset is None:
                    raise RuntimeError("metering repository omitted durable checkpoint")
                await self.consumer.commit({partition: result.next_offset})
                highwater = self.consumer.highwater(partition)
                if highwater is not None:
                    get_telemetry().record_consumer_lag(max(0, highwater - result.next_offset))
        except BaseException:
            get_telemetry().record_adapter_health(Component.METERING, "redpanda", healthy=False)
            raise
        finally:
            await self.consumer.stop()

    def _messages(self) -> AsyncIterator[ConsumerRecord[Any, bytes | None]]:
        return cast(AsyncIterator[ConsumerRecord[Any, bytes | None]], self.consumer.__aiter__())

    def poll_age_seconds(self) -> int | None:
        """Return the worst assigned partition poll age without network I/O."""
        assigned = self.consumer.assignment()
        if not assigned:
            return None
        timestamps = [self.consumer.last_poll_timestamp(partition) for partition in assigned]
        if any(value is None for value in timestamps):
            return None
        oldest = min(cast(list[int], timestamps))
        # aiokafka exposes this value as Unix wall-clock milliseconds, not a
        # monotonic timestamp. Keep both operands in the same clock domain so
        # a consumer that stops fetching eventually becomes stale.
        return max(0, int(time.time() - oldest / 1000))

    async def readiness_poll_age(self) -> int | None:
        """Verify fresh broker metadata, assignment, and consumer fetch progress."""
        if self.topic not in await self.consumer.topics():
            return None
        assigned = self.consumer.assignment()
        if not assigned:
            return None
        aggregate_lag = 0
        for partition in assigned:
            highwater = self.consumer.highwater(partition)
            if highwater is not None:
                position = await self.consumer.position(partition)
                aggregate_lag += max(0, highwater - position)
        get_telemetry().record_consumer_lag(aggregate_lag)
        return self.poll_age_seconds()


class _MeteringRebalance(ConsumerRebalanceListener):  # type: ignore[misc]
    def __init__(self, owner: KafkaMeteringConsumer) -> None:
        self.owner = owner

    async def on_partitions_revoked(self, revoked: list[TopicPartition]) -> None:
        for partition in revoked:
            self.owner._seeking.pop((partition.topic, partition.partition), None)

    async def on_partitions_assigned(self, assigned: list[TopicPartition]) -> None:
        beginnings = await self.owner.consumer.beginning_offsets(assigned)
        endings = await self.owner.consumer.end_offsets(assigned)
        for partition in assigned:
            self.owner._seeking.pop((partition.topic, partition.partition), None)
            checkpoint = await self.owner.service.checkpoint(partition.topic, partition.partition)
            beginning = int(beginnings[partition])
            desired = (
                checkpoint if checkpoint is not None and checkpoint >= beginning else beginning
            )
            if desired > int(endings[partition]):
                raise RuntimeError("durable checkpoint is ahead of the broker high watermark")
            # Broker group offsets are transport state. Always establish the
            # read position from PostgreSQL before waiting for the next record,
            # including when the broker group is already at highwater.
            self.owner.consumer.seek(partition, desired)
