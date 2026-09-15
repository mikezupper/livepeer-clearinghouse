"""HTTP API for discovery, workloads, signer authorization, usage, and administration."""

import hmac
import re
from datetime import UTC, datetime
from typing import Annotated, cast

from contracts.ports.v2.protocols import DiscoverySnapshotUnavailable
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from clearinghouse.application.access import AccessDenied, AccessService
from clearinghouse.application.core_store import AccessIdentity, GlobalStop, StoredCredential
from clearinghouse.application.discovery import DiscoveryService, DiscoverySnapshot, sdk_discovery
from clearinghouse.application.pagination import Cursor, CursorCodec, InvalidCursor, StaleCursor
from clearinghouse.application.usage import UsageService
from clearinghouse.application.workloads import SignerState, WorkloadService
from clearinghouse.domain.core import WorkloadId
from clearinghouse.infrastructure.simple_config import CoreSettings


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkloadRequest(Model):
    offer_id: str = Field(min_length=1, max_length=128)
    ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    client_reference: str | None = Field(default=None, max_length=256)
    max_spend_wei: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$", max_length=78)


class StopRequest(Model):
    enabled: bool
    reason: str = Field(min_length=1, max_length=500)


class GoState(BaseModel):
    model_config = ConfigDict(extra="ignore")
    StateID: str = Field(min_length=1, max_length=256)
    PMSessionID: str = ""
    OrchestratorAddress: str
    InitialPricePerUnit: int = Field(ge=0)
    InitialPixelsPerUnit: int = Field(gt=0)
    SequenceNumber: int = Field(ge=0)
    AuthID: str = ""
    App: str = Field(default="", max_length=256)
    Type: str = Field(default="", max_length=32)
    LastUpdate: str = Field(default="", max_length=64)


class GoRequest(Model):
    headers: dict[str, list[str]]
    state: GoState


class Actor:
    def __init__(
        self, identity: AccessIdentity, credential: StoredCredential | None = None
    ) -> None:
        self.identity = identity
        self.credential = credential


def _services(request: Request) -> tuple[AccessService, CoreSettings]:
    return cast(AccessService, request.app.state.access), cast(
        CoreSettings, request.app.state.settings
    )


async def current_actor(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    och_session: Annotated[str | None, Cookie()] = None,
) -> Actor:
    access, _settings = _services(request)
    try:
        if authorization and authorization.startswith("Bearer och_live_"):
            credential = await access.authenticate_credential(authorization[7:])
            return Actor(
                AccessIdentity(credential.user_id, credential.account_id, "sdk-credential", False),
                credential,
            )
        session = await access.authenticate_session(och_session)
        return Actor(session.identity)
    except AccessDenied as error:
        raise HTTPException(401, "authentication required") from error


async def mutation_actor(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    och_session: Annotated[str | None, Cookie()] = None,
    och_csrf: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> Actor:
    actor = await current_actor(request, authorization, och_session)
    if actor.credential:
        return actor
    access, settings = _services(request)
    origin = request.headers.get("origin", "").rstrip("/")
    if (
        origin not in settings.origin_values
        or not och_csrf
        or not x_csrf_token
        or not hmac.compare_digest(och_csrf, x_csrf_token)
    ):
        raise HTTPException(403, "CSRF validation failed")
    session = await access.authenticate_session(och_session)
    if not hmac.compare_digest(session.csrf_digest, access.digest("csrf", och_csrf)):
        raise HTTPException(403, "CSRF validation failed")
    return actor


def _price(price) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "numerator": str(price.numerator),
        "denominator": str(price.denominator),
        "currency": price.currency,
        "quantity_unit": price.quantity_unit,
    }


_GO_TIMESTAMP = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$"
)


def _go_timestamp_ns(value: str) -> int | None:
    if not value:
        return None
    matched = _GO_TIMESTAMP.fullmatch(value)
    if matched is None:
        return None
    base, fraction, offset = matched.groups()
    parsed = datetime.fromisoformat(base + ("+00:00" if offset == "Z" else offset))
    seconds = int(parsed.timestamp())
    return seconds * 1_000_000_000 + int((fraction or "").ljust(9, "0"))


