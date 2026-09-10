"""Metering normalization, settlement, and query workflows."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from clearinghouse.domain.accounts import PrincipalContext, Role
from clearinghouse.domain.metering import (
    Charge,
    OpenReservation,
    ProcessResult,
    ReconciliationCase,
    SignedTicketEvent,
    TransportRecord,
    UsageEvent,
)


class EventDecoder(Protocol):
    def __call__(self, payload: bytes) -> SignedTicketEvent | None: ...


class MeteringRepository(Protocol):
    async def process(
        self,
        record: TransportRecord,
        event: SignedTicketEvent | None,
        payload_sha256: str,
        decode_error: str | None,
    ) -> ProcessResult: ...

    async def list_usage(
        self, actor: PrincipalContext, account_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[UsageEvent]: ...

    async def list_charges(
        self, actor: PrincipalContext, account_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[Charge]: ...

    async def list_reconciliations(
        self, actor: PrincipalContext, limit: int, cursor: str | None
    ) -> Sequence[ReconciliationCase]: ...

    async def list_open_reservations(
        self, actor: PrincipalContext, account_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[OpenReservation]: ...

    async def health(self) -> dict[str, object]: ...

    async def checkpoint(self, topic: str, partition: int) -> int | None: ...

    async def heartbeat(self, now: datetime) -> None: ...

    async def reconcile_pending(self, now: datetime) -> int: ...


class MeteringService:
    def __init__(
        self,
        repository: MeteringRepository,
        decoder: EventDecoder,
        *,
        clock: Callable[[], datetime] | None = None,
        max_payload_bytes: int = 1_048_576,
    ) -> None:
        self.repository = repository
        self.decoder = decoder
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_payload_bytes = max_payload_bytes

    async def process(self, record: TransportRecord) -> ProcessResult:
        payload = record.payload
        if payload is None:
            return await self.repository.process(
                record, None, hashlib.sha256(b"\0kafka-tombstone").hexdigest(), "invalid_schema"
            )
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        if len(payload) > self.max_payload_bytes:
            return await self.repository.process(record, None, payload_sha256, "payload_too_large")
        try:
            event = self.decoder(payload)
        except ValueError:
            return await self.repository.process(record, None, payload_sha256, "invalid_schema")
        if event is not None:
            try:
                key = record.key.decode("ascii") if record.key is not None else ""
            except UnicodeDecodeError:
                key = ""
            if key != event.transport_event_id:
                return await self.repository.process(record, event, payload_sha256, "key_mismatch")
        return await self.repository.process(record, event, payload_sha256, None)

    async def list_usage(
        self, actor: PrincipalContext, account_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[UsageEvent]:
        self._validate_query(actor, account_id, limit)
        return await self.repository.list_usage(actor, account_id, limit, cursor)

    async def list_charges(
        self, actor: PrincipalContext, account_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[Charge]:
        self._validate_query(actor, account_id, limit)
        return await self.repository.list_charges(actor, account_id, limit, cursor)

    async def list_reconciliations(
        self, actor: PrincipalContext, limit: int, cursor: str | None
    ) -> Sequence[ReconciliationCase]:
        if not actor.is_operator and Role.TENANT_ADMIN not in actor.roles:
            raise PermissionError("operator or tenant administrator role required")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        return await self.repository.list_reconciliations(actor, limit, cursor)

    async def list_open_reservations(
        self, actor: PrincipalContext, account_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[OpenReservation]:
        self._validate_query(actor, account_id, limit)
        return await self.repository.list_open_reservations(actor, account_id, limit, cursor)

    async def health(self, actor: PrincipalContext) -> dict[str, object]:
        if not actor.is_operator:
            raise PermissionError("operator role required")
        return await self.repository.health()

    async def observe_health(self) -> dict[str, object]:
        """Refresh aggregate process telemetry without crossing the HTTP auth boundary."""
        return await self.repository.health()

    async def checkpoint(self, topic: str, partition: int) -> int | None:
        return await self.repository.checkpoint(topic, partition)

    async def heartbeat(self) -> None:
        await self.repository.heartbeat(self.clock())

    async def reconcile_pending(self) -> int:
        return await self.repository.reconcile_pending(self.clock())

    @staticmethod
    def _validate_query(actor: PrincipalContext, account_id: str | None, limit: int) -> None:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if actor.is_operator:
            return
        if actor.tenant_id is None:
            raise PermissionError("tenant scope required")
        if Role.TENANT_ADMIN in actor.roles:
            return
        if actor.account_id is None or account_id not in {None, str(actor.account_id)}:
            raise PermissionError("account scope denied")
