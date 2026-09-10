"""HTTP boundary for explicit verified-identity onboarding."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Never

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from clearinghouse.adapters.http.accounts import (
    COOKIE_SECURITY,
    AccountProblem,
    ProtectedActor,
    account_responses,
)
from clearinghouse.adapters.http.auth import CSRF_COOKIE, SESSION_COOKIE
from clearinghouse.application.onboarding import OnboardingService
from clearinghouse.domain.auth import AuthProvider
from clearinghouse.domain.onboarding import (
    IdentityInvitation,
    IdentityLink,
    InvalidInvitation,
    OnboardingConflict,
    OnboardingError,
    OnboardingForbidden,
    OnboardingNotFound,
)

router = APIRouter(prefix="/v1")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateIdentityInvitation(ApiModel):
    source_principal_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}_[A-Za-z0-9_-]{8,128}$")
    reason: str = Field(min_length=1, max_length=1000)


class RedeemIdentityInvitation(ApiModel):
    invitation_secret: str = Field(
        min_length=50, max_length=128, pattern=r"^och_inv_[A-Za-z0-9_-]+$"
    )


class IdentityLinkResponse(ApiModel):
    id: str
    provider: AuthProvider
    principal_id: str
    tenant_id: str
    linked_at: datetime


class IdentityInvitationResponse(ApiModel):
    id: str
    source_principal_id: str
    principal_id: str
    tenant_id: str
    expires_at: datetime
    created_at: datetime
    invitation_secret: str


def service_from_request(request: Request) -> OnboardingService:
    service: OnboardingService = request.app.state.onboarding_service
    return service


Service = Annotated[OnboardingService, Depends(service_from_request)]


def _raise(error: OnboardingError) -> Never:
    code = status.HTTP_400_BAD_REQUEST
    if isinstance(error, OnboardingForbidden):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, OnboardingNotFound):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, OnboardingConflict):
        code = status.HTTP_409_CONFLICT
    elif not isinstance(error, InvalidInvitation):
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
    raise AccountProblem(code, str(error)) from error


def _invitation(value: IdentityInvitation, secret: str) -> IdentityInvitationResponse:
    return IdentityInvitationResponse(
        id=value.id,
        source_principal_id=value.source_principal_id,
        principal_id=value.principal_id,
        tenant_id=value.tenant_id,
        expires_at=value.expires_at,
        created_at=value.created_at,
        invitation_secret=secret,
    )


def _link(value: IdentityLink) -> IdentityLinkResponse:
    return IdentityLinkResponse(
        id=value.id,
        provider=value.provider,
        principal_id=value.principal_id,
        tenant_id=value.tenant_id,
        linked_at=value.linked_at,
    )


@router.post(
    "/principals/{principal_id}/identity-invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=IdentityInvitationResponse,
    responses=account_responses(400, 403, 404, 409),
    tags=["auth"],
    openapi_extra=COOKIE_SECURITY,
)
async def create_identity_invitation(
    principal_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,31}_[A-Za-z0-9_-]{8,128}$")],
    body: CreateIdentityInvitation,
    service: Service,
    actor: ProtectedActor,
) -> IdentityInvitationResponse:
    try:
        issued = await service.issue_invitation(
            body.source_principal_id, principal_id, body.reason, actor
        )
        return _invitation(issued.invitation, issued.secret)
    except OnboardingError as error:
        _raise(error)


@router.post(
    "/auth/identity-links",
    response_model=IdentityLinkResponse,
    responses=account_responses(400, 403, 404, 409),
    tags=["auth"],
    openapi_extra=COOKIE_SECURITY,
)
async def redeem_identity_invitation(
    body: RedeemIdentityInvitation,
    service: Service,
    actor: ProtectedActor,
) -> Response:
    try:
        result = await service.redeem_invitation(body.invitation_secret, actor)
    except OnboardingError as error:
        _raise(error)
    response = Response(
        content=_link(result).model_dump_json(),
        media_type="application/json",
    )
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response
