"""PostgreSQL implementation of signer admission and exposure serialization."""

# ruff: noqa: E501, S608 -- SQL predicates are selected only from internal scope constants.

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clearinghouse.application.signer import SignerConflict
from clearinghouse.domain.accounts import PrincipalContext, Role
from clearinghouse.domain.signer import (
    Admission,
    DenialReason,
    Lease,
    PaymentState,
    SignerSession,
    ceil_ratio,
    epoch_nanoseconds,
    reservation_quantity,
    state_digest,
)


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _receipt_id(signer_id: str, state_id: str, sequence: int) -> str:
    digest = hashlib.sha256(f"{signer_id}\0{state_id}\0{sequence}".encode()).hexdigest()
    return f"receipt_{digest}"


def _transition_id(receipt_id: str, status: str) -> str:
    return f"transition_{hashlib.sha256(f'{receipt_id}\0{status}'.encode()).hexdigest()}"


class PostgresSignerRepository:
    """Lock global → account → lease → signer state for every admission write."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        global_cap: int,
        signer_id: str,
        signer_url: str,
        discovery_url: str,
    ) -> None:
        self.sessions = sessions
        self.global_cap = global_cap
        self.signer_id = signer_id
        self.signer_url = signer_url
        self.discovery_url = discovery_url

    @staticmethod
    def _lease(row: Row[tuple[object, ...]]) -> Lease:
        return Lease(
            row.id,
            int(row.cap),
            int(row.available),
            int(row.pending),
            int(row.settled),
            row.unit,
            row.expires_at,
        )

    @staticmethod
    async def _global_lock(session: AsyncSession) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('clearinghouse:exposure',0))")
        )

    @staticmethod
    async def _retire_expired_for_account(
        session: AsyncSession, account_id: str, now: datetime
    ) -> None:
        rows = (
            await session.execute(
                text("""
                SELECT s.id,l.id lease_id,l.available FROM signer_sessions s
                JOIN leases l ON l.session_id=s.id
                WHERE s.account_id=:account AND s.status='active'
                  AND (s.expires_at<=:now OR l.expires_at<=:now)
                ORDER BY s.id FOR UPDATE OF s,l
                """),
                {"account": account_id, "now": now},
            )
        ).all()
        released = sum(int(row.available) for row in rows)
        for row in rows:
            await session.execute(
                text("UPDATE signer_sessions SET status='expired' WHERE id=:id"),
                {"id": row.id},
            )
            await session.execute(
                text("UPDATE leases SET available=0 WHERE id=:id"), {"id": row.lease_id}
            )
        if released:
            await session.execute(
                text(
                    "UPDATE account_exposures SET open_lease_exposure="
                    "open_lease_exposure-:released WHERE account_id=:account"
                ),
                {"released": released, "account": account_id},
            )
            await session.execute(
                text(
                    "UPDATE global_exposure SET open_exposure=open_exposure-:released "
                    "WHERE singleton"
                ),
                {"released": released},
            )

    async def _retire_all_expired(self, session: AsyncSession, now: datetime) -> None:
        accounts = (
            await session.execute(
                text(
                    "SELECT DISTINCT account_id FROM signer_sessions "
                    "WHERE status='active' AND expires_at<=:now ORDER BY account_id"
                ),
                {"now": now},
            )
        ).scalars()
        for account_id in accounts:
            await session.execute(
                text("SELECT 1 FROM account_exposures WHERE account_id=:account FOR UPDATE"),
                {"account": account_id},
            )
            await self._retire_expired_for_account(session, account_id, now)

    async def initialize(self) -> None:
        async with self.sessions.begin() as session:
            await self._global_lock(session)
            row = (
                await session.execute(
                    text("SELECT * FROM global_exposure WHERE singleton FOR UPDATE")
                )
            ).one()
            if row.configured and int(row.exposure_cap) != self.global_cap:
                raise RuntimeError("configured global exposure cap conflicts with database")
            if not row.configured:
                configured = await session.execute(
                    text(
                        "UPDATE global_exposure SET exposure_cap=:cap,configured=true "
                        "WHERE singleton AND open_exposure<=:cap RETURNING singleton"
                    ),
                    {"cap": self.global_cap},
                )
                if configured.scalar_one_or_none() is None:
                    raise RuntimeError("global exposure cap is below existing exposure")

    async def create_session(
        self,
        *,
        principal: PrincipalContext,
        credential_id: str | None,
        capability: str,
        model: str | None,
        app: str,
        operation_key: str,
        request_hash: str,
        requested_cap: int,
        unit: str,
        ttl_seconds: int,
        token_hash: bytes,
        now: datetime,
    ) -> SignerSession:
        async with self.sessions.begin() as session:
            await self._global_lock(session)
            global_row = (
                await session.execute(
                    text("SELECT * FROM global_exposure WHERE singleton FOR UPDATE")
                )
            ).one()
            if not global_row.configured or global_row.kill_switch:
                raise ValueError("signer authorization is disabled")
            await self._retire_all_expired(session, now)
            previous = (
                await session.execute(
                    text(
                        "SELECT id,request_hash FROM signer_sessions "
                        "WHERE principal_id=:principal AND operation_key=:key"
                    ),
                    {"principal": str(principal.id), "key": operation_key},
                )
            ).one_or_none()
            if previous is not None:
                if previous.request_hash != request_hash:
                    raise SignerConflict("Idempotency-Key reused with a different request")
                raise SignerConflict(f"operation already created signer session {previous.id}")
            exposure = (
                await session.execute(
                    text("SELECT * FROM account_exposures WHERE account_id=:account FOR UPDATE"),
                    {"account": str(principal.account_id)},
                )
            ).one_or_none()
            if exposure is not None:
                await self._retire_expired_for_account(session, str(principal.account_id), now)
                exposure = (
                    await session.execute(
                        text("SELECT * FROM account_exposures WHERE account_id=:account"),
                        {"account": str(principal.account_id)},
                    )
                ).one()
            credential = None
            if credential_id is not None:
                credential = (
                    await session.execute(
                        text("SELECT * FROM credentials WHERE id=:id FOR UPDATE"),
                        {"id": credential_id},
                    )
                ).one_or_none()
            row = (
                await session.execute(
                    text("""
                SELECT a.*,t.status tenant_status,p.status principal_status,r.id rate_card_id,
                       r.charge_unit,r.quantity_unit,r.numerator,r.denominator,
                       c.status credential_status,c.expires_at credential_expires,
                       c.principal_id credential_principal,c.account_id credential_account
                FROM accounts a JOIN tenants t ON t.id=a.tenant_id
                JOIN principals p ON p.id=:principal
                LEFT JOIN credentials c ON c.id=:credential
                JOIN account_capability_policies policy ON policy.account_id=a.id
                  AND policy.capability=:capability AND policy.model=COALESCE(:model,'')
                  AND policy.allowed
                JOIN LATERAL (SELECT * FROM rate_cards r WHERE r.capability=:capability
                  AND r.model IS NOT DISTINCT FROM :model AND r.effective_at<=:now
                  ORDER BY r.version DESC LIMIT 1) r ON true
                WHERE a.id=:account AND EXISTS (SELECT 1 FROM principal_roles pr
                  WHERE pr.principal_id=p.id AND pr.role='credential_holder')
                FOR UPDATE OF a,t,p,policy
            """),
                    {
                        "principal": str(principal.id),
                        "credential": credential_id,
                        "account": str(principal.account_id),
                        "capability": capability,
                        "model": model,
                        "now": now,
                    },
                )
            ).one_or_none()
            if row is None or exposure is None:
                raise ValueError("capability denied")
            if (
                row.status != "active"
                or row.tenant_status != "active"
                or row.principal_status != "active"
            ):
                raise ValueError("identity unavailable")
            if credential_id is not None and (
                credential is None
                or credential.status != "active"
                or credential.principal_id != str(principal.id)
                or credential.account_id != str(principal.account_id)
                or (credential.expires_at is not None and credential.expires_at <= now)
            ):
                raise ValueError("source credential is unavailable")
            if unit != "wei" or row.unit != "wei" or row.charge_unit != "wei":
                raise ValueError("signer sessions require wei rates and accounts")
            if int(row.numerator) <= 0 or int(row.denominator) <= 0:
                raise ValueError("signer sessions require a positive exact rate")
            if int(row.numerator) > 2**63 - 1 or int(row.denominator) > 2**63 - 1:
                raise ValueError("rate exceeds the pinned signer wire bounds")
            posted = await session.scalar(
                text(
                    "SELECT COALESCE(sum(amount),0) FROM ledger_postings WHERE tenant_id=:tenant "
                    "AND account_code='payer:'||:account AND unit='wei'"
                ),
                {"tenant": str(principal.tenant_id), "account": str(principal.account_id)},
            )
            account_room = min(
                int(posted or 0) - int(exposure.open_lease_exposure),
                int(row.exposure_cap) - int(exposure.open_lease_exposure),
            )
            global_room = int(global_row.exposure_cap) - int(global_row.open_exposure)
            if requested_cap > min(account_room, global_room):
                raise ValueError("insufficient balance or exposure capacity")
            expires = now + timedelta(seconds=ttl_seconds)
            policy_snapshot_json = {
                "account_exposure_cap": int(row.exposure_cap),
                "account_open_exposure": int(exposure.open_lease_exposure),
                "allowed": True,
                "capability": capability,
                "global_exposure_cap": int(global_row.exposure_cap),
                "global_open_exposure": int(global_row.open_exposure),
                "model": model,
                "posted_balance": int(posted or 0),
                "rate_card_id": row.rate_card_id,
                "rate_denominator": int(row.denominator),
                "rate_numerator": int(row.numerator),
                "requested_cap": requested_cap,
            }
            policy_snapshot = hashlib.sha256(
                json.dumps(
                    policy_snapshot_json,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            session_id, lease_id = _id("ssn"), _id("lease")
            await session.execute(
                text("""
                INSERT INTO signer_sessions(id,tenant_id,account_id,principal_id,credential_id,
                  auth_source,signer_id,token_hash,operation_key,request_hash,capability,model,app,
                  expires_at,created_at)
                VALUES (:id,:tenant,:account,:principal,:credential,:auth_source,:signer,:hash,
                  :operation_key,:request_hash,:capability,:model,:app,:expires,:now)
            """),
                {
                    "id": session_id,
                    "tenant": str(principal.tenant_id),
                    "account": str(principal.account_id),
                    "principal": str(principal.id),
                    "credential": credential_id,
                    "auth_source": "credential" if credential_id else "browser",
                    "hash": token_hash,
                    "operation_key": operation_key,
                    "request_hash": request_hash,
                    "capability": capability,
                    "model": model,
                    "app": app,
                    "signer": self.signer_id,
                    "expires": expires,
                    "now": now,
                },
            )
            lease = (
                await session.execute(
                    text("""
                INSERT INTO leases(id,session_id,tenant_id,account_id,principal_id,rate_card_id,
                  capability,model,app,rate_numerator,rate_denominator,charge_unit,quantity_unit,
                  policy_snapshot,policy_snapshot_json,cap,available,unit,expires_at,created_at)
                VALUES (:id,:session,:tenant,:account,:principal,:rate,:capability,:model,:app,
                  :numerator,:denominator,'wei',:quantity_unit,:policy,
                  CAST(:policy_json AS jsonb),:cap,:cap,
                  'wei',:expires,:now)
                RETURNING *
            """),
                    {
                        "id": lease_id,
                        "session": session_id,
                        "tenant": str(principal.tenant_id),
                        "account": str(principal.account_id),
                        "principal": str(principal.id),
                        "rate": row.rate_card_id,
                        "capability": capability,
                        "model": model,
                        "app": app,
                        "numerator": int(row.numerator),
                        "denominator": int(row.denominator),
                        "quantity_unit": row.quantity_unit,
                        "policy": policy_snapshot,
                        "policy_json": json.dumps(policy_snapshot_json, sort_keys=True),
                        "cap": requested_cap,
                        "expires": expires,
                        "now": now,
                    },
                )
            ).one()
            await session.execute(
                text(
                    "UPDATE account_exposures SET open_lease_exposure=open_lease_exposure+:cap WHERE account_id=:account"
                ),
                {"cap": requested_cap, "account": str(principal.account_id)},
            )
            await session.execute(
                text("UPDATE global_exposure SET open_exposure=open_exposure+:cap WHERE singleton"),
                {"cap": requested_cap},
            )
            return SignerSession(
                session_id, "", self.signer_url, self.discovery_url, expires, self._lease(lease)
            )

    async def authorize(
        self, *, bearer_hash: bytes, signer_id: str, state: PaymentState, now: datetime
    ) -> Admission:
        async with self.sessions.begin() as session:
            await self._global_lock(session)
            global_row = (
                await session.execute(
                    text("SELECT * FROM global_exposure WHERE singleton FOR UPDATE")
                )
            ).one()
            if global_row.kill_switch:
                return Admission(False, DenialReason.KILL_SWITCH_ACTIVE)
            binding = (
                await session.execute(
                    text(
                        "SELECT account_id,credential_id FROM signer_sessions "
                        "WHERE token_hash=:hash AND signer_id=:signer"
                    ),
                    {"hash": bearer_hash, "signer": signer_id},
                )
            ).one_or_none()
            if binding is None:
                return Admission(False, DenialReason.UNKNOWN_CREDENTIAL)
            await session.execute(
                text("SELECT 1 FROM account_exposures WHERE account_id=:account FOR UPDATE"),
                {"account": binding.account_id},
            )
            await self._retire_expired_for_account(session, binding.account_id, now)
            if binding.credential_id is not None:
                await session.execute(
                    text("SELECT 1 FROM credentials WHERE id=:id FOR UPDATE"),
                    {"id": binding.credential_id},
                )
            auth = (
                await session.execute(
                    text("""
                SELECT s.*,l.id lease_id,l.cap,l.available,l.pending,l.settled,l.unit,
                  l.expires_at lease_expires,l.rate_card_id,l.rate_numerator,
                  l.rate_denominator,l.quantity_unit,l.capability,l.model,
                  a.status account_status,t.status tenant_status,p.status principal_status,
                  c.status credential_status,c.expires_at credential_expires,
                  EXISTS (SELECT 1 FROM principal_roles pr WHERE pr.principal_id=s.principal_id
                    AND pr.role='credential_holder') holder_role
                FROM signer_sessions s JOIN leases l ON l.session_id=s.id
                JOIN rate_cards r ON r.id=l.rate_card_id JOIN accounts a ON a.id=s.account_id
                JOIN tenants t ON t.id=s.tenant_id JOIN principals p ON p.id=s.principal_id
                LEFT JOIN credentials c ON c.id=s.credential_id
                WHERE s.token_hash=:hash AND s.signer_id=:signer FOR UPDATE OF s,l,a,t,p
            """),
                    {"hash": bearer_hash, "signer": signer_id},
                )
            ).one_or_none()
            if auth is None:
                return Admission(False, DenialReason.UNKNOWN_CREDENTIAL)
            if (state.sequence_number == 0 and state.auth_id not in {"", auth.id}) or (
                state.sequence_number > 0 and state.auth_id != auth.id
            ):
                return Admission(False, DenialReason.IDENTITY_UNAVAILABLE)
            digest = state_digest(replace(state, auth_id=auth.id))
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"{signer_id}:{state.state_id}"},
            )
            head = (
                await session.execute(
                    text(
                        "SELECT * FROM signer_state_heads WHERE signer_id=:signer AND state_id=:state FOR UPDATE"
                    ),
                    {"signer": signer_id, "state": state.state_id},
                )
            ).one_or_none()
            if head is None:
                if state.sequence_number != 0:
                    return Admission(False, DenialReason.OUT_OF_ORDER_STATE)
                elapsed = 0
            else:
                if head.session_id != auth.id:
                    return Admission(False, DenialReason.IDENTITY_UNAVAILABLE)
                if head.quarantined:
                    return Admission(False, DenialReason.STATE_FORK)
                if state.sequence_number <= head.last_sequence:
                    prior_hash = await session.scalar(
                        text(
                            "SELECT state_hash FROM authorization_receipts WHERE signer_id=:signer "
                            "AND state_id=:state AND sequence_number=:seq"
                        ),
                        {
                            "signer": signer_id,
                            "state": state.state_id,
                            "seq": state.sequence_number,
                        },
                    )
                    if digest == prior_hash:
                        return Admission(False, DenialReason.REPLAYED_STATE)
                    await session.execute(
                        text(
                            "UPDATE signer_state_heads SET quarantined=true "
                            "WHERE signer_id=:signer AND state_id=:state"
                        ),
                        {"signer": signer_id, "state": state.state_id},
                    )
                    forked_receipt = (
                        await session.execute(
                            text(
                                "SELECT id,status FROM authorization_receipts WHERE signer_id=:signer "
                                "AND state_id=:state AND sequence_number=:seq FOR UPDATE"
                            ),
                            {
                                "signer": signer_id,
                                "state": state.state_id,
                                "seq": state.sequence_number,
                            },
                        )
                    ).one_or_none()
                    if forked_receipt is not None and forked_receipt.status in {
                        "pending",
                        "unresolved",
                    }:
                        metering_schema = bool(
                            await session.scalar(
                                text("SELECT to_regclass('receipt_transition_events') IS NOT NULL")
                            )
                        )
                        if metering_schema:
                            case_id = _transition_id(forked_receipt.id, "fork_case")
                            await session.execute(
                                text("""
                                INSERT INTO reconciliation_cases(id,reservation_id,tenant_id,
                                  account_id,kind,status,reason,evidence,created_at)
                                SELECT :id,r.id,r.tenant_id,r.account_id,'forked_observation',
                                  'open','state_fork',
                                  '{}'::jsonb,:now FROM authorization_receipts r
                                WHERE r.id=:receipt
                                ON CONFLICT (reservation_id,kind)
                                  WHERE status='open' AND reservation_id IS NOT NULL DO NOTHING
                                """),
                                {"id": case_id, "receipt": forked_receipt.id, "now": now},
                            )
                            case_id = str(
                                await session.scalar(
                                    text("""SELECT id FROM reconciliation_cases
                                      WHERE reservation_id=:receipt AND kind='forked_observation'
                                        AND status='open'"""),
                                    {"receipt": forked_receipt.id},
                                )
                            )
                            await session.execute(
                                text("""
                                INSERT INTO metering_outbox(id,event_type,aggregate_id,payload,
                                  created_at) VALUES (:id,'metering.reconciliation.opened',:case,
                                  jsonb_build_object('schema_version','1.0',
                                    'kind','forked_observation','case_id',CAST(:case AS text)),:now)
                                ON CONFLICT (event_type,aggregate_id) DO NOTHING
                                """),
                                {
                                    "id": _transition_id(case_id, "outbox"),
                                    "case": case_id,
                                    "now": now,
                                },
                            )
                        await session.execute(
                            text(
                                "UPDATE authorization_receipts SET status='quarantined' WHERE id=:id"
                            ),
                            {"id": forked_receipt.id},
                        )
                        if metering_schema:
                            await session.execute(
                                text("""
                                INSERT INTO receipt_transition_events(id,receipt_id,from_status,
                                  to_status,reason,occurred_at,created_at)
                                VALUES (:id,:receipt,:old,'quarantined','state_fork',:now,:now)
                                """),
                                {
                                    "id": _transition_id(forked_receipt.id, "quarantined"),
                                    "receipt": forked_receipt.id,
                                    "old": forked_receipt.status,
                                    "now": now,
                                },
                            )
                    return Admission(False, DenialReason.STATE_FORK)
                if state.sequence_number != head.last_sequence + 1:
                    return Admission(False, DenialReason.OUT_OF_ORDER_STATE)
                await session.execute(
                    text(
                        "UPDATE authorization_receipts SET signer_confirmed_at="
                        "COALESCE(signer_confirmed_at,:now) WHERE signer_id=:signer "
                        "AND state_id=:state AND sequence_number=:seq"
                    ),
                    {
                        "now": now,
                        "signer": signer_id,
                        "state": state.state_id,
                        "seq": head.last_sequence,
                    },
                )
            if state.app != auth.app:
                return Admission(False, DenialReason.CAPABILITY_DENIED)
            try:
                current_ns = epoch_nanoseconds(state.last_update)
            except ValueError:
                return Admission(False, DenialReason.UNSUPPORTED_PAYMENT_SHAPE)
            elapsed = 0 if head is None else current_ns - int(head.last_update_ns)
            try:
                quantity, quantity_unit = reservation_quantity(state.payment_type, elapsed)
            except ValueError:
                return Admission(False, DenialReason.UNSUPPORTED_PAYMENT_SHAPE)
            if quantity_unit != auth.quantity_unit:
                return Admission(False, DenialReason.UNSUPPORTED_PAYMENT_SHAPE)
            if state.initial_price_per_unit != int(
                auth.rate_numerator
            ) or state.initial_pixels_per_unit != int(auth.rate_denominator):
                return Admission(False, DenialReason.UNSUPPORTED_PAYMENT_SHAPE)
            if auth.account_status != "active":
                return Admission(False, DenialReason.ACCOUNT_SUSPENDED)
            if auth.tenant_status != "active":
                return Admission(False, DenialReason.TENANT_SUSPENDED)
            if auth.expires_at <= now or auth.lease_expires <= now or auth.status == "expired":
                return Admission(False, DenialReason.LEASE_EXPIRED)
            if auth.principal_status != "active" or auth.status != "active":
                return Admission(False, DenialReason.IDENTITY_UNAVAILABLE)
            if not auth.holder_role:
                return Admission(False, DenialReason.IDENTITY_UNAVAILABLE)
            if auth.auth_source == "credential" and (
                auth.credential_status != "active"
                or (auth.credential_expires is not None and auth.credential_expires <= now)
            ):
                return Admission(False, DenialReason.UNKNOWN_CREDENTIAL)
            reserved = ceil_ratio(quantity, int(auth.rate_numerator), int(auth.rate_denominator))
            if reserved > int(auth.available):
                return Admission(False, DenialReason.LEASE_EXHAUSTED)
            await session.execute(
                text(
                    "UPDATE leases SET available=available-:amount,pending=pending+:amount WHERE id=:id"
                ),
                {"amount": reserved, "id": auth.lease_id},
            )
            await session.execute(
                text("""
                INSERT INTO authorization_receipts(id,signer_id,state_id,sequence_number,session_id,
                  lease_id,tenant_id,account_id,principal_id,reserved_amount,quantity,quantity_unit,
                  pm_session_id,app,orchestrator_address,last_update_ns,payment_type,state_hash,
                  rate_card_id,rate_numerator,rate_denominator,charge_unit,status,created_at)
                VALUES (:id,:signer,:state,:seq,:session,:lease,:tenant,:account,:principal,:amount,
                  :quantity,:unit,:pm,:app,:orch,:last,:type,:hash,:rate,:numerator,
                  :denominator,'wei','pending',:now)
            """),
                {
                    "id": _receipt_id(signer_id, state.state_id, state.sequence_number),
                    "signer": signer_id,
                    "state": state.state_id,
                    "seq": state.sequence_number,
                    "session": auth.id,
                    "lease": auth.lease_id,
                    "tenant": auth.tenant_id,
                    "account": auth.account_id,
                    "principal": auth.principal_id,
                    "amount": reserved,
                    "quantity": quantity,
                    "unit": quantity_unit,
                    "pm": state.pm_session_id,
                    "app": state.app,
                    "orch": state.orchestrator_address.lower(),
                    "last": current_ns,
                    "type": state.payment_type,
                    "hash": digest,
                    "rate": auth.rate_card_id,
                    "numerator": int(auth.rate_numerator),
                    "denominator": int(auth.rate_denominator),
                    "now": now,
                },
            )
            await session.execute(
                text("""
                INSERT INTO signer_state_heads(signer_id,state_id,session_id,last_sequence,last_update_ns,state_hash)
                VALUES (:signer,:state,:session,:seq,:last,:hash)
                ON CONFLICT(signer_id,state_id) DO UPDATE SET last_sequence=excluded.last_sequence,
                  last_update_ns=excluded.last_update_ns,state_hash=excluded.state_hash
            """),
                {
                    "signer": signer_id,
                    "state": state.state_id,
                    "session": auth.id,
                    "seq": state.sequence_number,
                    "last": current_ns,
                    "hash": digest,
                },
            )
            lease = Lease(
                auth.lease_id,
                int(auth.cap),
                int(auth.available) - reserved,
                int(auth.pending) + reserved,
                int(auth.settled),
                auth.unit,
                auth.lease_expires,
            )
            return Admission(
                True,
                session_id=auth.id,
                tenant_id=auth.tenant_id,
                account_id=auth.account_id,
                principal_id=auth.principal_id,
                lease=lease,
                reserved_amount=reserved,
                rate_numerator=int(auth.rate_numerator),
                rate_denominator=int(auth.rate_denominator),
                quantity_unit=quantity_unit,
            )

    async def refresh_session(
        self,
        *,
        principal: PrincipalContext,
        session_id: str,
        requested_cap: int | None,
        credential_id: str | None,
        operation_key: str,
        request_hash: str,
        token_hash: bytes,
        now: datetime,
    ) -> SignerSession:
        async with self.sessions.begin() as session:
            await self._global_lock(session)
            global_row = (
                await session.execute(
                    text("SELECT * FROM global_exposure WHERE singleton FOR UPDATE")
                )
            ).one()
            if global_row.kill_switch:
                raise ValueError("signer authorization is disabled")
            previous = (
                await session.execute(
                    text(
                        "SELECT id,request_hash FROM signer_sessions "
                        "WHERE principal_id=:principal AND operation_key=:key"
                    ),
                    {"principal": str(principal.id), "key": operation_key},
                )
            ).one_or_none()
            if previous is not None:
                if previous.request_hash != request_hash:
                    raise SignerConflict("Idempotency-Key reused with a different request")
                raise SignerConflict(f"operation already created signer session {previous.id}")
            old = (
                await session.execute(
                    text("""
                SELECT s.*,l.id lease_id,l.cap,l.available,l.pending,l.settled,l.unit,
                  l.expires_at lease_expires,l.rate_card_id,l.capability,l.model,l.app,
                  l.rate_numerator,l.rate_denominator,l.charge_unit,l.quantity_unit,
                  l.policy_snapshot,l.policy_snapshot_json,a.status account_status,t.status tenant_status,
                  p.status principal_status,c.status credential_status,
                  c.expires_at credential_expires,policy.allowed policy_allowed,
                  EXISTS (SELECT 1 FROM principal_roles pr WHERE pr.principal_id=s.principal_id
                    AND pr.role='credential_holder') holder_role
                FROM signer_sessions s JOIN leases l ON l.session_id=s.id
                JOIN account_exposures e ON e.account_id=s.account_id
                JOIN accounts a ON a.id=s.account_id JOIN tenants t ON t.id=s.tenant_id
                JOIN principals p ON p.id=s.principal_id
                JOIN account_capability_policies policy ON policy.account_id=s.account_id
                  AND policy.capability=s.capability AND policy.model=COALESCE(s.model,'')
                LEFT JOIN credentials c ON c.id=s.credential_id
                WHERE s.id=:id AND s.principal_id=:principal FOR UPDATE OF e,a,t,p,s,l
            """),
                    {"id": session_id, "principal": str(principal.id)},
                )
            ).one_or_none()
            if old is None or old.status != "active" or old.expires_at <= now:
                raise ValueError("signer session is not refreshable")
            if old.credential_id != credential_id:
                raise ValueError("session refresh requires its original authentication source")
            if (
                any(
                    value != "active"
                    for value in (old.account_status, old.tenant_status, old.principal_status)
                )
                or not old.policy_allowed
                or not old.holder_role
            ):
                raise ValueError("signer session scope is unavailable")
            if old.auth_source == "credential":
                credential = (
                    await session.execute(
                        text("SELECT status,expires_at FROM credentials WHERE id=:id FOR UPDATE"),
                        {"id": old.credential_id},
                    )
                ).one_or_none()
                if (
                    credential is None
                    or credential.status != "active"
                    or (credential.expires_at is not None and credential.expires_at <= now)
                ):
                    raise ValueError("source credential is unavailable")
            cap = int(old.available) if requested_cap is None else requested_cap
            if cap <= 0 or cap > int(old.available):
                raise ValueError("replacement cap exceeds remaining availability")
            expires = min(old.expires_at, now + timedelta(hours=1))
            new_id, lease_id = _id("ssn"), _id("lease")
            await session.execute(
                text("UPDATE signer_sessions SET status='revoked' WHERE id=:id"), {"id": old.id}
            )
            await session.execute(
                text("UPDATE leases SET available=0 WHERE id=:id"), {"id": old.lease_id}
            )
            await session.execute(
                text(
                    "UPDATE account_exposures SET open_lease_exposure=open_lease_exposure-:released WHERE account_id=:account"
                ),
                {"released": int(old.available) - cap, "account": old.account_id},
            )
            await session.execute(
                text(
                    "UPDATE global_exposure SET open_exposure=open_exposure-:released WHERE singleton"
                ),
                {"released": int(old.available) - cap},
            )
            await session.execute(
                text("""
                INSERT INTO signer_sessions(id,tenant_id,account_id,principal_id,credential_id,
                  auth_source,signer_id,token_hash,operation_key,request_hash,capability,model,app,
                  status,replaced_from_id,
                  expires_at,created_at)
                VALUES (:id,:tenant,:account,:principal,:credential,:auth_source,:signer,:hash,
                  :operation_key,:request_hash,:capability,:model,:app,'active',:old,:expires,:now)
            """),
                {
                    "id": new_id,
                    "tenant": old.tenant_id,
                    "account": old.account_id,
                    "principal": old.principal_id,
                    "credential": old.credential_id,
                    "auth_source": old.auth_source,
                    "signer": old.signer_id,
                    "hash": token_hash,
                    "operation_key": operation_key,
                    "request_hash": request_hash,
                    "capability": old.capability,
                    "model": old.model,
                    "app": old.app,
                    "old": old.id,
                    "expires": expires,
                    "now": now,
                },
            )
            lease = (
                await session.execute(
                    text("""
                INSERT INTO leases(id,session_id,tenant_id,account_id,principal_id,rate_card_id,
                  capability,model,app,rate_numerator,rate_denominator,charge_unit,quantity_unit,
                  policy_snapshot,policy_snapshot_json,cap,available,unit,expires_at,created_at)
                VALUES (:id,:session,:tenant,:account,:principal,:rate,:capability,:model,:app,
                  :numerator,:denominator,:charge,:quantity,:policy,
                  CAST(:policy_json AS jsonb),:cap,:cap,:unit,
                  :expires,:now)
                RETURNING *
            """),
                    {
                        "id": lease_id,
                        "session": new_id,
                        "tenant": old.tenant_id,
                        "account": old.account_id,
                        "principal": old.principal_id,
                        "rate": old.rate_card_id,
                        "capability": old.capability,
                        "model": old.model,
                        "app": old.app,
                        "numerator": old.rate_numerator,
                        "denominator": old.rate_denominator,
                        "charge": old.charge_unit,
                        "quantity": old.quantity_unit,
                        "policy": old.policy_snapshot,
                        "policy_json": json.dumps(old.policy_snapshot_json, sort_keys=True),
                        "cap": cap,
                        "unit": old.unit,
                        "expires": expires,
                        "now": now,
                    },
                )
            ).one()
            return SignerSession(
                new_id, "", self.signer_url, self.discovery_url, expires, self._lease(lease)
            )

    @staticmethod
    def _scope(principal: PrincipalContext) -> tuple[str, dict[str, str]]:
        if principal.is_operator:
            return "TRUE", {}
        if principal.tenant_id is None:
            return "FALSE", {}
        if Role.TENANT_ADMIN in principal.roles:
            return "s.tenant_id=:tenant", {"tenant": str(principal.tenant_id)}
        return "s.principal_id=:principal", {"principal": str(principal.id)}

    async def list_sessions(self, principal: PrincipalContext) -> Sequence[SignerSession]:
        predicate, parameters = self._scope(principal)
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(f"""
                    SELECT s.id,s.expires_at,l.id lease_id,l.cap,l.available,l.pending,l.settled,
                      l.unit,l.expires_at lease_expires
                    FROM signer_sessions s JOIN leases l ON l.session_id=s.id
                    WHERE {predicate} ORDER BY s.created_at DESC
                    """),
                    parameters,
                )
            ).all()
        return tuple(
            SignerSession(
                row.id,
                "",
                self.signer_url,
                self.discovery_url,
                row.expires_at,
                Lease(
                    row.lease_id,
                    int(row.cap),
                    int(row.available),
                    int(row.pending),
                    int(row.settled),
                    row.unit,
                    row.lease_expires,
                ),
            )
            for row in rows
        )

    async def list_leases(self, principal: PrincipalContext) -> Sequence[SignerSession]:
        return await self.list_sessions(principal)

    async def revoke_session(
        self, principal: PrincipalContext, session_id: str, now: datetime
    ) -> bool:
        async with self.sessions.begin() as session:
            await self._global_lock(session)
            await session.execute(text("SELECT * FROM global_exposure WHERE singleton FOR UPDATE"))
            predicate, scope = self._scope(principal)
            row = (
                await session.execute(
                    text(f"""
                    SELECT s.*,l.id lease_id,l.available,l.pending
                    FROM signer_sessions s JOIN leases l ON l.session_id=s.id
                    JOIN account_exposures e ON e.account_id=s.account_id
                    WHERE s.id=:id AND s.status='active' AND {predicate}
                    FOR UPDATE OF e,s,l
                    """),
                    {"id": session_id, **scope},
                )
            ).one_or_none()
            if row is None:
                return False
            available = int(row.available)
            new_status = "expired" if row.expires_at <= now else "revoked"
            await session.execute(
                text("UPDATE signer_sessions SET status=:status WHERE id=:id"),
                {"status": new_status, "id": session_id},
            )
            await session.execute(
                text("UPDATE leases SET available=0 WHERE id=:id"), {"id": row.lease_id}
            )
            await session.execute(
                text(
                    "UPDATE account_exposures SET open_lease_exposure="
                    "open_lease_exposure-:amount WHERE account_id=:account"
                ),
                {"amount": available, "account": row.account_id},
            )
            await session.execute(
                text(
                    "UPDATE global_exposure SET open_exposure=open_exposure-:amount WHERE singleton"
                ),
                {"amount": available},
            )
            await session.execute(
                text(
                    "INSERT INTO audit_events(id,tenant_id,actor_id,action,target_id,reason,request_id) "
                    "VALUES (:id,:tenant,:actor,'signer_session.revoked',:target,:reason,:request)"
                ),
                {
                    "id": _id("audit"),
                    "tenant": row.tenant_id,
                    "actor": str(principal.id),
                    "target": session_id,
                    "reason": new_status,
                    "request": _id("request"),
                },
            )
            return True

    async def get_kill_switch(self) -> dict[str, object]:
        async with self.sessions() as session:
            row = (
                await session.execute(text("SELECT * FROM global_exposure WHERE singleton"))
            ).one()
        return {
            "enabled": row.kill_switch,
            "reason": row.reason,
            "changed_at": row.changed_at,
            "actor_id": row.actor_id,
        }

    async def set_kill_switch(
        self, enabled: bool, reason: str, actor: PrincipalContext, now: datetime
    ) -> dict[str, object]:
        async with self.sessions.begin() as session:
            await self._global_lock(session)
            row = (
                await session.execute(
                    text(
                        "UPDATE global_exposure SET kill_switch=:enabled,reason=:reason,changed_at=:now,actor_id=:actor WHERE singleton RETURNING *"
                    ),
                    {"enabled": enabled, "reason": reason, "now": now, "actor": str(actor.id)},
                )
            ).one()
            await session.execute(
                text(
                    "INSERT INTO audit_events(id,tenant_id,actor_id,action,target_id,reason,request_id) VALUES (:id,NULL,:actor,'kill_switch.changed','global',:reason,:request)"
                ),
                {
                    "id": _id("audit"),
                    "actor": str(actor.id),
                    "reason": reason,
                    "request": _id("request"),
                },
            )
            return {
                "enabled": row.kill_switch,
                "reason": row.reason,
                "changed_at": row.changed_at,
                "actor_id": row.actor_id,
            }