def _offer(value) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "id": value.id,
        "runner_url": value.orchestrator_url,
        "orchestrator_address": value.orchestrator_address,
        "capability": value.capability,
        "model": value.model,
        "constraints": dict(value.constraints),
        "price": _price(value.price),
        "observed_at": value.observed_at.isoformat(),
        "expires_at": value.expires_at.isoformat(),
    }


def _discovery_headers(response: Response, snapshot: DiscoverySnapshot) -> None:
    response.headers["X-Clearinghouse-Discovery-Stale"] = str(snapshot.stale).lower()
    if snapshot.stale:
        response.headers["Warning"] = '110 - "Response is stale"'


def _workload(value) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "id": value.id,
        "account_id": value.account_id,
        "capability": value.capability,
        "model": value.model,
        "offer_id": value.offer_id,
        "quoted_price": _price(value.max_price),
        "status": value.status,
        "client_reference": value.client_reference,
        "max_spend_wei": str(value.max_spend_wei) if value.max_spend_wei is not None else None,
        "runner_session_id": value.runner_session_id,
        "manifest_id": value.manifest_id,
        "payment_session_id": value.payment_session_id,
        "created_at": value.created_at.isoformat(),
        "expires_at": value.expires_at.isoformat(),
    }


def _usage(value) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "id": value.id,
        "workload_id": value.workload_id,
        "manifest_id": value.manifest_id,
        "payment_session_id": value.payment_session_id,
        "capability": value.capability,
        "quantity": str(value.quantity),
        "quantity_unit": value.quantity_unit,
        "computed_fee": str(value.computed_fee),
        "currency": value.currency,
        "ticket_count": value.ticket_count,
        "sequence_number": value.sequence_number,
        "occurred_at": value.occurred_at.isoformat(),
        "status": value.status,
    }


