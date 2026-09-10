"""Tenant-scoped usage, charge, and reconciliation HTTP reads."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from sqlalchemy.exc import SQLAlchemyError

from clearinghouse.adapters.http.accounts import (
    COOKIE_SECURITY,
    AccountProblem,
    Actor,
    account_responses,
)
from clearinghouse.application.metering import MeteringService
from clearinghouse.domain.metering import Charge, OpenReservation, ReconciliationCase, UsageEvent
from clearinghouse.infrastructure.metering import encode_cursor

router = APIRouter(prefix="/v1")


def service(request: Request) -> MeteringService:
    return cast(MeteringService, request.app.state.metering_service)


Service = Annotated[MeteringService, Depends(service)]
OpaqueAccount = Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{1,31}_[A-Za-z0-9_-]{8,128}$")]
OpaqueCursor = Annotated[str | None, Query(min_length=1, max_length=512)]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


IsoDatetime = Annotated[
    datetime, PlainSerializer(lambda value: value.isoformat(), return_type=str, when_used="json")
]


class PageResponse(ApiModel):
    next_cursor: str | None = Field(default=None, max_length=512)


class QuantityResponse(ApiModel):
    value: str = Field(max_length=78, pattern=r"^(0|[1-9][0-9]*)$")
    unit: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class UsagePriceSnapshotResponse(ApiModel):
    rate_numerator: str = Field(max_length=78, pattern=r"^(0|[1-9][0-9]*)$")
    rate_denominator: str = Field(max_length=78, pattern=r"^[1-9][0-9]*$")
    charge_unit: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    quantity_unit: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    source: Literal["rate_card"]
    source_id: str = Field(min_length=1, max_length=200)
    source_version: Literal["snapshot"]


class UsageProducerResponse(ApiModel):
    id: str = Field(min_length=1, max_length=200)
    kind: Literal["signer"]
    software: Literal["go-livepeer"]
    software_version: str = Field(min_length=7, max_length=64, pattern=r"^[0-9a-f]+$")


class UsageSourceResponse(ApiModel):
    kind: Literal["go_livepeer_create_signed_ticket"]
    event_id: str = Field(min_length=1, max_length=200)
    sequence_number: str = Field(max_length=78, pattern=r"^(0|[1-9][0-9]*)$")
    confirmation: Literal["kafka"]
    ticket_count: str = Field(max_length=78, pattern=r"^(0|[1-9][0-9]*)$")
    signed_current_time: str = Field(min_length=20, max_length=40)
    signed_current_time_unix_ns: str = Field(max_length=19, pattern=r"^(0|[1-9][0-9]*)$")


class UsageResponse(ApiModel):
    schema_version: Literal["1.0"]
    event_id: str = Field(min_length=1, max_length=200)
    reservation_id: str = Field(min_length=1, max_length=200)
    lease_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=200)
    account_id: str = Field(min_length=1, max_length=200)
    principal_id: str = Field(min_length=1, max_length=200)
    manifest_id: str = Field(min_length=1, max_length=200)
    capability: str = Field(min_length=1, max_length=128)
    model: str | None = Field(default=None, max_length=512)
    quantity: QuantityResponse
    price_snapshot: UsagePriceSnapshotResponse
    producer: UsageProducerResponse
    occurred_at: str = Field(min_length=20, max_length=40)
    source: UsageSourceResponse


class UsagePageResponse(ApiModel):
    items: list[UsageResponse] = Field(max_length=200)
    page: PageResponse


class ChargePriceSnapshotResponse(ApiModel):
    rate_card_id: str = Field(min_length=1, max_length=200)
    rate_numerator: str = Field(max_length=78, pattern=r"^(0|[1-9][0-9]*)$")
    rate_denominator: str = Field(max_length=78, pattern=r"^[1-9][0-9]*$")
    quantity_unit: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class ChargeResponse(ApiModel):
    id: str = Field(min_length=1, max_length=200)
    usage_event_id: str = Field(min_length=1, max_length=200)
    reservation_id: str = Field(min_length=1, max_length=200)
    lease_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=200)
    account_id: str = Field(min_length=1, max_length=200)
    amount: QuantityResponse
    price_snapshot: ChargePriceSnapshotResponse
    created_at: IsoDatetime


class ChargePageResponse(ApiModel):
    items: list[ChargeResponse] = Field(max_length=200)
    page: PageResponse


class ReconciliationResponse(ApiModel):
    id: str = Field(min_length=1, max_length=200)
    reservation_id: str | None = Field(default=None, max_length=200)
    tenant_id: str | None = Field(default=None, max_length=200)
    account_id: str | None = Field(default=None, max_length=200)
    kind: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)
    created_at: IsoDatetime
    resolved_at: IsoDatetime | None


class ReconciliationPageResponse(ApiModel):
    items: list[ReconciliationResponse] = Field(max_length=200)
    page: PageResponse


class OpenReservationResponse(ApiModel):
    id: str = Field(min_length=1, max_length=200)
    lease_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=200)
    account_id: str = Field(min_length=1, max_length=200)
    status: str = Field(min_length=1, max_length=64)
    reserved_amount: QuantityResponse
    sequence_number: str = Field(max_length=78, pattern=r"^(0|[1-9][0-9]*)$")
    signer_confirmed_at: IsoDatetime | None
    created_at: IsoDatetime


class OpenReservationPageResponse(ApiModel):
    items: list[OpenReservationResponse] = Field(max_length=200)
    page: PageResponse


class MeteringHealthResponse(ApiModel):
    status: Literal["ready", "degraded"]
    open_cases: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    global_exposure_cap: str = Field(max_length=78, pattern=r"^(0|[1-9][0-9]*)$")
    global_open_exposure: str = Field(max_length=78, pattern=r"^(0|[1-9][0-9]*)$")
    last_checkpoint_at: IsoDatetime | None
    last_heartbeat_at: IsoDatetime | None


def _usage(value: UsageEvent) -> UsageResponse:
    return UsageResponse.model_validate(
        {
            "schema_version": "1.0",
            "event_id": value.id,
            "reservation_id": value.reservation_id,
            "lease_id": value.lease_id,
            "tenant_id": value.tenant_id,
            "account_id": value.account_id,
            "principal_id": value.principal_id,
            "manifest_id": value.manifest_id,
            "capability": value.capability,
            **({"model": value.model} if value.model else {}),
            "quantity": {"value": str(value.quantity), "unit": value.quantity_unit},
            "price_snapshot": {
                "rate_numerator": str(value.rate_numerator),
                "rate_denominator": str(value.rate_denominator),
                "charge_unit": value.charge_unit,
                "quantity_unit": value.quantity_unit,
                "source": "rate_card",
                "source_id": value.rate_card_id,
                "source_version": "snapshot",
            },
            "producer": {
                "id": value.producer_id,
                "kind": "signer",
                "software": "go-livepeer",
                "software_version": "e8dcf7a34744d5cb6b65ba43c0d9160a3975ccc6",
            },
            "occurred_at": value.occurred_at_text,
            "source": {
                "kind": "go_livepeer_create_signed_ticket",
                "event_id": value.transport_event_id,
                "sequence_number": str(value.sequence_number),
                "confirmation": "kafka",
                "ticket_count": str(value.ticket_count),
                "signed_current_time": value.occurred_at_text,
                "signed_current_time_unix_ns": str(value.occurred_at_ns),
            },
        }
    )


def _charge(value: Charge) -> ChargeResponse:
    return ChargeResponse.model_validate(
        {
            "id": value.id,
            "usage_event_id": value.usage_event_id,
            "reservation_id": value.reservation_id,
            "lease_id": value.lease_id,
            "tenant_id": value.tenant_id,
            "account_id": value.account_id,
            "amount": {"value": str(value.amount), "unit": value.unit},
            "price_snapshot": {
                "rate_card_id": value.rate_card_id,
                "rate_numerator": str(value.rate_numerator),
                "rate_denominator": str(value.rate_denominator),
                "quantity_unit": value.quantity_unit,
            },
            "created_at": value.created_at.isoformat(),
        }
    )


def _case(value: ReconciliationCase) -> ReconciliationResponse:
    return ReconciliationResponse.model_validate(
        {
            "id": value.id,
            "reservation_id": value.reservation_id,
            "tenant_id": value.tenant_id,
            "account_id": value.account_id,
            "kind": value.kind,
            "status": value.status,
            "reason": value.reason,
            "created_at": value.created_at.isoformat(),
            "resolved_at": value.resolved_at.isoformat() if value.resolved_at else None,
        }
    )


def _reservation(value: OpenReservation) -> OpenReservationResponse:
    return OpenReservationResponse.model_validate(
        {
            "id": value.id,
            "lease_id": value.lease_id,
            "tenant_id": value.tenant_id,
            "account_id": value.account_id,
            "status": value.status,
            "reserved_amount": {"value": str(value.reserved_amount), "unit": value.unit},
            "sequence_number": str(value.sequence_number),
            "signer_confirmed_at": value.signer_confirmed_at.isoformat()
            if value.signer_confirmed_at
            else None,
            "created_at": value.created_at.isoformat(),
        }
    )


def _page(values: list[Any], limit: int) -> tuple[list[Any], str | None]:
    visible = values[:limit]
    cursor = None
    if len(values) > limit:
        last = visible[-1]
        cursor = encode_cursor(last.created_at, last.id)
    return visible, cursor


async def _read(call: Any) -> Any:
    try:
        return await call
    except PermissionError as error:
        raise AccountProblem(403, "Forbidden") from error
    except ValueError as error:
        raise AccountProblem(400, "Invalid request") from error
    except SQLAlchemyError as error:
        raise AccountProblem(503, "Metering storage is unavailable") from error


@router.get(
    "/usage",
    tags=["usage"],
    status_code=status.HTTP_200_OK,
    response_model=UsagePageResponse,
    response_model_exclude_unset=True,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def list_usage(
    metering: Service,
    actor: Actor,
    response: Response,
    account_id: OpaqueAccount = None,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> UsagePageResponse:
    response.headers["Cache-Control"] = "no-store"
    values = list(await _read(metering.list_usage(actor, account_id, limit, cursor)))
    visible, next_cursor = _page(values, limit)
    return UsagePageResponse(
        items=[_usage(value) for value in visible], page=PageResponse(next_cursor=next_cursor)
    )


@router.get(
    "/charges",
    tags=["ledger"],
    status_code=status.HTTP_200_OK,
    response_model=ChargePageResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def list_charges(
    metering: Service,
    actor: Actor,
    response: Response,
    account_id: OpaqueAccount = None,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ChargePageResponse:
    response.headers["Cache-Control"] = "no-store"
    values = list(await _read(metering.list_charges(actor, account_id, limit, cursor)))
    visible, next_cursor = _page(values, limit)
    return ChargePageResponse(
        items=[_charge(value) for value in visible], page=PageResponse(next_cursor=next_cursor)
    )


@router.get(
    "/operations/reconciliation",
    tags=["operations"],
    status_code=status.HTTP_200_OK,
    response_model=ReconciliationPageResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def list_reconciliations(
    metering: Service,
    actor: Actor,
    response: Response,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ReconciliationPageResponse:
    response.headers["Cache-Control"] = "no-store"
    values = list(await _read(metering.list_reconciliations(actor, limit, cursor)))
    visible, next_cursor = _page(values, limit)
    return ReconciliationPageResponse(
        items=[_case(value) for value in visible], page=PageResponse(next_cursor=next_cursor)
    )


@router.get(
    "/open-reservations",
    tags=["usage"],
    status_code=status.HTTP_200_OK,
    response_model=OpenReservationPageResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def list_open_reservations(
    metering: Service,
    actor: Actor,
    response: Response,
    account_id: OpaqueAccount = None,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> OpenReservationPageResponse:
    response.headers["Cache-Control"] = "no-store"
    values = list(await _read(metering.list_open_reservations(actor, account_id, limit, cursor)))
    visible, next_cursor = _page(values, limit)
    return OpenReservationPageResponse(
        items=[_reservation(value) for value in visible], page=PageResponse(next_cursor=next_cursor)
    )


@router.get(
    "/operations/metering-health",
    tags=["operations"],
    status_code=status.HTTP_200_OK,
    response_model=MeteringHealthResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def metering_health(
    metering: Service, actor: Actor, response: Response
) -> MeteringHealthResponse:
    response.headers["Cache-Control"] = "no-store"
    return MeteringHealthResponse.model_validate(await _read(metering.health(actor)))
