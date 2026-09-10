from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from aiokafka.structs import TopicPartition  # type: ignore[import-untyped]

from clearinghouse.adapters.kafka import (
    KafkaMeteringConsumer,
    _MeteringRebalance,
    decode_go_livepeer,
)
from clearinghouse.application.metering import MeteringService
from clearinghouse.domain.accounts import AccountId, PrincipalContext, PrincipalId, Role, TenantId
from clearinghouse.domain.metering import MeteringOutcome, ProcessResult, TransportRecord
from clearinghouse.infrastructure.metering import (
    PostgresMeteringRepository,
    _decode_cursor,
    encode_cursor,
)

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def event_payload(**updates: object) -> bytes:
    data: dict[str, object] = {
        "session_id": "state_12345678",
        "session_status": "new",
        "app": "livepeer-ai-video-to-video",
        "pipeline": "fixed",
        "request_id": "request_12345678",
        "orch_address": "0x" + "ab" * 20,
        "orch_url": "",
        "manifest_id": "manifest_12345678",
        "pm_session_id": "pm_12345678",
        "current_time": "2026-09-09T00:00:00.000000001Z",
        "current_time_unix": 1788912000000,
        "previous_time": "2026-09-09T00:00:00.000000001Z",
        "previous_time_unix": 1788912000000,
        "billable_secs": 0,
        "pixels": 0,
        "session_balance": "100",
        "computed_fee": "1",
        "cost": "1.0000000000",
        "sequence_number": 0,
        "num_tickets": 1,
        "auth_id": "session_12345678",
    }
    data.update(cast(dict[str, object], updates.pop("data", {})))
    value: dict[str, object] = {
        "id": str(uuid4()),
        "type": "create_signed_ticket",
        "timestamp": "0",
        "gateway": "",
        "data": data,
    }
    value.update(updates)
    return json.dumps(value, separators=(",", ":")).encode()


class Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.next_offset = 1

    async def process(self, *values: Any) -> ProcessResult:
        self.calls.append(values)
        return ProcessResult(MeteringOutcome.SETTLED, next_offset=self.next_offset)

    async def checkpoint(self, _topic: str, _partition: int) -> int | None:
        return None

    async def list_usage(self, *values: Any) -> tuple[()]:
        self.calls.append(values)
        return ()

    async def list_charges(self, *values: Any) -> tuple[()]:
        self.calls.append(values)
        return ()

    async def list_reconciliations(self, *values: Any) -> tuple[()]:
        self.calls.append(values)
        return ()

    async def list_open_reservations(self, *values: Any) -> tuple[()]:
        self.calls.append(values)
        return ()

    async def health(self) -> dict[str, object]:
        return {"status": "ready"}

    async def reconcile_pending(self, _now: datetime) -> int:
        return 2

    async def heartbeat(self, _now: datetime) -> None:
        return None


def record(payload: bytes | None, *, key: bytes | None = None) -> TransportRecord:
    return TransportRecord("events", 0, 3, key, payload, NOW)


def test_decoder_accepts_pinned_shapes_and_preserves_nanoseconds() -> None:
    parsed = decode_go_livepeer(event_payload())
    assert parsed is not None
    assert parsed.gateway == ""
    assert parsed.current_time_ns % 1_000_000 == 1
    assert parsed.occurred_at_text.endswith("000000001Z")

    unrelated = json.dumps(
        {"id": str(uuid4()), "type": "discovery_results", "timestamp": "-1", "data": []}
    ).encode()
    assert decode_go_livepeer(unrelated) is None


@pytest.mark.parametrize(
    "billable",
    [1, -0.1, 1e-10, "1e999999999", "1e-999999999"],
)
def test_decoder_bounds_billable_without_float_or_exponent_bombs(billable: object) -> None:
    raw = event_payload(data={"billable_secs": billable})
    if isinstance(billable, str):
        raw = raw.replace(('"' + billable + '"').encode(), billable.encode())
    if isinstance(billable, str):
        with pytest.raises(ValueError, match="invalid_schema"):
            decode_go_livepeer(raw)
    else:
        assert decode_go_livepeer(raw) is not None