def create_core_router(
    discovery: DiscoveryService,
    workloads: WorkloadService,
    usage: UsageService,
    settings: CoreSettings,
) -> APIRouter:
    router = APIRouter(prefix="/v1")
    cursors = CursorCodec(settings.auth_pepper.get_secret_value())

    def decode_cursor(
        token: str | None,
        *,
        collection: str,
        scope: str,
        filters: dict[str, str | None] | None = None,
    ) -> Cursor:
        try:
            return cursors.decode(token, collection=collection, scope=scope, filters=filters or {})
        except InvalidCursor as error:
            raise HTTPException(400, str(error)) from error

    def next_cursor(
        key: tuple[str, ...] | None,
        *,
        collection: str,
        scope: str,
        filters: dict[str, str | None] | None = None,
        snapshot: str | None = None,
        stale: bool = False,
    ) -> str | None:
        return (
            cursors.encode(
                collection=collection,
                scope=scope,
                filters=filters or {},
                key=key,
                snapshot=snapshot,
                stale=stale,
            )
            if key
            else None
        )

    @router.get("/offers")
    async def offers(
        response: Response,
        actor: Annotated[Actor, Depends(current_actor)],
        capability: str | None = None,
        model: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        filters = {"capability": capability, "model": model}
        decoded = decode_cursor(
            cursor,
            collection="offers",
            scope=str(actor.identity.account_id),
            filters=filters,
        )
        try:
            snapshot = await discovery.page(
                capability=capability,
                model=model,
                limit=limit,
                after=decoded.key,
                snapshot=decoded.snapshot,
                stale=decoded.stale,
            )
        except DiscoverySnapshotUnavailable as error:
            raise HTTPException(503, "priced discovery is temporarily unavailable") from error
        except StaleCursor as error:
            raise HTTPException(
                409, detail={"code": "stale_cursor", "message": str(error)}
            ) from error
        response.headers["X-Clearinghouse-Discovery-Stale"] = str(snapshot.stale).lower()
        if snapshot.stale:
            response.headers["Warning"] = '110 - "Response is stale"'
        return {
            "items": [_offer(value) for value in snapshot.page.items],
            "next_cursor": next_cursor(
                snapshot.page.next_key,
                collection="offers",
                scope=str(actor.identity.account_id),
                filters=filters,
                snapshot=snapshot.generation,
                stale=snapshot.stale,
            ),
        }

    @router.get("/discovery")
    async def sdk_discovery_endpoint(
        response: Response, capability: str | None = None, model: str | None = None
    ) -> list[dict[str, object]]:
        try:
            snapshot = await discovery.refresh(capability, model)
        except DiscoverySnapshotUnavailable as error:
            raise HTTPException(503, "priced discovery is temporarily unavailable") from error
        _discovery_headers(response, snapshot)
        return sdk_discovery(snapshot.offers)

    @router.post("/workloads", status_code=201)
    async def create_workload(
        body: WorkloadRequest, actor: Annotated[Actor, Depends(mutation_actor)]
    ) -> Response:
        try:
            issued = await workloads.create(
                actor.credential or actor.identity,
                offer_id=body.offer_id,
                ttl_seconds=body.ttl_seconds,
                client_reference=body.client_reference,
                max_spend_wei=int(body.max_spend_wei) if body.max_spend_wei is not None else None,
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        content = {
            **_workload(issued.workload),
            "token": issued.access.token,
            "sdk_token": issued.access.sdk_token(),
            "signer_url": issued.access.signer_url,
            "discovery_url": issued.access.discovery_url,
        }
        return JSONResponse(content, status_code=201, headers={"Cache-Control": "no-store"})

    @router.get("/workloads")
    async def list_workloads(
        actor: Annotated[Actor, Depends(current_actor)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        scope = str(actor.identity.account_id)
        decoded = decode_cursor(cursor, collection="workloads", scope=scope)
        page = await workloads.list(actor.identity, limit=limit, after=decoded.key)
        return {
            "items": [_workload(value) for value in page.items],
            "next_cursor": next_cursor(page.next_key, collection="workloads", scope=scope),
        }

    @router.delete("/workloads/{workload_id}", status_code=204)
    async def revoke_workload(
        workload_id: str, actor: Annotated[Actor, Depends(mutation_actor)]
    ) -> Response:
        try:
            await workloads.revoke(actor.identity, WorkloadId(workload_id))
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        return Response(status_code=204)

    @router.get("/usage")
    async def list_usage(
        actor: Annotated[Actor, Depends(current_actor)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        scope = str(actor.identity.account_id)
        decoded = decode_cursor(cursor, collection="usage", scope=scope)
        events = await usage.events(actor.identity, limit=limit, after=decoded.key)
        return {
            "items": [_usage(event) for event in events.items],
            "next_cursor": next_cursor(events.next_key, collection="usage", scope=scope),
        }

    @router.get("/costs")
    async def list_costs(
        actor: Annotated[Actor, Depends(current_actor)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        scope = str(actor.identity.account_id)
        decoded = decode_cursor(cursor, collection="costs", scope=scope)
        costs = await usage.costs(actor.identity, limit=limit, after=decoded.key)
        return {
            "items": [
                {
                    "workload": _workload(cost.workload),
                    "measured_quantity": str(cost.measured_quantity),
                    "measured_unit": cost.measured_unit,
                    "quoted_fee": str(cost.quoted_fee),
                    "computed_fee": str(cost.computed_fee),
                    "currency": cost.currency,
                    "event_count": cost.event_count,
                    "spend_ceiling": (
                        str(cost.spend_ceiling) if cost.spend_ceiling is not None else None
                    ),
                    "authorized_fee": str(cost.authorized_fee),
                    "pending_fee": str(cost.pending_fee),
                    "remaining_spend": (
                        str(cost.remaining_spend) if cost.remaining_spend is not None else None
                    ),
                }
                for cost in costs.items
            ],
            "next_cursor": next_cursor(costs.next_key, collection="costs", scope=scope),
        }

    @router.get("/summary")
    async def account_summary(
        actor: Annotated[Actor, Depends(current_actor)], request: Request
    ) -> dict[str, object]:
        async with request.app.state.store.transaction() as transaction:
            summary = await transaction.account_summary(
                actor.identity.account_id, now=datetime.now(UTC)
            )
        return {
            "offers": summary.offers,
            "credentials": summary.credentials,
            "workloads": summary.workloads,
            "active_workloads": summary.active_workloads,
            "usage_events": summary.usage_events,
            "computed_fee": str(summary.computed_fee),
            "currency": "wei",
        }

    @router.post("/compat/go-livepeer/authorize")
    async def signer_authorize(
        body: GoRequest, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, object]:
        expected = f"Bearer {settings.signer_webhook_secret.get_secret_value()}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "signer authentication failed")
        forwarded = [
            item
            for name, values in body.headers.items()
            if name.lower() == "authorization"
            for item in values
        ]
        token = (
            forwarded[0][7:] if len(forwarded) == 1 and forwarded[0].startswith("Bearer ") else ""
        )
        state = body.state
        decision = await workloads.authorize(
            token,
            SignerState(
                state.StateID,
                state.SequenceNumber,
                state.OrchestratorAddress,
                state.InitialPricePerUnit,
                state.InitialPixelsPerUnit,
                app=state.App,
                job_type=state.Type,
                payment_session_id=state.PMSessionID or None,
                last_update_ns=_go_timestamp_ns(state.LastUpdate),
            ),
        )
        result: dict[str, object] = {
            "status": decision.status,
            "expiry": 0,
            "reason": decision.reason,
        }
        if decision.allowed:
            result["auth_id"] = decision.auth_id
        return result

    async def admin(actor: Annotated[Actor, Depends(current_actor)]) -> Actor:
        if not actor.identity.is_admin:
            raise HTTPException(403, "administrator access required")
        return actor

    async def admin_mutation(actor: Annotated[Actor, Depends(mutation_actor)]) -> Actor:
        if not actor.identity.is_admin:
            raise HTTPException(403, "administrator access required")
        return actor

    @router.get("/admin/overview")
    async def admin_overview(
        actor: Annotated[Actor, Depends(admin)], request: Request
    ) -> dict[str, object]:
        del actor
        store = request.app.state.store
        async with store.transaction() as transaction:
            stop = await transaction.get_global_stop()
            summary = await transaction.admin_summary(now=datetime.now(UTC))
        return {
            "users": summary.users,
            "workloads": summary.workloads,
            "active_workloads": summary.active_workloads,
            "usage": summary.usage_events,
            "unmatched_usage": summary.unmatched_usage,
            "computed_fee": str(summary.computed_fee),
            "currency": "wei",
            "global_stop": {
                "enabled": stop.enabled,
                "reason": stop.reason,
                "changed_at": stop.changed_at.isoformat(),
            },
        }

    @router.get("/admin/users")
    async def admin_users(
        actor: Annotated[Actor, Depends(admin)],
        request: Request,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        del actor
        decoded = decode_cursor(cursor, collection="admin-users", scope="admin")
        async with request.app.state.store.transaction() as transaction:
            page = await transaction.list_identities(limit=limit, after=decoded.key)
        return {
            "items": [
                {
                    "user_id": value.user_id,
                    "account_id": value.account_id,
                    "email": value.email,
                    "is_admin": value.is_admin,
                }
                for value in page.items
            ],
            "next_cursor": next_cursor(page.next_key, collection="admin-users", scope="admin"),
        }

    @router.get("/admin/workloads")
    async def admin_workloads(
        actor: Annotated[Actor, Depends(admin)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        del actor
        decoded = decode_cursor(cursor, collection="admin-workloads", scope="admin")
        page = await workloads.list(limit=limit, after=decoded.key)
        return {
            "items": [_workload(value) for value in page.items],
            "next_cursor": next_cursor(page.next_key, collection="admin-workloads", scope="admin"),
        }

    @router.put("/admin/global-stop")
    async def set_global_stop(
        body: StopRequest,
        actor: Annotated[Actor, Depends(admin_mutation)],
        request: Request,
    ) -> dict[str, object]:
        del actor
        value = GlobalStop(body.enabled, body.reason.strip(), datetime.now().astimezone())
        async with request.app.state.store.transaction() as transaction:
            await transaction.set_global_stop(value)
        return {
            "enabled": value.enabled,
            "reason": value.reason,
            "changed_at": value.changed_at.isoformat(),
        }

    return router
