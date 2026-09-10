"""HTTP boundaries for sessions and the pinned go-livepeer callback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Never, cast
from uuid import uuid4

from fastapi import APIRouter, Cookie, Depends, Header, Path, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError

from clearinghouse.adapters.http.accounts import _context
from clearinghouse.application.accounts import AccountService
from clearinghouse.application.signer import SignerConflict, SignerService
from clearinghouse.domain.accounts import PrincipalContext
from clearinghouse.domain.signer import Admission, PaymentState
from clearinghouse.infrastructure.telemetry import Outcome, Reason, get_telemetry, safe_reason

router = APIRouter(prefix="/v1")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSession(Model):
    capability: str = Field(min_length=1, max_length=128)
    model: str | None = Field(default=None, max_length=512)
    app: str = Field(min_length=1, max_length=256)
    requested_cap: str = Field(max_length=78, pattern=r"^[1-9][0-9]*$")
    unit: Literal["wei"]
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


class RefreshSession(Model):
    requested_cap: str | None = Field(default=None, max_length=78, pattern=r"^[1-9][0-9]*$")


class GoState(Model):
    StateID: str = Field(min_length=1, max_length=256)
    PMSessionID: str = Field(max_length=256)
    LastUpdate: str = Field(min_length=20, max_length=35)
    OrchestratorAddress: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    App: str = Field(max_length=256)
    AuthExpiry: int = Field(ge=0, le=2**63 - 1)
    SenderNonce: int = Field(ge=0, le=2**32 - 1)
    Balance: str = Field(max_length=160, pattern=r"^(?:-?(?:0|[1-9][0-9]*)(?:/[1-9][0-9]*)?)?$")
    InitialPricePerUnit: int = Field(gt=0, le=2**63 - 1)
    InitialPixelsPerUnit: int = Field(gt=0, le=2**63 - 1)
    Type: str = Field(max_length=32)
    SequenceNumber: int = Field(ge=0, le=2**64 - 1)
    AuthID: str = Field(max_length=256)


class GoRequest(Model):
    headers: dict[str, list[str]] = Field(max_length=64)
    state: GoState

    @model_validator(mode="after")
    def bound_forwarded_headers(self) -> GoRequest:
        size = 0
        for name, values in self.headers.items():
            if not name or len(name) > 128 or len(values) > 8:
                raise ValueError("forwarded headers exceed compatibility bounds")
            size += len(name)
            for value in values:
                if len(value) > 1024:
                    raise ValueError("forwarded header value exceeds compatibility bounds")
                size += len(value)
        if size > 8192:
            raise ValueError("forwarded headers exceed aggregate compatibility bound")
        return self


class NativeRequest(Model):
    bearer: str = Field(min_length=32, max_length=256)
    state: GoState


class KillSwitchRequest(Model):
    enabled: bool
    reason: str = Field(min_length=1, max_length=1000)


class SignerProblemResponse(Model):
    type: str
    title: str
    status: int
    request_id: str


class LeaseResponse(Model):
    id: str
    cap: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    available: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    pending: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    settled: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    unit: str
    expires_at: datetime


class SessionMetadataResponse(Model):
    id: str
    signer_url: str
    discovery_url: str
    expires_at: datetime
    lease: LeaseResponse


class CreatedSessionResponse(SessionMetadataResponse):
    token: str = Field(min_length=32, max_length=256)


class SessionListResponse(Model):
    items: list[SessionMetadataResponse]


class LeaseListResponse(Model):
    items: list[LeaseResponse]


class AdmissionIdentityResponse(Model):
    tenant_id: str
    account_id: str
    principal_id: str


class AuthorizeAllowResponse(Model):
    decision: Literal["allow"]
    identity: AdmissionIdentityResponse
    lease: LeaseResponse
    expires_in: Literal[0]


class AuthorizeDenyResponse(Model):
    decision: Literal["deny"]
    reason: str


class CompatPriceResponse(Model):
    price: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    currency: Literal["wei"]
    unit: str


class CompatAuthorizeResponse(Model):
    status: Literal[200, 401, 402, 503]
    expiry: Literal[0]
    reason: str | None = None
    auth_id: str | None = None
    maxPrice: CompatPriceResponse | None = None


class KillSwitchResponse(Model):
    enabled: bool
    reason: str
    actor_id: str | None
    changed_at: datetime


def _problem_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    schema = SignerProblemResponse.model_json_schema()
    return {
        code: {
            "description": "Signer request failed",
            "content": {"application/problem+json": {"schema": schema}},
        }
        for code in codes
    }


@dataclass(frozen=True, slots=True)
class SessionActor:
    principal: PrincipalContext
    credential_id: str | None = None


def service(request: Request) -> SignerService:
    return cast(SignerService, request.app.state.signer_service)


Service = Annotated[SignerService, Depends(service)]


async def session_actor(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    och_session: Annotated[str | None, Cookie()] = None,
    och_csrf: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> SessionActor:
    injected = getattr(request.state, "principal", None)
    if isinstance(injected, PrincipalContext):
        return SessionActor(injected)
    if authorization is not None and authorization.startswith("Bearer och_live_"):
        accounts = cast(AccountService | None, getattr(request.app.state, "account_service", None))
        match = (
            await accounts.authenticate_credential_context(authorization[7:]) if accounts else None
        )
        if match is None:
            _problem(status.HTTP_401_UNAUTHORIZED, "Authentication required")
        return SessionActor(*match)
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        _problem(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    authenticated = (
        await auth.current_principal(och_session)
        if request.method == "GET"
        else await auth.csrf_protected(request, och_session, och_csrf, x_csrf_token)
    )
    return SessionActor(_context(authenticated.principal))


SessionActorDependency = Annotated[SessionActor, Depends(session_actor)]


class SignerProblem(Exception):
    def __init__(self, status_code: int, title: str) -> None:
        self.status_code = status_code
        self.title = title


async def signer_problem_handler(_request: Request, error: SignerProblem) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        media_type="application/problem+json",
        content={
            "type": "urn:livepeer:clearinghouse:signer-error",
            "title": error.title,
            "status": error.status_code,
            "request_id": str(uuid4()),
        },
    )


def _problem(status_code: int, title: str) -> Never:
    raise SignerProblem(status_code, title)


def _state(value: GoState) -> PaymentState:
    return PaymentState(
        value.StateID,
        value.PMSessionID,
        value.LastUpdate,
        value.OrchestratorAddress,
        value.App,
        value.AuthExpiry,
        value.SenderNonce,
        value.Balance,
        value.InitialPricePerUnit,
        value.InitialPixelsPerUnit,
        value.Type,
        value.SequenceNumber,
        value.AuthID,
    )


def _admission(value: Admission) -> dict[str, Any]:
    if not value.allowed:
        return {"decision": "deny", "reason": value.reason}
    if value.lease is None:
        raise RuntimeError("allowed admission requires a lease")
    return {
        "decision": "allow",
        "identity": {
            "tenant_id": value.tenant_id,
            "account_id": value.account_id,
            "principal_id": value.principal_id,
        },
        "lease": {
            "id": value.lease.id,
            "cap": str(value.lease.cap),
            "available": str(value.lease.available),
            "pending": str(value.lease.pending),
            "settled": str(value.lease.settled),
            "unit": value.lease.unit,
            "expires_at": value.lease.expires_at.isoformat(),
        },
        "expires_in": 0,
    }


@router.post(
    "/sessions",
    status_code=201,
    tags=["sessions"],
    response_model=CreatedSessionResponse,
    responses=_problem_responses(401, 402, 403, 409, 503),
)
async def create_session(
    body: CreateSession,
    signer: Service,
    actor: SessionActorDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=256)],
) -> Response:
    try:
        value = await signer.create_session(
            actor.principal,
            body.capability,
            body.model,
            body.app,
            int(body.requested_cap),
            body.unit,
            body.ttl_seconds,
            idempotency_key,
            actor.credential_id,
        )
    except SignerConflict as error:
        _problem(409, str(error))
    except PermissionError as error:
        _problem(403, str(error))
    except ValueError as error:
        _problem(402, str(error))
    except SQLAlchemyError:
        _problem(503, "Signer authorization storage is unavailable")
    return JSONResponse(
        status_code=201,
        headers=_secret_headers(),
        content={
            "id": value.id,
            "token": value.token,
            "signer_url": value.signer_url,
            "discovery_url": value.discovery_url,
            "expires_at": value.expires_at.isoformat(),
            "lease": _admission(Admission(True, lease=value.lease))["lease"],
        },
    )


@router.post(
    "/authorize",
    tags=["authorization"],
    response_model=AuthorizeAllowResponse,
    responses={
        **_problem_responses(401, 503),
        402: {"description": "Authorization denied", "model": AuthorizeDenyResponse},
    },
)
async def authorize(
    body: NativeRequest, signer: Service, authorization: Annotated[str | None, Header()] = None
) -> JSONResponse:
    _signer_secret(signer, authorization)
    try:
        value = await signer.authorize(body.bearer, signer.signer_id, _state(body.state))
    except SQLAlchemyError:
        _problem(503, "Signer authorization storage is unavailable")
    if not value.allowed and value.reason == "ledger_unavailable":
        _problem(503, "Signer authorization dependency is unavailable")
    response_status = 200
    if not value.allowed:
        response_status = 402
    return JSONResponse(status_code=response_status, content=_admission(value))


@router.post(
    "/sessions/{session_id}/refresh",
    status_code=201,
    tags=["sessions"],
    response_model=CreatedSessionResponse,
    responses=_problem_responses(401, 403, 409, 503),
)
async def refresh_session(
    session_id: Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9_-]{7,127}$")],
    body: RefreshSession,
    signer: Service,
    actor: SessionActorDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=256)],
) -> JSONResponse:
    try:
        value = await signer.refresh_session(
            actor.principal,
            session_id,
            int(body.requested_cap) if body.requested_cap else None,
            idempotency_key,
            actor.credential_id,
        )
    except SignerConflict as error:
        _problem(409, str(error))
    except PermissionError as error:
        _problem(403, str(error))
    except ValueError as error:
        _problem(409, str(error))
    except SQLAlchemyError:
        _problem(503, "Signer authorization storage is unavailable")
    return JSONResponse(
        status_code=201,
        headers=_secret_headers(),
        content={
            "id": value.id,
            "token": value.token,
            "signer_url": value.signer_url,
            "discovery_url": value.discovery_url,
            "expires_at": value.expires_at.isoformat(),
            "lease": _admission(Admission(True, lease=value.lease))["lease"],
        },
    )


def _secret_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.get(
    "/sessions",
    tags=["sessions"],
    response_model=SessionListResponse,
    responses=_problem_responses(401, 403),
)
async def list_sessions(signer: Service, actor: SessionActorDependency) -> dict[str, Any]:
    values = await signer.list_sessions(actor.principal)
    return {"items": [_session(value, include_token=False) for value in values]}


@router.get(
    "/leases",
    tags=["leases"],
    response_model=LeaseListResponse,
    responses=_problem_responses(401, 403),
)
async def list_leases(signer: Service, actor: SessionActorDependency) -> dict[str, Any]:
    values = await signer.list_leases(actor.principal)
    return {"items": [_admission(Admission(True, lease=value.lease))["lease"] for value in values]}


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    tags=["sessions"],
    responses=_problem_responses(401, 403, 404),
)
async def revoke_session(
    session_id: Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9_-]{7,127}$")],
    signer: Service,
    actor: SessionActorDependency,
) -> Response:
    try:
        await signer.revoke_session(actor.principal, session_id)
    except ValueError as error:
        _problem(404, str(error))
    return Response(status_code=204)


@router.get(
    "/operations/kill-switch",
    tags=["operations"],
    response_model=KillSwitchResponse,
    responses=_problem_responses(401, 403),
)
async def get_kill_switch(signer: Service, actor: SessionActorDependency) -> dict[str, object]:
    try:
        return await signer.get_kill_switch(actor.principal)
    except PermissionError as error:
        _problem(403, str(error))


@router.put(
    "/operations/kill-switch",
    tags=["operations"],
    response_model=KillSwitchResponse,
    responses=_problem_responses(400, 401, 403),
)
async def set_kill_switch(
    body: KillSwitchRequest, signer: Service, actor: SessionActorDependency
) -> dict[str, object]:
    try:
        return await signer.set_kill_switch(body.enabled, body.reason, actor.principal)
    except PermissionError as error:
        _problem(403, str(error))


def _session(value: Any, *, include_token: bool) -> dict[str, Any]:
    result = {
        "id": value.id,
        "signer_url": value.signer_url,
        "discovery_url": value.discovery_url,
        "expires_at": value.expires_at.isoformat(),
        "lease": _admission(Admission(True, lease=value.lease))["lease"],
    }
    if include_token:
        result["token"] = value.token
    return result


def _signer_secret(signer: SignerService, header: str | None) -> None:
    if header is None or not header.startswith("Bearer ") or not signer.verify_webhook(header[7:]):
        _problem(status.HTTP_401_UNAUTHORIZED, "Signer authentication failed")


@router.post(
    "/compat/go-livepeer/authorize",
    tags=["authorization"],
    response_model=CompatAuthorizeResponse,
    responses=_problem_responses(401),
)
async def authorize_compat(
    body: GoRequest,
    signer: Service,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    _signer_secret(signer, authorization)
    forwarded = [
        value
        for name, values in body.headers.items()
        if name.lower() == "authorization"
        for value in values
    ]
    bearer = forwarded[0][7:] if len(forwarded) == 1 and forwarded[0].startswith("Bearer ") else ""
    try:
        value = await signer.authorize(bearer, signer.signer_id, _state(body.state))
    except SQLAlchemyError:
        get_telemetry().record_signer(Outcome.UNAVAILABLE, Reason.LEDGER_UNAVAILABLE)
        return JSONResponse({"status": 503, "reason": "ledger_unavailable", "expiry": 0})
    if not value.allowed:
        reason = safe_reason(value.reason)
        outcome = Outcome.UNAVAILABLE if reason is Reason.LEDGER_UNAVAILABLE else Outcome.DENIED
        get_telemetry().record_signer(outcome, reason)
        denied_status = 402
        if value.reason == "unknown_credential":
            denied_status = 401
        elif value.reason == "ledger_unavailable":
            denied_status = 503
        return JSONResponse({"status": denied_status, "reason": value.reason, "expiry": 0})
    get_telemetry().record_signer(Outcome.SUCCESS)
    response: dict[str, Any] = {"status": 200, "expiry": 0, "auth_id": value.session_id}
    if value.rate_denominator and value.rate_numerator is not None:
        numerator, denominator = value.rate_numerator, value.rate_denominator
        reduced = numerator // denominator if numerator % denominator == 0 else None
        if reduced is not None:
            response["maxPrice"] = {
                "price": str(reduced),
                "currency": "wei",
                "unit": value.quantity_unit,
            }
    return JSONResponse(response)