def test_decoder_canonicalizes_equivalent_billable_number_spellings() -> None:
    values = []
    for token in (
        b"1",
        b"1.0",
        b"1e0",
        b"10",
        b"10.0",
        b"10.50",
        b"100",
        b"-10",
        b"-0",
    ):
        raw = event_payload().replace(b'"billable_secs":0', b'"billable_secs":' + token)
        decoded = decode_go_livepeer(raw)
        assert decoded is not None
        values.append(decoded.billable_seconds)
    assert values == ["1", "1", "1", "10", "10", "10.5", "100", "-10", "0"]


@pytest.mark.parametrize(
    "updates",
    [
        {"data": {"current_time": "not-a-time"}},
        {"timestamp": "9223372036854775808"},
        {"id": "00000000-0000-4000-8000-00000000000G"},
        {"data": {"current_time": "2026-99-09T00:00:00Z"}},
        {"data": {"current_time_unix": 0}},
        {"data": {"previous_time_unix": 0}},
    ],
)
def test_decoder_rejects_bounded_but_semantically_invalid_fields(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="invalid_schema"):
        decode_go_livepeer(event_payload(**updates))


def test_metering_cursor_scope_and_quantity_helpers_cover_supported_roles() -> None:
    cursor = encode_cursor(NOW, "usage_12345678")
    assert _decode_cursor(cursor) == (NOW, "usage_12345678")
    for malformed in ("", "not-json", encode_cursor(NOW.replace(tzinfo=None), "usage_12345678")):
        with pytest.raises(ValueError, match="invalid cursor"):
            _decode_cursor(malformed)

    operator = PrincipalContext(PrincipalId("principal_operator0"), frozenset({Role.OPERATOR}))
    tenant_admin = PrincipalContext(
        PrincipalId("principal_admin000"),
        frozenset({Role.TENANT_ADMIN}),
        TenantId("tenant_12345678"),
    )
    holder = PrincipalContext(
        PrincipalId("principal_holder00"),
        frozenset({Role.CREDENTIAL_HOLDER}),
        TenantId("tenant_12345678"),
        AccountId("account_12345678"),
    )
    assert PostgresMeteringRepository._scope(operator, None) == ("TRUE", {})
    assert "account_id" in PostgresMeteringRepository._scope(operator, "account_12345678")[0]
    assert "tenant_id" in PostgresMeteringRepository._scope(tenant_admin, None)[0]
    assert "account_id" in PostgresMeteringRepository._scope(tenant_admin, "account_12345678")[0]
    assert PostgresMeteringRepository._scope(holder, None)[1]["account"] == "account_12345678"

    decoded = decode_go_livepeer(event_payload())
    assert decoded is not None
    assert PostgresMeteringRepository._actual_quantity(decoded, "fixed") == (1, "fixed")
    live = replace(decoded, pipeline="live", billable_seconds="1.2")
    assert PostgresMeteringRepository._actual_quantity(live, "live") == (2, "seconds")
    assert (
        PostgresMeteringRepository._actual_quantity(replace(live, billable_seconds="0"), "live")
        is None
    )
    lv2v = replace(decoded, pipeline="live-video-to-video", pixels=3887)
    assert PostgresMeteringRepository._actual_quantity(lv2v, "lv2v") == (
        3887,
        "720p-pixel-seconds",
    )
    assert PostgresMeteringRepository._actual_quantity(replace(lv2v, pixels=0), "lv2v") is None
    assert PostgresMeteringRepository._actual_quantity(decoded, "live") is None

    receipt = SimpleNamespace(
        session_id=decoded.auth_id,
        pm_session_id=decoded.pm_session_id,
        app=decoded.app,
        orchestrator_address=decoded.orchestrator_address,
        last_update_ns=decoded.current_time_ns,
        prior_update_ns=None,
        payment_type="fixed",
        quantity_unit="fixed",
        quantity=1,
        rate_numerator=1,
        rate_denominator=1,
        reserved_amount=1,
    )
    repository = PostgresMeteringRepository(
        cast(Any, None), consumer_group="group", topic="events", signer_id="signer"
    )
    row = cast(Any, receipt)
    assert repository._validate(row, replace(decoded, computed_fee=1)) == (None, 1)
    assert repository._validate(row, replace(decoded, app="other"))[0] is not None
    assert repository._validate(row, replace(decoded, computed_fee=0))[0] is not None


