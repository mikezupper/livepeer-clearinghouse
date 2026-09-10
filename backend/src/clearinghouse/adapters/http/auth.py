"""Authentication HTTP boundary."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from clearinghouse.application.authentication import AuthService
from clearinghouse.domain.auth import (
    AuthenticationUnavailable,
    AuthProvider,
    BrowserSession,
    CsrfRejected,
    InvalidAuthentication,
    Principal,
    ProviderDisabled,
    RateLimited,
)
from clearinghouse.domain.email import normalize_email
from clearinghouse.infrastructure.config import Settings

SESSION_COOKIE = "och_session"
CSRF_COOKIE = "och_csrf"
OAUTH_FLOW_COOKIE = "och_oauth_flow"


class EmailCodeRequest(BaseModel):
    """Decoded email challenge request."""

    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)


class EmailCodeVerification(EmailCodeRequest):
    """Decoded six-digit email challenge response."""

    code: str = Field(pattern=r"^[0-9]{6}$")


class AuthSessionResponse(BaseModel):
    """Public, non-secret session view."""

    model_config = ConfigDict(extra="forbid")
    principal_id: str
    tenant_id: str | None = None
    account_id: str | None = None
    roles: list[str]
    expires_at: datetime


class ProvidersResponse(BaseModel):
    """Enabled sign-in methods, never unconfigured providers."""

    model_config = ConfigDict(extra="forbid")
    providers: tuple[Literal["email", "google", "github"], ...]


class AuthProblemResponse(BaseModel):
    """Secret-free authentication problem detail."""

    model_config = ConfigDict(extra="forbid")
    type: str
    title: str
    status: int
    request_id: str


def _problem_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    schema = AuthProblemResponse.model_json_schema()
    return {
        code: {
            "description": "Authentication request failed",
            "content": {"application/problem+json": {"schema": schema}},
        }
        for code in codes
    }


@dataclass(frozen=True, slots=True)
class AuthenticatedRequest:
    """Principal and session identity injectable into protected routes."""

    principal: Principal
    session_id: str


def _problem(request: Request, status: int, title: str, kind: str) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or f"req_{secrets.token_urlsafe(12)}"
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": f"urn:livepeer:clearinghouse:{kind}",
            "title": title,
            "status": status,
            "request_id": request_id,
        },
    )


def _normalize_email(value: str) -> str:
    try:
        return normalize_email(value)
    except ValueError as error:
        raise InvalidAuthentication("invalid email address") from error


def _client_ip(request: Request) -> str:
    # Forwarded headers are intentionally ignored until a trusted-proxy boundary
    # is explicitly configured.
    return request.client.host if request.client is not None else "unknown"


def _session_response(session: BrowserSession) -> AuthSessionResponse:
    return AuthSessionResponse(
        principal_id=session.principal.id,
        tenant_id=session.principal.tenant_id,
        account_id=session.principal.account_id,
        roles=sorted(role.value for role in session.principal.roles),
        expires_at=session.expires_at,
    )


def _set_session_cookies(
    response: Response, session_token: str, csrf_token: str, settings: Settings
) -> None:
    max_age = settings.auth_session_ttl_seconds
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="strict",
        path="/",
    )


class AuthDependencies:
    """Reusable browser authentication and CSRF dependencies."""

    def __init__(self, service: AuthService, settings: Settings) -> None:
        self._service = service
        self._settings = settings

    async def current_session(
        self,
        och_session: Annotated[str | None, Cookie()] = None,
    ) -> BrowserSession:
        """Resolve a session or raise a stable unauthenticated failure."""
        try:
            return await self._service.authenticate(och_session)
        except InvalidAuthentication as error:
            raise HTTPException(status_code=401, detail="authentication required") from error

    async def current_principal(
        self,
        och_session: Annotated[str | None, Cookie()] = None,
    ) -> AuthenticatedRequest:
        """Expose the authenticated principal to other HTTP routers."""
        session = await self.current_session(och_session)
        return AuthenticatedRequest(session.principal, session.id)

    async def csrf_protected(
        self,
        request: Request,
        och_session: Annotated[str | None, Cookie()] = None,
        och_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> AuthenticatedRequest:
        """Authenticate and return one CSRF-protected actor dependency."""
        try:
            session = await self._service.authenticate(och_session)
            await self._service.validate_csrf(
                session,
                och_csrf,
                x_csrf_token,
                request.headers.get("origin", "").rstrip("/") or None,
                self._settings.auth_origins,
            )
        except InvalidAuthentication as error:
            raise HTTPException(status_code=401, detail="authentication required") from error
        except CsrfRejected as error:
            raise HTTPException(status_code=403, detail="csrf validation failed") from error
        return AuthenticatedRequest(session.principal, session.id)


def create_auth_router(
    service: AuthService, settings: Settings
) -> tuple[APIRouter, AuthDependencies]:
    """Build auth routes and dependencies from explicit capabilities."""
    router = APIRouter(prefix="/v1/auth", tags=["auth"])
    dependencies = AuthDependencies(service, settings)

    @router.get("/providers", response_model=ProvidersResponse)
    async def providers() -> ProvidersResponse:
        return ProvidersResponse(
            providers=cast(
                tuple[Literal["email", "google", "github"], ...], service.enabled_providers
            )
        )

    @router.post(
        "/email/code",
        status_code=202,
        response_class=Response,
        responses=_problem_responses(400, 429, 503),
    )
    async def request_code(payload: EmailCodeRequest, request: Request) -> Response:
        try:
            await service.request_email_code(_normalize_email(payload.email), _client_ip(request))
        except RateLimited as error:
            response = _problem(request, 429, "Authentication rate limited", "rate-limited")
            response.headers["Retry-After"] = str(error.retry_after_seconds)
            return response
        except InvalidAuthentication:
            return _problem(request, 400, "Invalid request", "invalid-request")
        except AuthenticationUnavailable:
            return _problem(request, 503, "Authentication unavailable", "unavailable")
        return Response(status_code=202)

    @router.post(
        "/email/verify",
        response_model=AuthSessionResponse,
        response_model_exclude_none=True,
        responses=_problem_responses(400, 429),
    )
    async def verify_code(
        payload: EmailCodeVerification, request: Request, response: Response
    ) -> AuthSessionResponse | Response:
        try:
            issued = await service.verify_email_code(
                _normalize_email(payload.email),
                payload.code,
                _client_ip(request),
                request.headers.get("user-agent", ""),
            )
        except RateLimited as error:
            limited = _problem(request, 429, "Authentication rate limited", "rate-limited")
            limited.headers["Retry-After"] = str(error.retry_after_seconds)
            return limited
        except InvalidAuthentication:
            return _problem(request, 400, "Invalid email code", "invalid-request")
        _set_session_cookies(response, issued.token, issued.csrf_token, settings)
        return _session_response(issued.session)

    @router.get(
        "/oauth/{provider}/start",
        status_code=302,
        response_class=RedirectResponse,
        responses=_problem_responses(404, 429),
    )
    async def start_oauth(provider: str, request: Request) -> Response:
        try:
            parsed = AuthProvider(provider)
            redirect_uri = _oauth_redirect_uri(parsed, settings)
            start = await service.start_oauth(parsed, redirect_uri, _client_ip(request))
        except ValueError, ProviderDisabled:
            return _problem(request, 404, "Authentication provider not found", "not-found")
        except RateLimited as error:
            limited = _problem(request, 429, "Authentication rate limited", "rate-limited")
            limited.headers["Retry-After"] = str(error.retry_after_seconds)
            return limited
        redirect = RedirectResponse(start.authorization_url, status_code=302)
        redirect.set_cookie(
            OAUTH_FLOW_COOKIE,
            start.flow_token,
            max_age=600,
            secure=settings.auth_cookie_secure,
            httponly=True,
            samesite="lax",
            path="/v1/auth/oauth",
        )
        return redirect

    @router.get(
        "/oauth/{provider}/callback",
        status_code=302,
        response_class=RedirectResponse,
        responses=_problem_responses(400, 429, 503),
    )
    async def oauth_callback(
        provider: str,
        request: Request,
        code: Annotated[str, Query(min_length=1, max_length=2048)],
        state: Annotated[str, Query(min_length=16, max_length=256)],
        och_oauth_flow: str | None = Cookie(default=None),
    ) -> Response:
        try:
            parsed = AuthProvider(provider)
            issued = await service.complete_oauth(
                parsed,
                state,
                och_oauth_flow,
                code,
                _client_ip(request),
                request.headers.get("user-agent", ""),
            )
        except ValueError, ProviderDisabled, InvalidAuthentication:
            invalid = _problem(request, 400, "Invalid OAuth response", "invalid-request")
            invalid.delete_cookie(OAUTH_FLOW_COOKIE, path="/v1/auth/oauth")
            return invalid
        except RateLimited as error:
            limited = _problem(request, 429, "Authentication rate limited", "rate-limited")
            limited.headers["Retry-After"] = str(error.retry_after_seconds)
            limited.delete_cookie(OAUTH_FLOW_COOKIE, path="/v1/auth/oauth")
            return limited
        except AuthenticationUnavailable:
            unavailable = _problem(request, 503, "Authentication unavailable", "unavailable")
            unavailable.delete_cookie(OAUTH_FLOW_COOKIE, path="/v1/auth/oauth")
            return unavailable
        redirect = RedirectResponse(settings.auth_success_redirect_url, status_code=302)
        redirect.delete_cookie(OAUTH_FLOW_COOKIE, path="/v1/auth/oauth")
        _set_session_cookies(redirect, issued.token, issued.csrf_token, settings)
        return redirect

    @router.get(
        "/session",
        response_model=AuthSessionResponse,
        response_model_exclude_none=True,
        responses=_problem_responses(401),
    )
    async def get_session(
        request: Request, och_session: str | None = Cookie(default=None)
    ) -> Response:
        try:
            session = await service.authenticate(och_session)
        except InvalidAuthentication:
            return _problem(request, 401, "Authentication required", "unauthorized")
        content = _session_response(session).model_dump(mode="json", exclude_none=True)
        return JSONResponse(content=content)

    @router.post(
        "/session/refresh",
        response_model=AuthSessionResponse,
        responses=_problem_responses(401, 403),
    )
    async def refresh_session(
        request: Request,
        och_session: str | None = Cookie(default=None),
        och_csrf: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> Response:
        try:
            session = await service.authenticate(och_session)
            await service.validate_csrf(
                session,
                och_csrf,
                x_csrf_token,
                request.headers.get("origin", "").rstrip("/") or None,
                settings.auth_origins,
            )
            issued = await service.renew_session(och_session)
        except InvalidAuthentication:
            return _problem(request, 401, "Authentication required", "unauthorized")
        except CsrfRejected:
            return _problem(request, 403, "CSRF validation failed", "forbidden")
        response = JSONResponse(
            content=_session_response(issued.session).model_dump(mode="json", exclude_none=True)
        )
        _set_session_cookies(response, issued.token, issued.csrf_token, settings)
        return response

    @router.delete(
        "/session",
        status_code=204,
        response_class=Response,
        responses=_problem_responses(401, 403),
    )
    async def delete_session(
        request: Request,
        och_session: str | None = Cookie(default=None),
        och_csrf: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> Response:
        try:
            session = await service.authenticate(och_session)
            await service.validate_csrf(
                session,
                och_csrf,
                x_csrf_token,
                request.headers.get("origin", "").rstrip("/") or None,
                settings.auth_origins,
            )
            await service.logout(session)
        except InvalidAuthentication:
            return _problem(request, 401, "Authentication required", "unauthorized")
        except CsrfRejected:
            return _problem(request, 403, "CSRF validation failed", "forbidden")
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    return router, dependencies


def _oauth_redirect_uri(provider: AuthProvider, settings: Settings) -> str:
    if provider is AuthProvider.GOOGLE and settings.auth_google_redirect_uri:
        return settings.auth_google_redirect_uri
    if provider is AuthProvider.GITHUB and settings.auth_github_redirect_uri:
        return settings.auth_github_redirect_uri
    raise ProviderDisabled(provider.value)
