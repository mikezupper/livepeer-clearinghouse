"""HTTP boundary for direct user/account access."""

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from clearinghouse.application.access import AccessDenied, AccessService
from clearinghouse.application.core_store import AccessIdentity, StoredCredential
from clearinghouse.application.pagination import CursorCodec, InvalidCursor
from clearinghouse.domain.core import CredentialId
from clearinghouse.infrastructure.simple_config import CoreSettings

SESSION_COOKIE = "och_session"
CSRF_COOKIE = "och_csrf"
OAUTH_COOKIE = "och_oauth_flow"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailRequest(Model):
    email: str = Field(min_length=3, max_length=320)


class EmailVerification(EmailRequest):
    code: str = Field(pattern=r"^[0-9]{6}$")


class CredentialRequest(Model):
    name: str = Field(min_length=1, max_length=80)


@dataclass(frozen=True, slots=True)
class Actor:
    identity: AccessIdentity
    credential: StoredCredential | None = None


def _session_body(identity: AccessIdentity, expires_at: datetime) -> dict[str, object]:
    return {
        "user_id": identity.user_id,
        "account_id": identity.account_id,
        "email": identity.email,
        "is_admin": identity.is_admin,
        "expires_at": expires_at.isoformat(),
    }


def _set_cookies(response: Response, token: str, csrf: str, settings: CoreSettings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def create_access_router(service: AccessService, settings: CoreSettings) -> APIRouter:
    router = APIRouter(prefix="/v1")
    cursors = CursorCodec(settings.auth_pepper.get_secret_value())

    async def actor(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        och_session: Annotated[str | None, Cookie()] = None,
    ) -> Actor:
        if authorization and authorization.startswith("Bearer och_live_"):
            credential = await service.authenticate_credential(authorization[7:])
            return Actor(
                AccessIdentity(credential.user_id, credential.account_id, "sdk-credential", False),
                credential,
            )
        try:
            session = await service.authenticate_session(och_session)
        except AccessDenied as error:
            raise HTTPException(401, "authentication required") from error
        return Actor(session.identity)

    async def mutation_actor(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        och_session: Annotated[str | None, Cookie()] = None,
        och_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> Actor:
        value = await actor(request, authorization, och_session)
        if value.credential is not None:
            return value
        origin = request.headers.get("origin", "").rstrip("/")
        if (
            origin not in settings.origin_values
            or not och_csrf
            or not x_csrf_token
            or not hmac.compare_digest(och_csrf, x_csrf_token)
        ):
            raise HTTPException(403, "CSRF validation failed")
        session = await service.authenticate_session(och_session)
        if not hmac.compare_digest(session.csrf_digest, service.digest("csrf", och_csrf)):
            raise HTTPException(403, "CSRF validation failed")
        return value

    @router.get("/auth/providers")
    async def providers() -> dict[str, object]:
        return {"providers": service.providers}

    @router.post("/auth/email/code", status_code=202)
    async def request_code(body: EmailRequest) -> Response:
        try:
            await service.request_code(body.email)
        except ValueError as error:
            raise HTTPException(400, "invalid email address") from error
        return Response(status_code=202)

    @router.post("/auth/email/verify")
    async def verify_code(body: EmailVerification) -> Response:
        try:
            issued = await service.verify_code(body.email, body.code)
        except (AccessDenied, ValueError) as error:
            raise HTTPException(400, "invalid email code") from error
        response = JSONResponse(_session_body(issued.session.identity, issued.session.expires_at))
        _set_cookies(response, issued.token, issued.csrf_token, settings)
        return response

    @router.get("/auth/oauth/{provider}/start")
    async def oauth_start(provider: str) -> Response:
        redirect_uri = f"{settings.public_url.rstrip('/')}/v1/auth/oauth/{provider}/callback"
        try:
            started = await service.start_oauth(provider, redirect_uri)
        except (AccessDenied, ValueError) as error:
            raise HTTPException(404, "authentication provider not found") from error
        response = RedirectResponse(started.authorization_url, 302)
        response.set_cookie(
            OAUTH_COOKIE,
            started.state,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/v1/auth/oauth",
        )
        return response

    @router.get("/auth/oauth/{provider}/callback")
    async def oauth_callback(
        provider: str,
        code: str,
        state: str,
        och_oauth_flow: Annotated[str | None, Cookie()] = None,
    ) -> Response:
        try:
            issued = await service.complete_oauth(provider, state, och_oauth_flow, code)
        except (AccessDenied, ValueError) as error:
            raise HTTPException(400, "invalid OAuth response") from error
        response = RedirectResponse(settings.public_url, 302)
        response.delete_cookie(OAUTH_COOKIE, path="/v1/auth/oauth")
        _set_cookies(response, issued.token, issued.csrf_token, settings)
        return response

    @router.get("/auth/session")
    async def get_session(
        och_session: Annotated[str | None, Cookie()] = None,
    ) -> dict[str, object]:
        try:
            session = await service.authenticate_session(och_session)
        except AccessDenied as error:
            raise HTTPException(401, "authentication required") from error
        return _session_body(session.identity, session.expires_at)

    @router.delete("/auth/session", status_code=204)
    async def logout(
        value: Annotated[Actor, Depends(mutation_actor)],
        och_session: Annotated[str | None, Cookie()] = None,
    ) -> Response:
        del value
        if och_session:
            await service.logout(och_session)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    @router.get("/credentials")
    async def list_credentials(
        value: Annotated[Actor, Depends(actor)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        filters: dict[str, str | None] = {}
        try:
            decoded = cursors.decode(
                cursor,
                collection="credentials",
                scope=str(value.identity.account_id),
                filters=filters,
            )
            credentials = await service.list_credentials(
                value.identity, limit=limit, after=decoded.key
            )
        except (InvalidCursor, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        next_cursor = (
            cursors.encode(
                collection="credentials",
                scope=str(value.identity.account_id),
                filters=filters,
                key=credentials.next_key,
            )
            if credentials.next_key
            else None
        )
        return {
            "items": [
                {
                    "id": credential.id,
                    "name": credential.name,
                    "created_at": credential.created_at.isoformat(),
                    "revoked_at": credential.revoked_at.isoformat()
                    if credential.revoked_at
                    else None,
                }
                for credential in credentials.items
            ],
            "next_cursor": next_cursor,
        }

    @router.post("/credentials", status_code=201)
    async def create_credential(
        body: CredentialRequest, value: Annotated[Actor, Depends(mutation_actor)]
    ) -> Response:
        issued = await service.create_credential(value.identity, body.name)
        return JSONResponse(
            status_code=201,
            headers={"Cache-Control": "no-store"},
            content={
                "id": issued.credential.id,
                "name": issued.credential.name,
                "token": issued.token,
                "created_at": issued.credential.created_at.isoformat(),
            },
        )

    @router.delete("/credentials/{credential_id}", status_code=204)
    async def revoke_credential(
        credential_id: str, value: Annotated[Actor, Depends(mutation_actor)]
    ) -> Response:
        try:
            await service.revoke_credential(value.identity, CredentialId(credential_id))
        except AccessDenied as error:
            raise HTTPException(404, "credential not found") from error
        return Response(status_code=204)

    router.actor_dependency = actor  # type: ignore[attr-defined]
    router.mutation_actor_dependency = mutation_actor  # type: ignore[attr-defined]
    return router