@pytest.mark.asyncio
async def test_postgres_repository_rejects_a_record_outside_its_topic_binding() -> None:
    repository = PostgresMeteringRepository(
        cast(Any, None), consumer_group="group", topic="configured", signer_id="signer"
    )
    with pytest.raises(ValueError, match="configured metering binding"):
        await repository.process(record(b"{}"), None, "a" * 64, "invalid_schema")


@pytest.mark.parametrize(
    "payload",
    [b'{"a":1,"a":2}', b'{"id":NaN}', b"[]", b"{" * 1000],
)
def test_decoder_normalizes_poison_errors(payload: bytes) -> None:
    with pytest.raises(ValueError, match="invalid_schema"):
        decode_go_livepeer(payload)


@pytest.mark.asyncio
async def test_service_quarantines_tombstone_size_schema_and_key() -> None:
    repository = Repository()
    service = MeteringService(repository, decode_go_livepeer, max_payload_bytes=1024)
    await service.process(record(None))
    await service.process(record(b"x" * 1025))
    await service.process(record(b"{}"))
    await service.process(record(event_payload(), key=b"wrong"))
    assert [call[3] for call in repository.calls] == [
        "invalid_schema",
        "payload_too_large",
        "invalid_schema",
        "key_mismatch",
    ]


@pytest.mark.asyncio
async def test_service_ignores_mixed_stream_and_enforces_query_roles() -> None:
    repository = Repository()
    service = MeteringService(repository, decode_go_livepeer)
    ignored = json.dumps(
        {"id": str(uuid4()), "type": "network_capabilities", "timestamp": "0", "data": []}
    ).encode()
    await service.process(record(ignored))
    assert repository.calls[-1][1] is None and repository.calls[-1][3] is None

    holder = PrincipalContext(
        PrincipalId("principal_holder0"),
        frozenset({Role.CREDENTIAL_HOLDER}),
        TenantId("tenant_12345678"),
        AccountId("account_12345678"),
    )
    await service.list_usage(holder, None, 50, None)
    with pytest.raises(PermissionError):
        await service.list_charges(holder, "account_other000", 50, None)
    with pytest.raises(PermissionError):
        await service.list_reconciliations(holder, 50, None)
    with pytest.raises(PermissionError):
        await service.health(holder)
    with pytest.raises(ValueError):
        await service.list_usage(holder, None, 0, None)
    with pytest.raises(ValueError):
        await service.list_reconciliations(
            PrincipalContext(PrincipalId("principal_operator0"), frozenset({Role.OPERATOR})),
            201,
            None,
        )
    unscoped = PrincipalContext(PrincipalId("principal_unscoped0"), frozenset())
    with pytest.raises(PermissionError):
        await service.list_open_reservations(unscoped, None, 50, None)
    operator = PrincipalContext(PrincipalId("principal_operator0"), frozenset({Role.OPERATOR}))
    tenant_admin = PrincipalContext(
        PrincipalId("principal_admin000"),
        frozenset({Role.TENANT_ADMIN}),
        TenantId("tenant_12345678"),
    )
    await service.list_charges(operator, None, 50, None)
    await service.list_reconciliations(tenant_admin, 50, None)
    await service.list_open_reservations(tenant_admin, None, 50, None)
    assert (await service.health(operator))["status"] == "ready"
    assert (await service.observe_health())["status"] == "ready"
    await service.heartbeat()
    assert await service.reconcile_pending() == 2


