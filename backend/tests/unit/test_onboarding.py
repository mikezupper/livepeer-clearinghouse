"""Identity onboarding application and HTTP boundary tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from clearinghouse.adapters.http.accounts import AccountProblem, account_problem_handler
from clearinghouse.adapters.http.onboarding import _raise, router
from clearinghouse.application.onboarding import OnboardingService
from clearinghouse.domain.accounts import AccountId, PrincipalContext, PrincipalId, Role, TenantId
from clearinghouse.domain.auth import AuthProvider
from clearinghouse.domain.onboarding import (
    IdentityInvitation,
    IdentityLink,
    InvalidInvitation,
    OnboardingConflict,
    OnboardingError,
    OnboardingForbidden,
    OnboardingNotFound,
    OperatorBootstrap,
)
from clearinghouse.infrastructure.config import Settings
from clearinghouse.infrastructure.onboarding import _translate_concurrency
from clearinghouse.main import create_app

NOW = datetime(2026, 9, 9, tzinfo=UTC)


class CapturingRepository:
    def __init__(self) -> None:
        self.created: dict[str, object] = {}
        self.redeemed: dict[str, object] = {}
        self.bootstrapped: dict[str, str] = {}
        self.error: Exception | None = None

    async def create_identity_invitation(
        self,
        *,
        invitation_id: str,
        source_principal_id: str,
        principal_id: str,
        secret_prefix: str,
        secret_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
        reason: str,
        actor: PrincipalContext,
    ) -> IdentityInvitation:
        if self.error:
            raise self.error
        self.created = dict(locals())
        return IdentityInvitation(
            invitation_id,
            source_principal_id,
            principal_id,
            "tenant_one",
            expires_at,
            created_at,
        )

    async def redeem_identity_invitation(
        self,
        *,
        secret_prefix: str,
        secret_hash: bytes,
        now: datetime,
        actor: PrincipalContext,
    ) -> IdentityLink:
        if self.error:
            raise self.error
        self.redeemed = dict(locals())
        return IdentityLink(
            "link_one",
            "identity_one",
            AuthProvider.GITHUB,
            "target_one",
            "tenant_one",
            now,
        )

    async def bootstrap_operator(
        self, *, normalized_email: str, configuration_hash: str
    ) -> OperatorBootstrap:
        if self.error:
            raise self.error
        self.bootstrapped = {
            "normalized_email": normalized_email,
            "configuration_hash": configuration_hash,
        }
        return OperatorBootstrap("operator_one", True)


def context(
    principal_id: str,
    *roles: Role,
    tenant_id: str | None = None,
    account_id: str | None = None,
) -> PrincipalContext:
    return PrincipalContext(
        PrincipalId(principal_id),
        frozenset(roles),
        TenantId(tenant_id) if tenant_id else None,
        AccountId(account_id) if account_id else None,
    )


@pytest.mark.asyncio
async def test_invitation_is_secret_backed_and_redeem_is_unscoped_only() -> None:
    repository = CapturingRepository()
    service = OnboardingService(repository, "a-secure-onboarding-pepper", clock=lambda: NOW)
    operator = context("operator_one", Role.OPERATOR)
    issued = await service.issue_invitation(
        "source_one", "target_one", "user requested access", operator
    )
    assert issued.secret.startswith("och_inv_")
    assert repository.created["source_principal_id"] == "source_one"
    assert repository.created["secret_hash"] != issued.secret
    assert repository.created["created_at"] == NOW

    actor = context("source_one")
    linked = await service.redeem_invitation(issued.secret, actor)
    assert linked.provider is AuthProvider.GITHUB
    assert repository.redeemed["secret_prefix"] == issued.secret[:20]
    assert repository.redeemed["secret_hash"] == repository.created["secret_hash"]

    for scoped in (
        context("source_one", Role.TENANT_ADMIN, tenant_id="tenant_one"),
        context("source_one", tenant_id="tenant_one"),
        context("source_one", account_id="account_one"),
    ):
        with pytest.raises(OnboardingForbidden):
            await service.redeem_invitation(issued.secret, scoped)


@pytest.mark.asyncio
async def test_invitation_and_bootstrap_fail_closed() -> None:
    repository = CapturingRepository()
    with pytest.raises(ValueError):
        OnboardingService(repository, "short")
    with pytest.raises(ValueError):
        OnboardingService(repository, "long-enough-pepper", invitation_ttl_seconds=10)
    service = OnboardingService(repository, "long-enough-pepper", clock=lambda: NOW)
    with pytest.raises(OnboardingForbidden):
        await service.issue_invitation("source", "target", "reason", context("ordinary"))
    with pytest.raises(ValueError):
        await service.bootstrap_operator("operator@example.test", "short")
    result = await service.bootstrap_operator("operator@example.test", "s" * 32)
    assert result.created
    assert repository.bootstrapped["normalized_email"] == "operator@example.test"
    assert repository.bootstrapped["configuration_hash"] != "s" * 32


def test_onboarding_http_contract_and_error_translation() -> None:
    repository = CapturingRepository()
    service = OnboardingService(repository, "long-enough-http-pepper", clock=lambda: NOW)
    actors = [context("operator_one", Role.OPERATOR)]
    app = FastAPI()
    app.state.onboarding_service = service
    app.add_exception_handler(AccountProblem, account_problem_handler)  # type: ignore[arg-type]

    @app.middleware("http")
    async def inject_actor(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = actors[0]
        return await call_next(request)

    app.include_router(router)
    client = TestClient(app)
    paths = app.openapi()["paths"]
    for path, method, success in (
        ("/v1/principals/{principal_id}/identity-invitations", "post", "201"),
        ("/v1/auth/identity-links", "post", "200"),
    ):
        operation = paths[path][method]
        assert operation["security"] == [{"cookieAuth": []}]
        assert "$ref" in operation["responses"][success]["content"]["application/json"]["schema"]
        assert {"400", "401", "403", "404", "409"} <= set(operation["responses"])
        for code in ("400", "401", "403", "404", "409"):
            assert set(operation["responses"][code]["content"]) == {"application/problem+json"}

    issued = client.post(
        "/v1/principals/target_12345678/identity-invitations",
        json={"source_principal_id": "source_12345678", "reason": "approved"},
    )
    assert issued.status_code == 201
    assert issued.json()["source_principal_id"] == "source_12345678"

    actors[0] = context("source_one")
    linked = client.post(
        "/v1/auth/identity-links",
        json={"invitation_secret": issued.json()["invitation_secret"]},
    )
    assert linked.status_code == 200 and linked.json()["provider"] == "github"
    assert any("och_session=" in value for value in linked.headers.get_list("set-cookie"))

    repository.error = OnboardingConflict("state changed")
    conflict = client.post(
        "/v1/auth/identity-links",
        json={"invitation_secret": issued.json()["invitation_secret"]},
    )
    assert conflict.status_code == 409
    for error, expected in (
        (OnboardingForbidden("denied"), 403),
        (OnboardingNotFound("hidden"), 404),
        (OnboardingConflict("changed"), 409),
        (InvalidInvitation("invalid"), 400),
        (OnboardingError("unexpected"), 500),
    ):
        with pytest.raises(AccountProblem) as problem:
            _raise(error)
        assert problem.value.status_code == expected


@pytest.mark.asyncio
async def test_database_concurrency_errors_are_stable() -> None:
    class OriginalError(Exception):
        def __init__(self, sqlstate: str) -> None:
            self.sqlstate = sqlstate

    deadlock = DBAPIError("statement", {}, OriginalError("40P01"), False)
    with pytest.raises(OnboardingConflict):
        async with _translate_concurrency():
            raise deadlock
    unrelated = DBAPIError("statement", {}, OriginalError("22000"), False)
    with pytest.raises(DBAPIError):
        async with _translate_concurrency():
            raise unrelated


def test_bootstrap_configuration_and_startup_are_fail_closed() -> None:
    for values in (
        {"operator_bootstrap_email": "operator@example.test"},
        {"operator_bootstrap_secret": "s" * 32},
        {"operator_bootstrap_email": "not-an-email", "operator_bootstrap_secret": "s" * 32},
        {"operator_bootstrap_email": "operator@example.test", "operator_bootstrap_secret": "x"},
    ):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **values)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-placeholder bootstrap"):
        Settings(
            environment="production",
            auth_pepper="a1" * 16,
            credential_pepper="b2" * 16,
            signer_webhook_secret="0123456789abcdefghijklmnopqrstuv",  # noqa: S106
            signer_session_pepper="zyxwvutsrqponmlkjihgfedcba987654",
            signer_url="https://signer.example.org",
            signer_discovery_url="https://signer.example.org",
            auth_resend_api_key="re_abcdefghijklmnopqrstuvwxyz",
            auth_resend_from="auth@livepeer.org",
            auth_allowed_origins="https://app.example.org",
            auth_success_redirect_url="https://app.example.org/",
            operator_bootstrap_email="operator@livepeer.org",
            operator_bootstrap_secret="replace-this-bootstrap-secret-value",  # noqa: S106
            _env_file=None,
        )

    settings = Settings(
        environment="test",
        operator_bootstrap_email="Operator@Livepeer.Org",
        operator_bootstrap_secret="safe-random-operator-seed-value-123456",  # noqa: S106
        _env_file=None,
    )
    onboarding = AsyncMock(spec=OnboardingService)
    onboarding.bootstrap_operator.return_value = OperatorBootstrap("operator_12345678", True)
    store = AsyncMock()
    app = create_app(
        settings,
        store=store,
        onboarding_service=cast(OnboardingService, onboarding),
    )
    with TestClient(app):
        pass
    onboarding.bootstrap_operator.assert_awaited_once_with(
        "operator@livepeer.org", "safe-random-operator-seed-value-123456"
    )
