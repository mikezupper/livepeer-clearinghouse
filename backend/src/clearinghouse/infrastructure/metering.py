"""PostgreSQL metering settlement and tenant-scoped read model."""

# ruff: noqa: E501, S608 -- SQL predicates are selected from fixed internal scopes.

from __future__ import annotations

import hashlib
import json
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clearinghouse.domain.accounts import PrincipalContext, Role
from clearinghouse.domain.metering import (
    Charge,
    MeteringOutcome,
    OpenReservation,
    ProcessResult,
    QuarantineReason,
    ReconciliationCase,
    SignedTicketEvent,
    TransportRecord,
    UsageEvent,
)
from clearinghouse.infrastructure.telemetry import Component, Outcome, Reason, get_telemetry


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("\0".join(map(str, parts)).encode()).hexdigest()
    return f"{prefix}_{digest}"


def _business_digest(event: SignedTicketEvent) -> str:
    value = {
        key: item
        for key, item in asdict(event).items()
        if key
        not in {
            "transport_event_id",
            "gateway",
            "request_id",
            "manifest_id",
            "session_balance",
            "cost",
        }
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def encode_cursor(created_at: datetime, identity: str) -> str:
    raw = json.dumps([created_at.isoformat(), identity], separators=(",", ":")).encode()
    return urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    if not 1 <= len(value) <= 512:
        raise ValueError("invalid cursor")
    try:
        raw = urlsafe_b64decode(value + "=" * (-len(value) % 4))
        timestamp, identity = json.loads(raw)
        parsed = datetime.fromisoformat(timestamp)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid cursor") from error
    if parsed.tzinfo is None or not isinstance(identity, str) or not 1 <= len(identity) <= 256:
        raise ValueError("invalid cursor")
    return parsed, identity


class PostgresMeteringRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        consumer_group: str,
        topic: str,
        signer_id: str,
        reconciliation_batch_size: int = 100,
        confirmation_grace_seconds: int = 30,
        heartbeat_stale_seconds: int = 60,
    ) -> None:
        self.sessions = sessions
        self.consumer_group = consumer_group
        self.topic = topic
        self.signer_id = signer_id
        self.reconciliation_batch_size = reconciliation_batch_size
        self.confirmation_grace_seconds = confirmation_grace_seconds
        self.heartbeat_stale_seconds = heartbeat_stale_seconds

    @staticmethod
    async def _global_lock(session: AsyncSession) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('clearinghouse:exposure',0))")
        )

    async def process(
        self,
        record: TransportRecord,
        event: SignedTicketEvent | None,
        payload_sha256: str,
        decode_error: str | None,
    ) -> ProcessResult:
        if record.topic != self.topic:
            raise ValueError("record topic does not match configured metering binding")
        now = record.received_at
        async with self.sessions.begin() as session:
            existing = await self._begin_position(session, record, now)
            if existing is not None:
                return existing
            if event is None and decode_error is None:
                result = await self._record_observation(
                    session, record, None, payload_sha256, MeteringOutcome.IGNORED, None, None, now
                )
                await self._advance(session, record, now)
                return replace(result, next_offset=record.offset + 1)
            if decode_error is not None:
                reason = {
                    "payload_too_large": QuarantineReason.PAYLOAD_TOO_LARGE,
                    "key_mismatch": QuarantineReason.KEY_MISMATCH,
                }.get(decode_error, QuarantineReason.INVALID_SCHEMA)
                result = await self._quarantine(
                    session, record, event, payload_sha256, reason, None, now
                )
                await self._advance(session, record, now)
                return replace(result, next_offset=record.offset + 1)
            if event is None:
                raise RuntimeError("decoded event unexpectedly absent")
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"metering-event:{self.signer_id}:{event.transport_event_id}"},
            )
            canonical = (
                await session.execute(
                    text("""
                    SELECT payload_sha256,business_sha256,receipt_id FROM metering_canonical_events
                    WHERE producer_id=:producer AND transport_event_id=:event
                    """),
                    {"producer": self.signer_id, "event": event.transport_event_id},
                )
            ).one_or_none()
            if canonical is not None:
                if canonical.payload_sha256 == payload_sha256:
                    result = await self._record_observation(
                        session,
                        record,
                        event,
                        payload_sha256,
                        MeteringOutcome.DUPLICATE,
                        None,
                        canonical.receipt_id,
                        now,
                    )
                else:
                    result = await self._quarantine(
                        session,
                        record,
                        event,
                        payload_sha256,
                        QuarantineReason.FORKED_OBSERVATION,
                        canonical.receipt_id,
                        now,
                    )
                await self._advance(session, record, now)
                return replace(result, next_offset=record.offset + 1)

            preliminary = (
                await session.execute(
                    text("""
                    SELECT id,account_id FROM authorization_receipts
                    WHERE signer_id=:signer AND state_id=:state AND sequence_number=:sequence
                    """),
                    {
                        "signer": self.signer_id,
                        "state": event.state_id,
                        "sequence": event.sequence_number,
                    },
                )
            ).one_or_none()
            if preliminary is None:
                reason = await self._missing_reason(session, event)
                result = await self._quarantine(
                    session, record, event, payload_sha256, reason, None, now
                )
                await self._advance(session, record, now)
                return replace(result, next_offset=record.offset + 1)
            await self._global_lock(session)
            await session.execute(
                text("SELECT 1 FROM account_exposures WHERE account_id=:account FOR UPDATE"),
                {"account": preliminary.account_id},
            )
            receipt = (
                await session.execute(
                    text("""
                    SELECT r.*,l.capability,l.model,l.available,l.pending,l.settled,
                      l.expires_at lease_expires,s.status session_status,s.expires_at session_expires
                      ,(SELECT p.last_update_ns FROM authorization_receipts p
                        WHERE p.signer_id=r.signer_id AND p.state_id=r.state_id
                          AND p.sequence_number=r.sequence_number-1) prior_update_ns
                    FROM authorization_receipts r JOIN leases l ON l.id=r.lease_id
                    JOIN signer_sessions s ON s.id=r.session_id
                    WHERE r.id=:id FOR UPDATE OF r,l
                    """),
                    {"id": preliminary.id},
                )
            ).one()
            validation_reason, actual_quantity = self._validate(receipt, event)
            if validation_reason is not None:
                result = await self._quarantine(
                    session, record, event, payload_sha256, validation_reason, receipt.id, now
                )
                if validation_reason in {
                    QuarantineReason.FEE_MISMATCH,
                    QuarantineReason.LINEAGE_MISMATCH,
                } and receipt.status in {"pending", "unresolved"}:
                    await self._transition(
                        session,
                        receipt.id,
                        receipt.status,
                        "quarantined",
                        validation_reason.value,
                        result_observation_id(record, self.consumer_group),
                        now,
                    )
                await self._advance(session, record, now)
                return replace(result, next_offset=record.offset + 1)
            if receipt.status == "settled":
                accepted = await session.scalar(
                    text(
                        "SELECT business_sha256 FROM metering_canonical_events WHERE receipt_id=:id"
                    ),
                    {"id": receipt.id},
                )
                if accepted == _business_digest(event):
                    result = await self._record_observation(
                        session,
                        record,
                        event,
                        payload_sha256,
                        MeteringOutcome.DUPLICATE,
                        None,
                        receipt.id,
                        now,
                    )
                else:
                    result = await self._quarantine(
                        session,
                        record,
                        event,
                        payload_sha256,
                        QuarantineReason.FORKED_OBSERVATION,
                        receipt.id,
                        now,
                    )
                await self._advance(session, record, now)
                return replace(result, next_offset=record.offset + 1)
            if receipt.status == "quarantined":
                result = await self._quarantine(
                    session,
                    record,
                    event,
                    payload_sha256,
                    QuarantineReason.FORKED_OBSERVATION,
                    receipt.id,
                    now,
                )
                await self._advance(session, record, now)
                return replace(result, next_offset=record.offset + 1)
            result = await self._settle(
                session, record, event, payload_sha256, receipt, actual_quantity, now
            )
            await self._advance(session, record, now)
            return replace(result, next_offset=record.offset + 1)

    async def _begin_position(
        self, session: AsyncSession, record: TransportRecord, now: datetime
    ) -> ProcessResult | None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"metering:{self.consumer_group}:{record.topic}:{record.partition}"},
        )
        checkpoint = (
            await session.execute(
                text("""
                SELECT next_offset FROM metering_checkpoints WHERE consumer_group=:group
                  AND topic=:topic AND partition=:partition FOR UPDATE
                """),
                {
                    "group": self.consumer_group,
                    "topic": record.topic,
                    "partition": record.partition,
                },
            )
        ).one_or_none()
        existing = (
            await session.execute(
                text("""
                SELECT outcome,payload_sha256 FROM metering_observations WHERE consumer_group=:group
                  AND topic=:topic AND partition=:partition AND kafka_offset=:offset
                """),
                {
                    "group": self.consumer_group,
                    "topic": record.topic,
                    "partition": record.partition,
                    "offset": record.offset,
                },
            )
        ).one_or_none()
        if existing is not None:
            if checkpoint is None:
                raise RuntimeError("durable observation lacks checkpoint")
            payload = record.payload if record.payload is not None else b"\0kafka-tombstone"
            new_hash = hashlib.sha256(payload).hexdigest()
            if existing.payload_sha256 != new_hash:
                await self._record_position_conflict(
                    session, record, existing.payload_sha256, new_hash, now
                )
                return ProcessResult(
                    MeteringOutcome.QUARANTINED,
                    QuarantineReason.TRANSPORT_DIVERGENCE,
                    next_offset=int(checkpoint.next_offset),
                )
            return ProcessResult(MeteringOutcome.DUPLICATE, next_offset=int(checkpoint.next_offset))
        expected = int(checkpoint.next_offset) if checkpoint is not None else 0
        if record.beginning_offset > expected:
            await self._record_transport_gap(session, record, expected, now)
        return None

    async def _record_transport_gap(
        self,
        session: AsyncSession,
        record: TransportRecord,
        expected_offset: int,
        now: datetime,
    ) -> None:
        gap_id = _stable_id(
            "gap",
            self.consumer_group,
            record.topic,
            record.partition,
            expected_offset,
            record.beginning_offset,
        )
        case_id = _stable_id("recon", gap_id)
        await session.execute(
            text("""INSERT INTO metering_transport_gaps(id,consumer_group,topic,partition,
              expected_offset,observed_offset,reason,created_at)
              VALUES (:id,:group,:topic,:partition,:expected,:observed,'retention_gap',:now)
              ON CONFLICT DO NOTHING"""),
            {
                "id": gap_id,
                "group": self.consumer_group,
                "topic": record.topic,
                "partition": record.partition,
                "expected": expected_offset,
                "observed": record.beginning_offset,
                "now": now,
            },
        )
        await session.execute(
            text("""INSERT INTO reconciliation_cases(id,kind,status,reason,evidence,created_at)
              VALUES (:id,'transport_gap','open','transport_gap',
                jsonb_build_object('gap_id',CAST(:gap AS text)),:now) ON CONFLICT DO NOTHING"""),
            {"id": case_id, "gap": gap_id, "now": now},
        )
        await session.execute(
            text("""INSERT INTO metering_outbox(id,event_type,aggregate_id,payload,created_at)
              VALUES (:id,'metering.reconciliation.opened',:case,
                jsonb_build_object('schema_version','1.0','kind','transport_gap','case_id',
                  CAST(:case AS text)),:now)
              ON CONFLICT (event_type,aggregate_id) DO NOTHING"""),
            {"id": _stable_id("outbox", case_id), "case": case_id, "now": now},
        )
        get_telemetry().record_metering(Outcome.INVALID, Reason.GAP)

    async def _record_position_conflict(
        self,
        session: AsyncSession,
        record: TransportRecord,
        original_hash: str,
        conflicting_hash: str,
        now: datetime,
    ) -> None:
        conflict_id = _stable_id(
            "conflict",
            self.consumer_group,
            record.topic,
            record.partition,
            record.offset,
            conflicting_hash,
        )
        case_id = _stable_id("recon", conflict_id)
        await session.execute(
            text("""
            INSERT INTO metering_position_conflicts(id,consumer_group,topic,partition,kafka_offset,
              original_payload_sha256,conflicting_payload_sha256,received_at)
            VALUES (:id,:group,:topic,:partition,:offset,:original,:conflicting,:now)
            ON CONFLICT (consumer_group,topic,partition,kafka_offset,conflicting_payload_sha256)
            DO NOTHING
            """),
            {
                "id": conflict_id,
                "group": self.consumer_group,
                "topic": record.topic,
                "partition": record.partition,
                "offset": record.offset,
                "original": original_hash,
                "conflicting": conflicting_hash,
                "now": now,
            },
        )
        await session.execute(
            text("""
            INSERT INTO reconciliation_cases(id,kind,status,reason,evidence,created_at)
            VALUES (:id,'transport_divergence','open','transport_divergence',
              jsonb_build_object('conflict_id',CAST(:conflict AS text)),:now)
            ON CONFLICT DO NOTHING
            """),
            {"id": case_id, "conflict": conflict_id, "now": now},
        )
        await session.execute(
            text("""
            INSERT INTO metering_outbox(id,event_type,aggregate_id,payload,created_at)
            VALUES (:id,'metering.reconciliation.opened',:case,
              jsonb_build_object('schema_version','1.0','kind','transport_divergence',
                'case_id',CAST(:case AS text)),:now)
            ON CONFLICT (event_type,aggregate_id) DO NOTHING
            """),
            {"id": _stable_id("outbox", case_id), "case": case_id, "now": now},
        )

    async def _advance(self, session: AsyncSession, record: TransportRecord, now: datetime) -> None:
        updated = await session.scalar(
            text("""
            UPDATE metering_checkpoints SET next_offset=:next,updated_at=:now
            WHERE consumer_group=:group AND topic=:topic AND partition=:partition
            RETURNING next_offset
            """),
            {
                "group": self.consumer_group,
                "topic": record.topic,
                "partition": record.partition,
                "next": record.offset + 1,
                "now": now,
            },
        )
        if updated is None:
            await session.execute(
                text("""
                INSERT INTO metering_checkpoints(consumer_group,topic,partition,next_offset,updated_at)
                VALUES (:group,:topic,:partition,:next,:now)
                """),
                {
                    "group": self.consumer_group,
                    "topic": record.topic,
                    "partition": record.partition,
                    "next": record.offset + 1,
                    "now": now,
                },
            )

    async def _record_observation(
        self,
        session: AsyncSession,
        record: TransportRecord,
        event: SignedTicketEvent | None,
        payload_hash: str,
        outcome: MeteringOutcome,
        reason: QuarantineReason | None,
        receipt_id: str | None,
        now: datetime,
        computed_fee: int | None = None,
    ) -> ProcessResult:
        observation_id = result_observation_id(record, self.consumer_group)
        await session.execute(
            text("""
            INSERT INTO metering_observations(id,producer_id,transport_event_id,consumer_group,
              topic,partition,kafka_offset,broker_beginning_offset,payload_sha256,business_sha256,
              outcome,reason,receipt_id,computed_fee,
              received_at,processed_at)
            VALUES (:id,:producer,:event,:group,:topic,:partition,:offset,:beginning,:hash,:business,:outcome,:reason,
              :receipt,:fee,:received,:processed)
            """),
            {
                "id": observation_id,
                "producer": self.signer_id,
                "event": event.transport_event_id if event else None,
                "group": self.consumer_group,
                "topic": record.topic,
                "partition": record.partition,
                "offset": record.offset,
                "beginning": record.beginning_offset,
                "hash": payload_hash,
                "business": _business_digest(event) if event else None,
                "outcome": outcome.value,
                "reason": reason.value if reason else None,
                "receipt": receipt_id,
                "fee": computed_fee,
                "received": record.received_at,
                "processed": now,
            },
        )
        return ProcessResult(outcome, reason)

    async def _quarantine(
        self,
        session: AsyncSession,
        record: TransportRecord,
        event: SignedTicketEvent | None,
        payload_hash: str,
        reason: QuarantineReason,
        receipt_id: str | None,
        now: datetime,
    ) -> ProcessResult:
        result = await self._record_observation(
            session,
            record,
            event,
            payload_hash,
            MeteringOutcome.QUARANTINED,
            reason,
            receipt_id,
            now,
        )
        observation_id = result_observation_id(record, self.consumer_group)
        kind = (
            reason.value
            if reason.value
            in {
                "unknown_reservation",
                "fee_mismatch",
                "sequence_gap",
                "forked_observation",
                "lineage_mismatch",
                "invalid_schema",
                "key_mismatch",
                "payload_too_large",
            }
            else "invalid_schema"
        )
        case_id = _stable_id("recon", receipt_id or observation_id, kind)
        inserted_case = await session.scalar(
            text("""
            INSERT INTO reconciliation_cases(id,reservation_id,observation_id,tenant_id,account_id,
              kind,status,reason,evidence,created_at)
            SELECT :id,:receipt,:observation,r.tenant_id,r.account_id,:kind,'open',:reason,
              CAST(:evidence AS jsonb),:now FROM (SELECT 1) seed
            LEFT JOIN authorization_receipts r ON r.id=:receipt
            ON CONFLICT (reservation_id,kind)
              WHERE status='open' AND reservation_id IS NOT NULL DO NOTHING RETURNING id
            """),
            {
                "id": case_id,
                "receipt": receipt_id,
                "observation": observation_id,
                "kind": kind,
                "reason": reason.value,
                "evidence": '{"payload_sha256":"' + payload_hash + '"}',
                "now": now,
            },
        )
        if inserted_case is None and receipt_id is not None:
            case_id = str(
                await session.scalar(
                    text("""SELECT id FROM reconciliation_cases
                      WHERE reservation_id=:receipt AND kind=:kind AND status='open'"""),
                    {"receipt": receipt_id, "kind": kind},
                )
            )
        await session.execute(
            text("""
            INSERT INTO metering_outbox(id,event_type,aggregate_id,payload,created_at)
            VALUES (:id,'metering.quarantined',:observation,CAST(:payload AS jsonb),:now)
            """),
            {
                "id": _stable_id("outbox", observation_id),
                "observation": observation_id,
                "payload": '{"schema_version":"1.0","reason":"'
                + reason.value
                + '","raw_payload_sha256":"'
                + payload_hash
                + '"}',
                "now": now,
            },
        )
        await session.execute(
            text("""
            INSERT INTO metering_outbox(id,event_type,aggregate_id,payload,created_at)
            VALUES (:id,'metering.reconciliation.opened',:case,CAST(:payload AS jsonb),:now)
            ON CONFLICT (event_type,aggregate_id) DO NOTHING
            """),
            {
                "id": _stable_id("outbox", case_id),
                "case": case_id,
                "payload": '{"schema_version":"1.0","kind":"'
                + kind
                + '","case_id":"'
                + case_id
                + '"}',
                "now": now,
            },
        )
        return result

    async def _missing_reason(
        self, session: AsyncSession, event: SignedTicketEvent
    ) -> QuarantineReason:
        head = await session.scalar(
            text(
                "SELECT last_sequence FROM signer_state_heads WHERE signer_id=:signer AND state_id=:state"
            ),
            {"signer": self.signer_id, "state": event.state_id},
        )
        if head is not None and event.sequence_number <= int(head):
            return QuarantineReason.UNKNOWN_RESERVATION
        return QuarantineReason.SEQUENCE_GAP

    @staticmethod
    def _actual_quantity(event: SignedTicketEvent, payment_type: str) -> tuple[int, str] | None:
        if payment_type == "fixed" and event.pipeline == "fixed":
            return 1, "fixed"
        if payment_type == "live" and event.pipeline == "live":
            value = int(Decimal(event.billable_seconds).to_integral_value(rounding=ROUND_CEILING))
            return (value, "seconds") if value > 0 else None
        if payment_type == "lv2v" and event.pipeline == "live-video-to-video":
            return (event.pixels, "720p-pixel-seconds") if event.pixels > 0 else None
        return None

    def _validate(
        self, receipt: Row[tuple[object, ...]], event: SignedTicketEvent
    ) -> tuple[QuarantineReason | None, int]:
        expected_status = "new" if event.sequence_number == 0 else "continuing"
        actual = self._actual_quantity(event, receipt.payment_type)
        if (
            event.auth_id != receipt.session_id
            or event.pm_session_id != receipt.pm_session_id
            or event.app != receipt.app
            or event.orchestrator_address != receipt.orchestrator_address
            or event.current_time_ns != int(receipt.last_update_ns)
            or (event.sequence_number == 0 and event.previous_time_ns != event.current_time_ns)
            or (
                event.sequence_number > 0
                and (
                    receipt.prior_update_ns is None
                    or event.previous_time_ns != int(receipt.prior_update_ns)
                )
            )
            or event.session_status != expected_status
            or actual is None
            or actual[1] != receipt.quantity_unit
            or actual[0] > int(receipt.quantity)
        ):
            return QuarantineReason.LINEAGE_MISMATCH, 0
        quantity = actual[0]
        numerator = quantity * int(receipt.rate_numerator)
        expected_fee = (2 * numerator + int(receipt.rate_denominator)) // (
            2 * int(receipt.rate_denominator)
        )
        if event.computed_fee != expected_fee or event.computed_fee > int(receipt.reserved_amount):
            return QuarantineReason.FEE_MISMATCH, quantity
        return None, quantity

    async def _settle(
        self,
        session: AsyncSession,
        record: TransportRecord,
        event: SignedTicketEvent,
        payload_hash: str,
        receipt: Row[tuple[object, ...]],
        quantity: int,
        now: datetime,
    ) -> ProcessResult:
        observation_id = result_observation_id(record, self.consumer_group)
        usage_id = _stable_id("usage", self.signer_id, event.state_id, event.sequence_number)
        charge_id = _stable_id("charge", usage_id)
        transaction_id = _stable_id("ledger", charge_id)
        await self._record_observation(
            session,
            record,
            event,
            payload_hash,
            MeteringOutcome.SETTLED,
            None,
            receipt.id,
            now,
            event.computed_fee,
        )
        await session.execute(
            text("""
            INSERT INTO metering_canonical_events(producer_id,transport_event_id,observation_id,
              payload_sha256,business_sha256,receipt_id,created_at)
            VALUES (:producer,:event,:observation,:hash,:business,:receipt,:now)
            """),
            {
                "producer": self.signer_id,
                "event": event.transport_event_id,
                "observation": observation_id,
                "hash": payload_hash,
                "business": _business_digest(event),
                "receipt": receipt.id,
                "now": now,
            },
        )
        await session.execute(
            text("""
            INSERT INTO usage_events(id,reservation_id,observation_id,lease_id,tenant_id,account_id,
              principal_id,capability,model,quantity,quantity_unit,rate_card_id,rate_numerator,
              rate_denominator,charge_unit,producer_id,transport_event_id,state_hash,sequence_number,
              ticket_count,manifest_id,occurred_at,occurred_at_text,occurred_at_ns,created_at)
            VALUES (:id,:receipt,:observation,:lease,:tenant,:account,:principal,:capability,:model,
              :quantity,:quantity_unit,:rate,:numerator,:denominator,:charge_unit,:producer,:event,
              :state_hash,:sequence,:tickets,:manifest,:occurred,:occurred_text,:occurred_ns,:now)
            """),
            {
                "id": usage_id,
                "receipt": receipt.id,
                "observation": observation_id,
                "lease": receipt.lease_id,
                "tenant": receipt.tenant_id,
                "account": receipt.account_id,
                "principal": receipt.principal_id,
                "capability": receipt.capability,
                "model": receipt.model,
                "quantity": quantity,
                "quantity_unit": receipt.quantity_unit,
                "rate": receipt.rate_card_id,
                "numerator": receipt.rate_numerator,
                "denominator": receipt.rate_denominator,
                "charge_unit": receipt.charge_unit,
                "producer": self.signer_id,
                "event": event.transport_event_id,
                "state_hash": receipt.state_hash,
                "sequence": receipt.sequence_number,
                "tickets": event.ticket_count,
                "manifest": event.manifest_id,
                "occurred": event.occurred_at,
                "occurred_text": event.occurred_at_text,
                "occurred_ns": event.current_time_ns,
                "now": now,
            },
        )
        await session.execute(
            text("""INSERT INTO ledger_transactions(id,tenant_id,kind,source_id,actor_id,created_at)
              VALUES (:id,:tenant,'charge',:source,'principal_system_metering',:now)"""),
            {"id": transaction_id, "tenant": receipt.tenant_id, "source": charge_id, "now": now},
        )
        await session.execute(
            text("""
            INSERT INTO charges(id,usage_event_id,reservation_id,lease_id,tenant_id,account_id,
              amount,unit,ledger_transaction_id,rate_card_id,rate_numerator,rate_denominator,
              quantity_unit,created_at) VALUES (:id,:usage,:receipt,:lease,:tenant,:account,:amount,
              :unit,:transaction,:rate,:numerator,:denominator,:quantity_unit,:now)
            """),
            {
                "id": charge_id,
                "usage": usage_id,
                "receipt": receipt.id,
                "lease": receipt.lease_id,
                "tenant": receipt.tenant_id,
                "account": receipt.account_id,
                "amount": event.computed_fee,
                "unit": receipt.charge_unit,
                "transaction": transaction_id,
                "rate": receipt.rate_card_id,
                "numerator": receipt.rate_numerator,
                "denominator": receipt.rate_denominator,
                "quantity_unit": receipt.quantity_unit,
                "now": now,
            },
        )
        await session.execute(
            text("""
            INSERT INTO ledger_postings(id,transaction_id,tenant_id,account_code,amount,unit,created_at)
            VALUES (:payer,:tx,:tenant,:account,:debit,:unit,:now),
                   (:system,:tx,:tenant,'system:usage',:credit,:unit,:now)
            """),
            {
                "payer": _id("post"),
                "system": _id("post"),
                "tx": transaction_id,
                "tenant": receipt.tenant_id,
                "account": f"payer:{receipt.account_id}",
                "debit": -event.computed_fee,
                "credit": event.computed_fee,
                "unit": receipt.charge_unit,
                "now": now,
            },
        )
        await self._transition(
            session,
            receipt.id,
            receipt.status,
            "settled",
            "kafka_confirmation",
            observation_id,
            now,
        )
        active = (
            receipt.session_status == "active"
            and receipt.session_expires > now
            and receipt.lease_expires > now
        )
        surplus = int(receipt.reserved_amount) - event.computed_fee if active else 0
        await session.execute(
            text("""UPDATE leases SET available=available+:surplus,pending=pending-:reserved,
              settled=settled+:fee WHERE id=:lease"""),
            {
                "surplus": surplus,
                "reserved": receipt.reserved_amount,
                "fee": event.computed_fee,
                "lease": receipt.lease_id,
            },
        )
        released_open = event.computed_fee if active else int(receipt.reserved_amount)
        await session.execute(
            text(
                "UPDATE account_exposures SET open_lease_exposure=open_lease_exposure-:reserved WHERE account_id=:account"
            ),
            {"reserved": released_open, "account": receipt.account_id},
        )
        await session.execute(
            text(
                "UPDATE global_exposure SET open_exposure=open_exposure-:reserved WHERE singleton"
            ),
            {"reserved": released_open},
        )
        return ProcessResult(MeteringOutcome.SETTLED, usage_event_id=usage_id, charge_id=charge_id)

    async def _transition(
        self,
        session: AsyncSession,
        receipt_id: str,
        old: str,
        new: str,
        reason: str,
        observation_id: str | None,
        now: datetime,
    ) -> None:
        await session.execute(
            text("""
            INSERT INTO receipt_transition_events(id,receipt_id,from_status,to_status,reason,
              observation_id,occurred_at,created_at) VALUES (:id,:receipt,:old,:new,:reason,
              :observation,:now,:now)
            """),
            {
                "id": _stable_id("transition", receipt_id, new),
                "receipt": receipt_id,
                "old": old,
                "new": new,
                "reason": reason,
                "observation": observation_id,
                "now": now,
            },
        )
        updated = await session.scalar(
            text("""UPDATE authorization_receipts SET status=:new WHERE id=:id AND status=:old
              RETURNING id"""),
            {"new": new, "id": receipt_id, "old": old},
        )
        if updated is None:
            raise RuntimeError("receipt status changed concurrently")
        if old == "unresolved" and new in {"settled", "quarantined"}:
            await session.execute(
                text("""UPDATE reconciliation_cases SET status='resolved',resolved_at=:now
                  WHERE reservation_id=:receipt AND kind='missing_confirmation' AND status='open'"""),
                {"receipt": receipt_id, "now": now},
            )

    async def reconcile_pending(self, now: datetime) -> int:
        async with self.sessions.begin() as session:
            await self._global_lock(session)
            expired_leases = (
                await session.execute(
                    text("""SELECT id,account_id,available FROM leases
                      WHERE expires_at<=:now AND available>0 ORDER BY account_id,id
                      LIMIT :limit FOR UPDATE SKIP LOCKED"""),
                    {"now": now, "limit": self.reconciliation_batch_size},
                )
            ).all()
            for lease in expired_leases:
                await session.execute(
                    text("UPDATE leases SET available=0 WHERE id=:lease"), {"lease": lease.id}
                )
                await session.execute(
                    text("""UPDATE account_exposures
                      SET open_lease_exposure=open_lease_exposure-:released
                      WHERE account_id=:account"""),
                    {"released": lease.available, "account": lease.account_id},
                )
                await session.execute(
                    text("""UPDATE global_exposure SET open_exposure=open_exposure-:released
                      WHERE singleton"""),
                    {"released": lease.available},
                )
            rows = (
                await session.execute(
                    text("""
                    SELECT r.id,r.status,r.tenant_id,r.account_id,r.lease_id,l.available,
                      l.expires_at FROM authorization_receipts r
                    JOIN leases l ON l.id=r.lease_id WHERE r.status='pending'
                      AND (r.signer_confirmed_at<=:confirmed_before OR l.expires_at<=:now)
                    ORDER BY COALESCE(r.signer_confirmed_at,l.expires_at),r.id
                    FOR UPDATE OF r,l SKIP LOCKED
                    LIMIT :limit
                    """),
                    {
                        "now": now,
                        "confirmed_before": now
                        - timedelta(seconds=self.confirmation_grace_seconds),
                        "limit": self.reconciliation_batch_size,
                    },
                )
            ).all()
            for row in rows:
                await self._transition(
                    session, row.id, "pending", "unresolved", "missing_confirmation", None, now
                )
                await session.execute(
                    text("""
                    INSERT INTO reconciliation_cases(id,reservation_id,tenant_id,account_id,kind,
                      status,reason,evidence,created_at) VALUES (:id,:receipt,:tenant,:account,
                      'missing_confirmation','open','missing_confirmation','{}'::jsonb,:now)
                    ON CONFLICT (reservation_id,kind)
                      WHERE status='open' AND reservation_id IS NOT NULL DO NOTHING
                    """),
                    {
                        "id": _stable_id("recon", row.id, "missing_confirmation"),
                        "receipt": row.id,
                        "tenant": row.tenant_id,
                        "account": row.account_id,
                        "now": now,
                    },
                )
                case_id = _stable_id("recon", row.id, "missing_confirmation")
                await session.execute(
                    text("""
                    INSERT INTO metering_outbox(id,event_type,aggregate_id,payload,created_at)
                    VALUES (:id,'metering.reconciliation.opened',:case,CAST(:payload AS jsonb),:now)
                    ON CONFLICT (event_type,aggregate_id) DO NOTHING
                    """),
                    {
                        "id": _stable_id("outbox", case_id),
                        "case": case_id,
                        "payload": '{"schema_version":"1.0","kind":"missing_confirmation",'
                        '"case_id":"' + case_id + '"}',
                        "now": now,
                    },
                )
            return len(rows)

    async def list_usage(
        self, actor: PrincipalContext, account_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[UsageEvent]:
        where, params = self._scope(actor, account_id)
        if cursor:
            cursor_time, cursor_id = _decode_cursor(cursor)
            where += " AND (u.created_at,u.id)<(:cursor_time,:cursor_id)"
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
        params["limit"] = limit + 1
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT u.* FROM usage_events u WHERE {where} "
                        "ORDER BY u.created_at DESC,u.id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).all()
        return [
            UsageEvent(
                row.id,
                row.reservation_id,
                row.lease_id,
                row.tenant_id,
                row.account_id,
                row.principal_id,
                row.capability,
                row.model,
                int(row.quantity),
                row.quantity_unit,
                row.rate_card_id,
                int(row.rate_numerator),
                int(row.rate_denominator),
                row.charge_unit,
                row.manifest_id,
                row.producer_id,
                str(row.transport_event_id),
                int(row.sequence_number),
                row.ticket_count,
                row.occurred_at,
                row.occurred_at_text,
                int(row.occurred_at_ns),
                row.created_at,
            )
            for row in rows
        ]

    async def list_charges(
        self, actor: PrincipalContext, account_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[Charge]:
        where, params = self._scope(actor, account_id, alias="c")
        if cursor:
            cursor_time, cursor_id = _decode_cursor(cursor)
            where += " AND (c.created_at,c.id)<(:cursor_time,:cursor_id)"
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
        params["limit"] = limit + 1
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT c.* FROM charges c WHERE {where} "
                        "ORDER BY c.created_at DESC,c.id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).all()
        return [
            Charge(
                row.id,
                row.usage_event_id,
                row.reservation_id,
                row.lease_id,
                row.tenant_id,
                row.account_id,
                int(row.amount),
                row.unit,
                row.rate_card_id,
                int(row.rate_numerator),
                int(row.rate_denominator),
                row.quantity_unit,
                row.created_at,
            )
            for row in rows
        ]

    async def list_reconciliations(
        self, actor: PrincipalContext, limit: int, cursor: str | None
    ) -> Sequence[ReconciliationCase]:
        params: dict[str, object] = {"limit": limit + 1}
        where = "TRUE"
        if not actor.is_operator:
            where = "tenant_id=:tenant"
            params["tenant"] = str(actor.tenant_id)
        if cursor:
            cursor_time, cursor_id = _decode_cursor(cursor)
            where += " AND (created_at,id)<(:cursor_time,:cursor_id)"
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM reconciliation_cases WHERE {where} "
                        "ORDER BY created_at DESC,id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).all()
        return [
            ReconciliationCase(
                row.id,
                row.reservation_id,
                row.tenant_id,
                row.account_id,
                row.kind,
                row.status,
                row.reason,
                row.created_at,
                row.resolved_at,
            )
            for row in rows
        ]

    async def list_open_reservations(
        self, actor: PrincipalContext, account_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[OpenReservation]:
        where, params = self._scope(actor, account_id, alias="r")
        where += " AND r.status IN ('pending','unresolved','quarantined')"
        if cursor:
            cursor_time, cursor_id = _decode_cursor(cursor)
            where += " AND (r.created_at,r.id)<(:cursor_time,:cursor_id)"
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
        params["limit"] = limit + 1
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(f"""SELECT r.id,r.lease_id,r.tenant_id,r.account_id,r.status,
                      r.reserved_amount,r.charge_unit,r.sequence_number,r.signer_confirmed_at,
                      r.created_at FROM authorization_receipts r WHERE {where}
                      ORDER BY r.created_at DESC,r.id DESC LIMIT :limit"""),
                    params,
                )
            ).all()
        return [
            OpenReservation(
                row.id,
                row.lease_id,
                row.tenant_id,
                row.account_id,
                row.status,
                int(row.reserved_amount),
                row.charge_unit,
                int(row.sequence_number),
                row.signer_confirmed_at,
                row.created_at,
            )
            for row in rows
        ]

    async def health(self) -> dict[str, object]:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    text("""
              SELECT (SELECT count(*) FROM reconciliation_cases WHERE status='open') open_cases,
                (SELECT count(*) FROM metering_observations WHERE outcome='quarantined') quarantined,
                (SELECT count(*) FROM authorization_receipts WHERE status='pending') pending,
                (SELECT count(*) FROM authorization_receipts WHERE status='unresolved') unresolved,
                (SELECT coalesce(sum(reserved_amount),0) FROM authorization_receipts
                  WHERE status='pending') pending_amount,
                (SELECT coalesce(sum(reserved_amount),0) FROM authorization_receipts
                  WHERE status='unresolved') unresolved_amount,
                (SELECT coalesce(extract(epoch FROM (now()-min(created_at))),0)::bigint
                  FROM authorization_receipts WHERE status='pending') pending_oldest_seconds,
                (SELECT coalesce(extract(epoch FROM (now()-min(created_at))),0)::bigint
                  FROM authorization_receipts WHERE status='unresolved') unresolved_oldest_seconds,
                (SELECT exposure_cap FROM global_exposure WHERE singleton) exposure_cap,
                (SELECT open_exposure FROM global_exposure WHERE singleton) open_exposure,
                (SELECT max(updated_at) FROM metering_checkpoints
                  WHERE consumer_group=:group AND topic=:topic) last_checkpoint_at,
                (SELECT last_seen_at FROM metering_worker_heartbeats
                  WHERE consumer_group=:group AND topic=:topic AND signer_id=:signer) last_heartbeat_at,
                now() observed_at
            """),
                    {"group": self.consumer_group, "topic": self.topic, "signer": self.signer_id},
                )
            ).one()
        ready = (
            row.last_heartbeat_at is not None
            and (row.observed_at - row.last_heartbeat_at).total_seconds()
            <= self.heartbeat_stale_seconds
        )
        cap = int(row.exposure_cap)

        def utilization(amount: int) -> int:
            if cap <= 0:
                return 0 if amount == 0 else 10_000
            return min(10_000, amount * 10_000 // cap)

        telemetry = get_telemetry()
        telemetry.record_exposure_snapshot(
            open_utilization_bps=utilization(int(row.open_exposure)),
            pending_utilization_bps=utilization(int(row.pending_amount)),
            unresolved_utilization_bps=utilization(int(row.unresolved_amount)),
            pending_count=int(row.pending),
            unresolved_count=int(row.unresolved),
        )
        telemetry.record_settlement_age(
            pending_seconds=max(0, int(row.pending_oldest_seconds)),
            unresolved_seconds=max(0, int(row.unresolved_oldest_seconds)),
        )
        heartbeat_age = (
            int((row.observed_at - row.last_heartbeat_at).total_seconds())
            if row.last_heartbeat_at is not None
            else self.heartbeat_stale_seconds + 1
        )
        telemetry.record_consumer_heartbeat(max(0, heartbeat_age))
        telemetry.record_adapter_health(Component.DATABASE, "postgresql", healthy=True)
        telemetry.record_adapter_health(Component.METERING, "redpanda", healthy=ready)
        return {
            "status": "ready" if ready else "degraded",
            "open_cases": row.open_cases,
            "quarantined": row.quarantined,
            "unresolved": row.unresolved,
            "global_exposure_cap": str(row.exposure_cap),
            "global_open_exposure": str(row.open_exposure),
            "last_checkpoint_at": row.last_checkpoint_at.isoformat()
            if row.last_checkpoint_at
            else None,
            "last_heartbeat_at": row.last_heartbeat_at.isoformat()
            if row.last_heartbeat_at
            else None,
        }

    async def heartbeat(self, now: datetime) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                text("""INSERT INTO metering_worker_heartbeats(consumer_group,topic,signer_id,
                  started_at,last_seen_at) VALUES (:group,:topic,:signer,:now,:now)
                  ON CONFLICT (consumer_group,topic,signer_id) DO UPDATE
                    SET last_seen_at=EXCLUDED.last_seen_at"""),
                {
                    "group": self.consumer_group,
                    "topic": self.topic,
                    "signer": self.signer_id,
                    "now": now,
                },
            )

    async def checkpoint(self, topic: str, partition: int) -> int | None:
        if topic != self.topic or partition < 0:
            raise ValueError("invalid metering partition")
        async with self.sessions() as session:
            value = await session.scalar(
                text("""SELECT next_offset FROM metering_checkpoints
                  WHERE consumer_group=:group AND topic=:topic AND partition=:partition"""),
                {"group": self.consumer_group, "topic": topic, "partition": partition},
            )
        return int(value) if value is not None else None

    @staticmethod
    def _scope(
        actor: PrincipalContext, account_id: str | None, *, alias: str = "u"
    ) -> tuple[str, dict[str, object]]:
        params: dict[str, object] = {}
        if actor.is_operator:
            if account_id:
                params["account"] = account_id
                return f"{alias}.account_id=:account", params
            return "TRUE", params
        if Role.TENANT_ADMIN in actor.roles and actor.tenant_id is not None:
            params["tenant"] = str(actor.tenant_id)
            clause = f"{alias}.tenant_id=:tenant"
            if account_id:
                params["account"] = account_id
                clause += f" AND {alias}.account_id=:account"
            return clause, params
        params["account"] = str(actor.account_id)
        return f"{alias}.account_id=:account", params


def result_observation_id(record: TransportRecord, consumer_group: str) -> str:
    return _stable_id("observation", consumer_group, record.topic, record.partition, record.offset)
