"""Quoted workload creation and go-livepeer authorization policy."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from clearinghouse.application.core_store import AccessIdentity, CoreStore, StoredCredential
from clearinghouse.application.gateway_access import GatewayAccess
from clearinghouse.application.pagination import KeysetPage
from clearinghouse.domain.core import (
    MAX_SIGNED_AMOUNT,
    AuthorizationId,
    ExactPrice,
    PaymentAuthorization,
    Workload,
    WorkloadId,
    WorkloadStatus,
    exact_cost,
)


@dataclass(frozen=True, slots=True)
class SignerState:
    state_id: str
    sequence_number: int
    orchestrator_address: str
    initial_price_per_unit: int
    initial_pixels_per_unit: int
    app: str = ""
    job_type: str = ""
    manifest_id: str | None = None
    payment_session_id: str | None = None
    last_update_ns: int | None = None

    @property
    def price(self) -> ExactPrice | None:
        unit = {
            "live": "seconds",
            "lv2v": "720p-pixel-seconds",
            "fixed": "fixed",
        }.get(self.job_type)
        return (
            ExactPrice(self.initial_price_per_unit, self.initial_pixels_per_unit, "wei", unit)
            if unit
            else None
        )

    def billable_quantity(self, previous_update_ns: int | None) -> int | None:
        """Conservatively mirror go-livepeer v0.9.2 remote-signer billing units."""

        if self.last_update_ns is None:
            return None
        if self.job_type == "fixed":
            return 1
        if self.job_type not in {"live", "lv2v"}:
            return None
        if previous_update_ns is None:
            return 10 if self.job_type == "live" else 60 * 1280 * 720 * 30
        delta_ns = self.last_update_ns - previous_update_ns
        if delta_ns <= 0:
            return None
        units_per_second = 1 if self.job_type == "live" else 1280 * 720 * 30
        numerator = delta_ns * units_per_second
        return (numerator + 1_000_000_000 - 1) // 1_000_000_000


@dataclass(frozen=True, slots=True)
class SignerDecision:
    allowed: bool
    status: int
    reason: str | None = None
    auth_id: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedWorkload:
    workload: Workload
    access: GatewayAccess


class WorkloadService:
    def __init__(
        self,
        store: CoreStore,
        *,
        pepper: str,
        signer_id: str,
        public_signer_url: str,
        public_discovery_url: str,
        clock: Callable[[], datetime] | None = None,
        secret_factory: Callable[[], str] | None = None,
    ) -> None:
        if len(pepper) < 16:
            raise ValueError("workload pepper must be at least 16 characters")
        self.store = store
        self.pepper = pepper.encode()
        self.signer_id = signer_id
        self.public_signer_url = public_signer_url.rstrip("/")
        self.public_discovery_url = public_discovery_url
        self.clock = clock or (lambda: datetime.now(UTC))
        self.secret_factory = secret_factory or (lambda: secrets.token_urlsafe(36))

    def digest(self, token: str) -> bytes:
        return hmac.new(self.pepper, f"workload\0{token}".encode(), hashlib.sha256).digest()

    async def create(
        self,
        actor: AccessIdentity | StoredCredential,
        *,
        offer_id: str,
        ttl_seconds: int = 3600,
        client_reference: str | None = None,
        max_spend_wei: int | None = None,
    ) -> IssuedWorkload:
        if not 60 <= ttl_seconds <= 86_400:
            raise ValueError("workload lifetime must be 60 to 86400 seconds")
        if max_spend_wei is not None and not 0 < max_spend_wei <= MAX_SIGNED_AMOUNT:
            raise ValueError("maximum workload spend must be a positive signed 64-bit integer")
        now = self.clock()
        async with self.store.transaction() as transaction:
            offer = await transaction.get_offer(offer_id, now=now)
            if offer is None:
                raise ValueError("price observation is missing or expired")
            token = f"och_work_{self.secret_factory()}"
            workload = Workload(
                WorkloadId(f"work_{self.secret_factory()}"),
                actor.account_id,
                actor.user_id,
                offer.capability,
                offer.model,
                offer.id,
                offer.price,
                WorkloadStatus.ACTIVE,
                now + timedelta(seconds=ttl_seconds),
                now,
                client_reference=client_reference.strip() if client_reference else None,
                max_spend_wei=max_spend_wei,
            )
            await transaction.put_workload(workload, self.digest(token))
        access = GatewayAccess(
            token,
            self.public_signer_url,
            self.public_discovery_url,
            workload.id,
            workload.expires_at.isoformat(),
            (dict(offer.constraints).get("orchestrator_url", offer.orchestrator_url),),
        )
        return IssuedWorkload(workload, access)

    async def authorize(self, token: str, state: SignerState) -> SignerDecision:
        now = self.clock()
        async with self.store.transaction() as transaction:
            stopped = await transaction.get_global_stop()
            if stopped.enabled:
                return SignerDecision(False, 503, "clearinghouse_stopped")
            workload = await transaction.get_workload_by_token(self.digest(token))
            if workload is None:
                return SignerDecision(False, 401, "unknown_credential")
            if workload.status is not WorkloadStatus.ACTIVE or workload.expires_at <= now:
                return SignerDecision(False, 402, "workload_inactive")
            actual = state.price
            maximum = workload.max_price
            if actual is None:
                return SignerDecision(False, 402, "unsupported_job_type")
            if state.app != workload.capability:
                return SignerDecision(False, 402, "capability_mismatch")
            if actual.currency != maximum.currency or actual.quantity_unit != maximum.quantity_unit:
                return SignerDecision(False, 402, "price_unit_mismatch")
            if actual.numerator * maximum.denominator > maximum.numerator * actual.denominator:
                return SignerDecision(False, 402, "price_exceeds_quote")
            offer = await transaction.get_offer(str(workload.offer_id), now=workload.created_at)
            if (
                offer is not None
                and offer.orchestrator_address
                and offer.orchestrator_address.lower() != state.orchestrator_address.lower()
            ):
                return SignerDecision(False, 402, "orchestrator_mismatch")
            authorization_id = AuthorizationId(str(workload.id))
            previous = await transaction.get_authorization(authorization_id)
            if previous is not None and previous.state_id != state.state_id:
                return SignerDecision(False, 402, "workload_already_bound")
            if previous is not None and workload.max_spend_wei is None:
                return SignerDecision(True, 200, auth_id=str(workload.id))
            if previous is not None and state.sequence_number == previous.sequence_number:
                if state.last_update_ns != previous.signer_last_update_ns:
                    return SignerDecision(False, 409, "authorization_conflict")
                return SignerDecision(True, 200, auth_id=str(workload.id))
            if previous is not None and state.sequence_number != previous.sequence_number + 1:
                return SignerDecision(False, 409, "authorization_sequence_conflict")

            authorized_fee = previous.authorized_fee if previous else 0
            signer_last_update_ns = previous.signer_last_update_ns if previous else None
            if workload.max_spend_wei is not None:
                if previous is None and state.sequence_number != 0:
                    return SignerDecision(False, 409, "authorization_sequence_conflict")
                quantity = state.billable_quantity(signer_last_update_ns)
                if quantity is None:
                    return SignerDecision(False, 402, "spend_exposure_unavailable")
                reservation = exact_cost(quantity, actual)
                aggregates = await transaction.usage_aggregates((workload.id,))
                attributed_fee = aggregates[0].computed_fee if aggregates else 0
                if max(attributed_fee, authorized_fee) + reservation > workload.max_spend_wei:
                    return SignerDecision(False, 402, "spend_ceiling_exceeded")
                authorized_fee += reservation

            stored = await transaction.put_authorization(
                PaymentAuthorization(
                    authorization_id,
                    workload.id,
                    self.signer_id,
                    state.state_id,
                    state.sequence_number,
                    state.orchestrator_address.lower(),
                    maximum,
                    now,
                    state.last_update_ns,
                    authorized_fee,
                )
            )
            if not stored:
                return SignerDecision(False, 409, "authorization_conflict")
            if previous is None:
                await transaction.bind_workload(
                    workload.id,
                    state_id=state.state_id,
                    manifest_id=state.manifest_id,
                    payment_session_id=state.payment_session_id,
                )
        return SignerDecision(True, 200, auth_id=str(workload.id))

    async def list(
        self,
        identity: AccessIdentity | None = None,
        *,
        limit: int = 50,
        after: tuple[str, ...] = (),
    ) -> KeysetPage[Workload]:
        now = self.clock()
        async with self.store.transaction() as transaction:
            stored = await transaction.list_workloads(
                identity.account_id if identity else None, limit=limit, after=after
            )
        return KeysetPage(
            tuple(
                replace(workload, status=workload.effective_status(now))
                for workload in stored.items
            ),
            stored.next_key,
        )

    async def revoke(self, identity: AccessIdentity, workload_id: WorkloadId) -> None:
        existing = None
        async with self.store.transaction() as transaction:
            existing = await transaction.get_workload(workload_id)
            if existing is None or existing.account_id != identity.account_id:
                raise ValueError("workload not found")
            if not await transaction.revoke_workload(workload_id, at=self.clock()):
                raise ValueError("workload is not active")