class Consumer:
    def __init__(self, messages: list[SimpleNamespace], *, beginning: int = 0) -> None:
        self.messages = messages
        self.commits: list[dict[object, int]] = []
        self.seeks: list[tuple[object, int]] = []
        self.started = False
        self.stopped = False
        self.listener: object | None = None
        self.beginning = beginning
        self.ending = max((message.offset + 1 for message in messages), default=beginning)
        self.assigned: set[TopicPartition] = set()
        self.last_poll_ms: int | None = None
        self.available_topics = {"events"}
        self.position_value = 0

    def subscribe(self, _topics: list[str], listener: object) -> None:
        self.listener = listener

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def __aiter__(self) -> Any:
        async def values() -> Any:
            for message in self.messages:
                yield message

        return values()

    async def commit(self, offsets: dict[object, int]) -> None:
        self.commits.append(offsets)

    async def beginning_offsets(self, partitions: list[object]) -> dict[object, int]:
        return {partition: self.beginning for partition in partitions}

    async def end_offsets(self, partitions: list[object]) -> dict[object, int]:
        return {partition: self.ending for partition in partitions}

    def seek(self, partition: object, offset: int) -> None:
        self.seeks.append((partition, offset))

    def highwater(self, _partition: object) -> int | None:
        return max((message.offset + 1 for message in self.messages), default=None)

    def assignment(self) -> set[TopicPartition]:
        return self.assigned

    def last_poll_timestamp(self, _partition: TopicPartition) -> int | None:
        return self.last_poll_ms

    async def topics(self) -> set[str]:
        return self.available_topics

    async def position(self, _partition: TopicPartition) -> int:
        return self.position_value


@pytest.mark.asyncio
async def test_consumer_poll_age_requires_assignment_and_a_completed_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = Consumer([])
    consumer = KafkaMeteringConsumer(
        MeteringService(Repository(), decode_go_livepeer),
        bootstrap_servers="unused",
        topic="events",
        group_id="group",
        client_id="client",
        consumer_factory=lambda *_args, **_kwargs: fake,
    )
    assert consumer.poll_age_seconds() is None
    fake.assigned = {TopicPartition("events", 0)}
    fake.messages = [SimpleNamespace(offset=4)]
    fake.position_value = 3
    assert consumer.poll_age_seconds() is None
    assert await consumer.readiness_poll_age() is None
    fake.last_poll_ms = 1_699_999_998_500
    monkeypatch.setattr("clearinghouse.adapters.kafka.time.time", lambda: 1_700_000_000.0)
    assert consumer.poll_age_seconds() == 1
    assert await consumer.readiness_poll_age() == 1
    monkeypatch.setattr("clearinghouse.adapters.kafka.time.time", lambda: 1_700_000_120.0)
    assert consumer.poll_age_seconds() == 121
    assert await consumer.readiness_poll_age() == 121
    fake.available_topics.clear()
    assert await consumer.readiness_poll_age() is None


@pytest.mark.asyncio
async def test_consumer_commits_only_authoritative_database_offset() -> None:
    repository = Repository()
    service = MeteringService(repository, decode_go_livepeer)
    payload = event_payload()
    event_id = json.loads(payload)["id"].encode()
    fake = Consumer(
        [SimpleNamespace(topic="events", partition=0, offset=0, key=event_id, value=payload)]
    )
    consumer = KafkaMeteringConsumer(
        service,
        bootstrap_servers="unused",
        topic="events",
        group_id="group",
        client_id="client",
        consumer_factory=lambda *_args, **_kwargs: fake,
    )
    await consumer.run()
    assert fake.started and fake.stopped
    assert list(fake.commits[0].values()) == [1]


@pytest.mark.asyncio
async def test_consumer_seeks_forward_when_database_is_ahead() -> None:
    repository = Repository()

    async def checkpoint(_topic: str, _partition: int) -> int:
        return 9

    cast(Any, repository).checkpoint = checkpoint
    service = MeteringService(repository, decode_go_livepeer)
    fake = Consumer([SimpleNamespace(topic="events", partition=0, offset=3, key=None, value=b"{}")])
    consumer = KafkaMeteringConsumer(
        service,
        bootstrap_servers="unused",
        topic="events",
        group_id="group",
        client_id="client",
        consumer_factory=lambda *_args, **_kwargs: fake,
    )
    await consumer.run()
    assert fake.seeks and not fake.commits and not repository.calls


