"""FastAPI adapter for account, credential, catalog, and ledger workflows."""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any, Literal, Never
from uuid import uuid4

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clearinghouse.application.accounts import (
    AccountService,
    Conflict,
    DomainError,
    Forbidden,
    InvalidRequest,
    NotFound,
)
from clearinghouse.domain.accounts import (
    Account,
    AccountId,
    Credential,
    ExactRate,
    Grant,
    GrantKind,
    Principal,
    PrincipalContext,
    PrincipalId,
    Role,
    Status,
    Tenant,
    TenantId,
)
from clearinghouse.domain.auth import Principal as AuthPrincipal
from clearinghouse.infrastructure.metering import encode_cursor

router = APIRouter(prefix="/v1")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTenant(ApiModel):
    display_name: str = Field(min_length=1, max_length=200)


class UpdateTenant(ApiModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Status | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_property(self) -> UpdateTenant:
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        return self


class CreateAccount(ApiModel):
    tenant_id: str
    display_name: str = Field(min_length=1, max_length=200)
    unit: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    exposure_cap: str = Field(max_length=78, pattern=r"^(0|[1-9][0-9]*)$")


class UpdateAccount(ApiModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    exposure_cap: str | None = Field(default=None, max_length=78, pattern=r"^(0|[1-9][0-9]*)$")
    status: Status | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_property(self) -> UpdateAccount:
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        return self


class CreatePrincipal(ApiModel):
    tenant_id: str | None = None
    account_id: str | None = None
    display_name: str | None = Field(default=None, max_length=200)
    roles: frozenset[Role]


class UpdatePrincipal(ApiModel):
    status: Status | None = None
    roles: frozenset[Role] | None = None
    reason: str = Field(min_length=1, max_length=1000)


class LinkPrincipal(ApiModel):
    tenant_id: str
    account_id: str | None = None
    roles: frozenset[Role]
    reason: str = Field(min_length=1, max_length=1000)


class IssueCredential(ApiModel):
    account_id: str
    principal_id: str
    label: str = Field(min_length=1, max_length=200)
    expires_at: datetime | None = None


class Money(ApiModel):
    amount: str = Field(max_length=79, pattern=r"^-?(0|[1-9][0-9]*)$")
    unit: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")


class CreateGrant(ApiModel):
    account_id: str
    kind: GrantKind
    amount: Money
    reason: str = Field(min_length=1, max_length=1000)
    external_reference: str | None = Field(default=None, max_length=256)


class ExactRateBody(ApiModel):
    numerator: str = Field(max_length=78, pattern=r"^(0|[1-9][0-9]*)$")
    denominator: str = Field(max_length=78, pattern=r"^[1-9][0-9]*$")
    charge_unit: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    quantity_unit: Literal["fixed", "seconds", "pixels", "720p-pixel-seconds", "wei"]


class CreateRateCard(ApiModel):
    capability: str = Field(min_length=1, max_length=128)
    model: str | None = Field(default=None, max_length=512)
    rate: ExactRateBody
    effective_at: datetime


class SetCapabilityPolicy(ApiModel):
    capability: str = Field(min_length=1, max_length=128)
    model: str | None = Field(default=None, max_length=512)
    allowed: bool
    reason: str = Field(min_length=1, max_length=1000)


class ProblemResponse(ApiModel):
    """Stable RFC 9457-style problem returned by the HTTP boundary."""

    type: str
    title: str
    status: int
    request_id: str


class PageResponse(ApiModel):
    next_cursor: str | None


class TenantResponse(ApiModel):
    id: str
    display_name: str
    status: Status
    created_at: datetime


class TenantPageResponse(ApiModel):
    items: list[TenantResponse]
    page: PageResponse


class AccountResponse(ApiModel):
    id: str
    tenant_id: str
    display_name: str
    unit: str
    exposure_cap: str
    status: Status
    created_at: datetime


class AccountPageResponse(ApiModel):
    items: list[AccountResponse]
    page: PageResponse


class PrincipalResponse(ApiModel):
    id: str
    tenant_id: str | None
    account_id: str | None
    display_name: str | None
    status: Status
    roles: list[Role]
    created_at: datetime


class CredentialResponse(ApiModel):
    id: str
    account_id: str
    principal_id: str
    prefix: str
    label: str
    status: Status
    created_at: datetime


class IssuedCredentialResponse(ApiModel):
    credential: CredentialResponse
    secret: str


class GrantResponse(ApiModel):
    id: str
    account_id: str
    kind: GrantKind
    amount: Money
    reason: str
    external_reference: str | None
    actor_id: str
    created_at: datetime


class GrantPageResponse(ApiModel):
    items: list[GrantResponse]
    page: PageResponse


class BalanceResponse(ApiModel):
    account_id: str
    posted: Money
    open_lease_exposure: Money
    available: Money


class ExactRateResponse(ApiModel):
    numerator: str
    denominator: str
    charge_unit: str
    quantity_unit: Literal["fixed", "seconds", "pixels", "720p-pixel-seconds", "wei"]


class RateCardResponse(ApiModel):
    id: str
    version: str
    capability: str
    model: str | None
    rate: ExactRateResponse
    effective_at: datetime
    created_at: datetime


class CatalogEntryResponse(ApiModel):
    capability: str
    model: str | None
    rate: ExactRateResponse
    available: bool


COOKIE_SECURITY: dict[str, Any] = {"security": [{"cookieAuth": []}]}


def problem_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Describe only problem+json failures actually emitted by this boundary."""
    schema = ProblemResponse.model_json_schema()
    return {
        code: {
            "description": HTTPStatus(code).phrase,
            "content": {"application/problem+json": {"schema": schema}},
        }
        for code in codes
    }


def account_responses(*problem_codes: int, csrf: bool = False) -> dict[int | str, dict[str, Any]]:
    """Include the authentication problem shared by every account operation.

    ``csrf`` is a semantic call-site marker; CSRF rejection uses the same typed
    problem representation as other forbidden responses.
    """
    return problem_responses(*problem_codes, status.HTTP_401_UNAUTHORIZED)


def service_from_request(request: Request) -> AccountService:
    service: AccountService = request.app.state.account_service
    return service


async def principal_from_request(
    request: Request, och_session: Annotated[str | None, Cookie()] = None
) -> PrincipalContext:
    """Small seam overridden by the authentication adapter during composition."""
    injected = getattr(request.state, "principal", None)
    if isinstance(injected, PrincipalContext):
        return injected
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        raise AccountProblem(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    try:
        authenticated = await auth.current_principal(och_session)
    except HTTPException as error:
        raise AccountProblem(error.status_code, "Authentication required") from error
    principal = authenticated.principal
    return PrincipalContext(
        id=PrincipalId(principal.id),
        tenant_id=TenantId(principal.tenant_id) if principal.tenant_id else None,
        account_id=AccountId(principal.account_id) if principal.account_id else None,
        roles=frozenset(Role(role.value) for role in principal.roles),
    )


def _context(principal: AuthPrincipal) -> PrincipalContext:
    return PrincipalContext(
        id=PrincipalId(principal.id),
        tenant_id=TenantId(principal.tenant_id) if principal.tenant_id else None,
        account_id=AccountId(principal.account_id) if principal.account_id else None,
        roles=frozenset(Role(role.value) for role in principal.roles),
    )


async def protected_principal_from_request(
    request: Request,
    och_session: Annotated[str | None, Cookie()] = None,
    och_csrf: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> PrincipalContext:
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        injected = getattr(request.state, "principal", None)
        if isinstance(injected, PrincipalContext):
            return injected
        raise AccountProblem(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    try:
        authenticated = await auth.csrf_protected(request, och_session, och_csrf, x_csrf_token)
    except HTTPException as error:
        title = "CSRF validation failed" if error.status_code == 403 else "Authentication required"
        raise AccountProblem(error.status_code, title) from error
    return _context(authenticated.principal)


Service = Annotated[AccountService, Depends(service_from_request)]
Actor = Annotated[PrincipalContext, Depends(principal_from_request)]
ProtectedActor = Annotated[PrincipalContext, Depends(protected_principal_from_request)]
OpaqueCursor = Annotated[
    str | None, Query(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_-]+$")
]


def _page(values: list[Any], limit: int) -> tuple[list[Any], str | None]:
    visible = values[:limit]
    next_cursor = None
    if len(values) > limit:
        last = visible[-1]
        next_cursor = encode_cursor(last.created_at, last.id)
    return visible, next_cursor


class AccountProblem(Exception):
    def __init__(self, status_code: int, title: str) -> None:
        self.status_code = status_code
        self.title = title


async def account_problem_handler(_request: Request, error: AccountProblem) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        media_type="application/problem+json",
        content={
            "type": "urn:livepeer:clearinghouse:error",
            "title": error.title,
            "status": error.status_code,
            "request_id": str(uuid4()),
        },
    )


def _raise(error: DomainError) -> Never:
    code = status.HTTP_400_BAD_REQUEST
    if isinstance(error, Forbidden):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, NotFound):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, Conflict):
        code = status.HTTP_409_CONFLICT
    elif not isinstance(error, InvalidRequest):
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
    raise AccountProblem(code, str(error)) from error


def _tenant(item: Tenant) -> TenantResponse:
    return TenantResponse(
        id=item.id,
        display_name=item.display_name,
        status=item.status,
        created_at=item.created_at,
    )


def _account(item: Account) -> AccountResponse:
    return AccountResponse(
        id=item.id,
        tenant_id=item.tenant_id,
        display_name=item.display_name,
        unit=item.unit,
        exposure_cap=str(item.exposure_cap),
        status=item.status,
        created_at=item.created_at,
    )


def _principal(item: Principal) -> PrincipalResponse:
    return PrincipalResponse(
        id=item.id,
        tenant_id=item.tenant_id,
        account_id=item.account_id,
        display_name=item.display_name,
        status=item.status,
        roles=sorted(item.roles),
        created_at=item.created_at,
    )


def _credential(item: Credential) -> CredentialResponse:
    return CredentialResponse(
        id=item.id,
        account_id=item.account_id,
        principal_id=item.principal_id,
        prefix=item.prefix,
        label=item.label,
        status=item.status,
        created_at=item.created_at,
    )


def _rate(value: ExactRate) -> ExactRateResponse:
    return ExactRateResponse(
        numerator=str(value.numerator),
        denominator=str(value.denominator),
        charge_unit=value.charge_unit,
        quantity_unit=value.quantity_unit,
    )


def _grant(grant: Grant) -> GrantResponse:
    return GrantResponse(
        id=grant.id,
        account_id=grant.account_id,
        kind=grant.kind,
        amount=Money(amount=str(grant.amount), unit=grant.unit),
        reason=grant.reason,
        external_reference=grant.external_reference,
        actor_id=grant.actor_id,
        created_at=grant.created_at,
    )


@router.get(
    "/tenants",
    response_model=TenantPageResponse,
    responses=account_responses(400),
    tags=["tenants"],
    openapi_extra=COOKIE_SECURITY,
)
async def list_tenants(
    service: Service,
    actor: Actor,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TenantPageResponse:
    try:
        values = list(await service.list_tenants(actor, limit, cursor))
    except ValueError as error:
        raise AccountProblem(400, "Invalid request") from error
    visible, next_cursor = _page(list(values), limit)
    return TenantPageResponse(
        items=[_tenant(x) for x in visible], page=PageResponse(next_cursor=next_cursor)
    )


@router.post(
    "/tenants",
    status_code=status.HTTP_201_CREATED,
    response_model=TenantResponse,
    responses=account_responses(400, 403),
    tags=["tenants"],
    openapi_extra=COOKIE_SECURITY,
)
async def create_tenant(
    body: CreateTenant, service: Service, actor: ProtectedActor
) -> TenantResponse:
    try:
        return _tenant(await service.create_tenant(body.display_name, actor))
    except DomainError as error:
        _raise(error)


@router.get(
    "/tenants/{tenant_id}",
    response_model=TenantResponse,
    responses=account_responses(403, 404),
    tags=["tenants"],
    openapi_extra=COOKIE_SECURITY,
)
async def get_tenant(tenant_id: str, service: Service, actor: Actor) -> TenantResponse:
    try:
        return _tenant(await service.get_tenant(tenant_id, actor))
    except DomainError as error:
        _raise(error)


@router.patch(
    "/tenants/{tenant_id}",
    response_model=TenantResponse,
    responses=account_responses(400, 403, 404),
    tags=["tenants"],
    openapi_extra=COOKIE_SECURITY,
)
async def update_tenant(
    tenant_id: str, body: UpdateTenant, service: Service, actor: ProtectedActor
) -> TenantResponse:
    try:
        return _tenant(
            await service.update_tenant(
                tenant_id, body.display_name, body.status, body.reason, actor
            )
        )
    except DomainError as error:
        _raise(error)


@router.get(
    "/accounts",
    response_model=AccountPageResponse,
    responses=account_responses(400),
    tags=["accounts"],
    openapi_extra=COOKIE_SECURITY,
)
async def list_accounts(
    service: Service,
    actor: Actor,
    tenant_id: str | None = None,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AccountPageResponse:
    try:
        values = list(await service.list_accounts(tenant_id, actor, limit, cursor))
    except ValueError as error:
        raise AccountProblem(400, "Invalid request") from error
    visible, next_cursor = _page(list(values), limit)
    return AccountPageResponse(
        items=[_account(x) for x in visible], page=PageResponse(next_cursor=next_cursor)
    )


@router.post(
    "/accounts",
    status_code=status.HTTP_201_CREATED,
    response_model=AccountResponse,
    responses=account_responses(400, 403),
    tags=["accounts"],
    openapi_extra=COOKIE_SECURITY,
)
async def create_account(
    body: CreateAccount, service: Service, actor: ProtectedActor
) -> AccountResponse:
    try:
        return _account(
            await service.create_account(
                body.tenant_id, body.display_name, body.unit, int(body.exposure_cap), actor
            )
        )
    except DomainError as error:
        _raise(error)


@router.get(
    "/accounts/{account_id}",
    response_model=AccountResponse,
    responses=account_responses(404),
    tags=["accounts"],
    openapi_extra=COOKIE_SECURITY,
)
async def get_account(account_id: str, service: Service, actor: Actor) -> AccountResponse:
    try:
        return _account(await service.get_account(account_id, actor))
    except DomainError as error:
        _raise(error)


@router.patch(
    "/accounts/{account_id}",
    response_model=AccountResponse,
    responses=account_responses(400, 403, 404),
    tags=["accounts"],
    openapi_extra=COOKIE_SECURITY,
)
async def update_account(
    account_id: str, body: UpdateAccount, service: Service, actor: ProtectedActor
) -> AccountResponse:
    try:
        cap = int(body.exposure_cap) if body.exposure_cap is not None else None
        return _account(
            await service.update_account(
                account_id, body.display_name, cap, body.status, body.reason, actor
            )
        )
    except DomainError as error:
        _raise(error)


@router.get(
    "/principals",
    response_model=list[PrincipalResponse],
    responses=account_responses(),
    tags=["accounts"],
    openapi_extra=COOKIE_SECURITY,
)
async def list_principals(
    service: Service, actor: Actor, tenant_id: str | None = None
) -> list[PrincipalResponse]:
    return [_principal(x) for x in await service.list_principals(tenant_id, actor)]


@router.post(
    "/principals",
    status_code=status.HTTP_201_CREATED,
    response_model=PrincipalResponse,
    responses=account_responses(400, 403),
    tags=["accounts"],
    openapi_extra=COOKIE_SECURITY,
)
async def create_principal(
    body: CreatePrincipal, service: Service, actor: ProtectedActor
) -> PrincipalResponse:
    try:
        return _principal(
            await service.create_principal(
                body.tenant_id, body.account_id, body.display_name, body.roles, actor
            )
        )
    except DomainError as error:
        _raise(error)


@router.patch(
    "/principals/{principal_id}",
    response_model=PrincipalResponse,
    responses=account_responses(400, 403, 404),
    tags=["accounts"],
    openapi_extra=COOKIE_SECURITY,
)
async def update_principal(
    principal_id: str, body: UpdatePrincipal, service: Service, actor: ProtectedActor
) -> PrincipalResponse:
    try:
        return _principal(
            await service.update_principal(
                principal_id, body.status, body.roles, body.reason, actor
            )
        )
    except DomainError as error:
        _raise(error)


@router.get(
    "/credentials",
    response_model=list[CredentialResponse],
    responses=account_responses(),
    tags=["credentials"],
    openapi_extra=COOKIE_SECURITY,
)
async def list_credentials(service: Service, actor: Actor) -> list[CredentialResponse]:
    return [_credential(x) for x in await service.list_credentials(actor)]


@router.post(
    "/principals/{principal_id}/scope",
    response_model=PrincipalResponse,
    responses=account_responses(400, 403, 404, 409),
    tags=["accounts"],
    openapi_extra=COOKIE_SECURITY,
)
async def link_principal(
    principal_id: str,
    body: LinkPrincipal,
    service: Service,
    actor: ProtectedActor,
) -> PrincipalResponse:
    try:
        return _principal(
            await service.link_principal(
                principal_id,
                body.tenant_id,
                body.account_id,
                body.roles,
                body.reason,
                actor,
            )
        )
    except DomainError as error:
        _raise(error)


@router.post(
    "/credentials",
    status_code=status.HTTP_201_CREATED,
    response_model=IssuedCredentialResponse,
    responses=account_responses(400, 403, 404),
    tags=["credentials"],
    openapi_extra=COOKIE_SECURITY,
)
async def issue_credential(
    body: IssueCredential, service: Service, actor: ProtectedActor
) -> IssuedCredentialResponse:
    try:
        issued = await service.issue_credential(
            body.account_id, body.principal_id, body.label, body.expires_at, actor
        )
        return IssuedCredentialResponse(
            credential=_credential(issued.credential), secret=issued.secret
        )
    except DomainError as error:
        _raise(error)


@router.post(
    "/credentials/{credential_id}/rotate",
    response_model=IssuedCredentialResponse,
    responses=account_responses(403, 404),
    tags=["credentials"],
    openapi_extra=COOKIE_SECURITY,
)
async def rotate_credential(
    credential_id: str, service: Service, actor: ProtectedActor
) -> IssuedCredentialResponse:
    try:
        issued = await service.rotate_credential(credential_id, actor)
        return IssuedCredentialResponse(
            credential=_credential(issued.credential), secret=issued.secret
        )
    except DomainError as error:
        _raise(error)


@router.delete(
    "/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses=account_responses(403, 404),
    tags=["credentials"],
    openapi_extra=COOKIE_SECURITY,
)
async def revoke_credential(
    credential_id: str, service: Service, actor: ProtectedActor
) -> Response:
    try:
        await service.revoke_credential(credential_id, actor)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DomainError as error:
        _raise(error)


@router.post(
    "/grants",
    status_code=status.HTTP_201_CREATED,
    response_model=GrantResponse,
    responses=account_responses(400, 403, 404, 409),
    tags=["ledger"],
    openapi_extra=COOKIE_SECURITY,
)
async def create_grant(
    body: CreateGrant,
    service: Service,
    actor: ProtectedActor,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=256)],
) -> GrantResponse:
    try:
        grant = await service.create_grant(
            body.account_id,
            body.kind,
            int(body.amount.amount),
            body.amount.unit,
            body.reason,
            body.external_reference,
            idempotency_key,
            actor,
        )
        return _grant(grant)
    except DomainError as error:
        _raise(error)


@router.get(
    "/grants",
    response_model=GrantPageResponse,
    responses=account_responses(400, 403),
    tags=["ledger"],
    openapi_extra=COOKIE_SECURITY,
)
async def list_grants(
    service: Service,
    actor: Actor,
    account_id: str | None = None,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> GrantPageResponse:
    try:
        values = list(await service.list_grants(account_id, actor, limit, cursor))
        visible, next_cursor = _page(list(values), limit)
        return GrantPageResponse(
            items=[_grant(item) for item in visible],
            page=PageResponse(next_cursor=next_cursor),
        )
    except DomainError as error:
        _raise(error)
    except ValueError as error:
        raise AccountProblem(400, "Invalid request") from error


@router.get(
    "/balances/{account_id}",
    response_model=BalanceResponse,
    responses=account_responses(403, 404),
    tags=["ledger"],
    openapi_extra=COOKIE_SECURITY,
)
async def get_balance(account_id: str, service: Service, actor: Actor) -> BalanceResponse:
    try:
        balance = await service.balance(account_id, actor)
        return BalanceResponse(
            account_id=balance.account_id,
            posted=Money(amount=str(balance.posted), unit=balance.unit),
            open_lease_exposure=Money(amount=str(balance.open_lease_exposure), unit=balance.unit),
            available=Money(amount=str(balance.available), unit=balance.unit),
        )
    except DomainError as error:
        _raise(error)


@router.get(
    "/rate-cards",
    response_model=list[RateCardResponse],
    responses=account_responses(403),
    tags=["rates"],
    openapi_extra=COOKIE_SECURITY,
)
async def list_rate_cards(service: Service, actor: Actor) -> list[RateCardResponse]:
    try:
        cards = await service.list_rate_cards(actor)
    except DomainError as error:
        _raise(error)
    return [
        RateCardResponse(
            id=x.id,
            version=str(x.version),
            capability=x.capability,
            model=x.model,
            rate=_rate(x.rate),
            effective_at=x.effective_at,
            created_at=x.created_at,
        )
        for x in cards
    ]


@router.post(
    "/rate-cards",
    status_code=status.HTTP_201_CREATED,
    response_model=RateCardResponse,
    responses=account_responses(400, 403),
    tags=["rates"],
    openapi_extra=COOKIE_SECURITY,
)
async def create_rate_card(
    body: CreateRateCard, service: Service, actor: ProtectedActor
) -> RateCardResponse:
    try:
        rate = ExactRate(
            int(body.rate.numerator),
            int(body.rate.denominator),
            body.rate.charge_unit,
            body.rate.quantity_unit,
        )
        card = await service.create_rate_card(
            body.capability, body.model, rate, body.effective_at, actor
        )
        return RateCardResponse(
            id=card.id,
            version=str(card.version),
            capability=card.capability,
            model=card.model,
            rate=_rate(card.rate),
            effective_at=card.effective_at,
            created_at=card.created_at,
        )
    except (DomainError, ValueError) as error:
        _raise(InvalidRequest(str(error)))


@router.get(
    "/catalog",
    response_model=list[CatalogEntryResponse],
    responses=account_responses(403),
    tags=["catalog"],
    openapi_extra=COOKIE_SECURITY,
)
async def get_catalog(service: Service, actor: Actor) -> list[CatalogEntryResponse]:
    if actor.account_id is None:
        _raise(Forbidden("an account-scoped identity is required"))
    return [
        CatalogEntryResponse(
            capability=x.capability,
            model=x.model,
            rate=_rate(x.rate),
            available=x.available,
        )
        for x in await service.catalog(str(actor.account_id), actor)
    ]


@router.put(
    "/accounts/{account_id}/capabilities",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses=account_responses(400, 403, 404),
    tags=["accounts"],
    openapi_extra=COOKIE_SECURITY,
)
async def set_capability_policy(
    account_id: str,
    body: SetCapabilityPolicy,
    service: Service,
    actor: ProtectedActor,
) -> Response:
    try:
        await service.set_capability_policy(
            account_id, body.capability, body.model, body.allowed, body.reason, actor
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DomainError as error:
        _raise(error)