@pytest.mark.asyncio
async def test_consumer_restores_broker_ahead_and_accepts_next_visible_offset() -> None:
    repository = Repository()
    repository.next_offset = 4

    async def checkpoint(_topic: str, _partition: int) -> int:
        return 3

    cast(Any, repository).checkpoint = checkpoint
    payload = event_payload()
    key = json.loads(payload)["id"].encode()
    messages = [
        SimpleNamespace(topic="events", partition=0, offset=9, key=key, value=payload),
        SimpleNamespace(topic="events", partition=0, offset=3, key=key, value=payload),
    ]
    fake = Consumer(messages)
    consumer = KafkaMeteringConsumer(
        MeteringService(repository, decode_go_livepeer),
        bootstrap_servers="unused",
        topic="events",
        group_id="group",
        client_id="client",
        consumer_factory=lambda *_args, **_kwargs: fake,
    )
    await consumer.run()
    assert fake.seeks and fake.commits
    assert repository.calls[-1][0].offset == 3


@pytest.mark.asyncio
async def test_consumer_fails_if_exact_offset_is_missing_after_seek() -> None:
    repository = Repository()

    async def checkpoint(_topic: str, _partition: int) -> int:
        return 3

    cast(Any, repository).checkpoint = checkpoint
    messages = [
        SimpleNamespace(topic="events", partition=0, offset=9, key=None, value=b"{}"),
        SimpleNamespace(topic="events", partition=0, offset=5, key=None, value=b"{}"),
    ]
    fake = Consumer(messages)
    consumer = KafkaMeteringConsumer(
        MeteringService(repository, decode_go_livepeer),
        bootstrap_servers="unused",
        topic="events",
        group_id="group",
        client_id="client",
        consumer_factory=lambda *_args, **_kwargs: fake,
    )
    with pytest.raises(RuntimeError, match="required offset"):
        await consumer.run()
    assert not repository.calls and not fake.commits


@pytest.mark.asyncio
async def test_consumer_clears_seek_state_on_rebalance() -> None:
    fake = Consumer([])
    consumer = KafkaMeteringConsumer(
        MeteringService(Repository(), decode_go_livepeer),
        bootstrap_servers="unused",
        topic="events",
        group_id="group",
        client_id="client",
        consumer_factory=lambda *_args, **_kwargs: fake,
    )
    consumer._seeking[("events", 0)] = 3
    listener = _MeteringRebalance(consumer)
    await listener.on_partitions_revoked([TopicPartition("events", 0)])
    assert not consumer._seeking


@pytest.mark.asyncio
async def test_assignment_seeks_from_database_before_waiting_for_messages() -> None:
    repository = Repository()

    async def checkpoint(_topic: str, _partition: int) -> int:
        return 0

    cast(Any, repository).checkpoint = checkpoint
    fake = Consumer([])
    fake.ending = 1  # Broker group may already be committed at highwater.
    consumer = KafkaMeteringConsumer(
        MeteringService(repository, decode_go_livepeer),
        bootstrap_servers="unused",
        topic="events",
        group_id="group",
        client_id="client",
        consumer_factory=lambda *_args, **_kwargs: fake,
    )
    partition = TopicPartition("events", 0)
    await _MeteringRebalance(consumer).on_partitions_assigned([partition])
    assert fake.seeks == [(partition, 0)]


@pytest.mark.asyncio
async def test_assignment_refuses_checkpoint_ahead_of_broker() -> None:
    repository = Repository()

    async def checkpoint(_topic: str, _partition: int) -> int:
        return 2

    cast(Any, repository).checkpoint = checkpoint
    fake = Consumer([])
    fake.ending = 1
    consumer = KafkaMeteringConsumer(
        MeteringService(repository, decode_go_livepeer),
        bootstrap_servers="unused",
        topic="events",
        group_id="group",
        client_id="client",
        consumer_factory=lambda *_args, **_kwargs: fake,
    )
    with pytest.raises(RuntimeError, match="ahead of the broker"):
        await _MeteringRebalance(consumer).on_partitions_assigned([TopicPartition("events", 0)])
