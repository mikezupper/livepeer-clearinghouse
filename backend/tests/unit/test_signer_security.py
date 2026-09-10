"""Security, authorization, and failure-branch tests for signer HTTP boundaries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from clearinghouse.adapters.http.signer import SessionActor, SignerProblem, session_actor
from clearinghouse.application.signer import SignerConflict, SignerService
from clearinghouse.domain.accounts import AccountId, PrincipalContext, PrincipalId, Role, TenantId
from clearinghouse.domain.auth import Principal as AuthPrincipal
from clearinghouse.domain.auth import Role as AuthRole
from clearinghouse.domain.signer import Admission, DenialReason, Lease, PaymentState, SignerSession
from clearinghouse.infrastructure.config import Settings
from clearinghouse.infrastructure.telemetry import Outcome, Reason, Telemetry, set_telemetry
from clearinghouse.main import create_app

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
LAST_UPDATE = "2026-09-09T12:00:00.123456789Z"
ORCHESTRATOR_ADDRESS = "0x" + "a" * 40
HOLDER = PrincipalContext(
    PrincipalId("principal_holder1"),
    frozenset({Role.CREDENTIAL_HOLDER}),
    TenantId("tenant_holder12"),
    AccountId("account_holder1"),
)
OPERATOR = PrincipalContext(PrincipalId("principal_operator1"), frozenset({Role.OPERATOR}))
STATE = {
    "StateID": "state_security1",
    "PMSessionID": "pm_security1234",
    "LastUpdate": LAST_UPDATE,
    "OrchestratorAddress": ORCHESTRATOR_ADDRESS,
    "App": "preflight",
    "AuthExpiry": 0,
    "SenderNonce": 1,
    "Balance": "0",
    "InitialPricePerUnit": 3,
    "InitialPixelsPerUnit": 2,
    "Type": "live",
    "SequenceNumber": 0,
    "AuthID": "",
}


def _lease() -> Lease:
    return Lease("lease_security1", 100, 90, 10, 0, "wei", NOW + timedelta(hours=1))


class RecordingRepository:
    signer_id = "signer_security1"

    def __init__(self) -> None:
        self.created: dict[str, Any] = {}
        self.refreshed: dict[str, Any] = {}
        self.authorized: dict[str, Any] = {}
        self.admission = Admission(
            True,
            session_id="session_security1",
            tenant_id=str(HOLDER.tenant_id),
            account_id=str(HOLDER.account_id),
            principal_id=str(HOLDER.id),
            lease=_lease(),
            rate_numerator=3,
            rate_denominator=1,
            quantity_unit="seconds",
        )
        self.failure: Exception | None = None
        self.revoked = True
        self.killed = False

    async def initialize(self) -> None:
        return None

    async def create_session(self, **values: Any) -> SignerSession:
        self.created = values
        if self.failure is not None:
            raise self.failure
        return self._session("session_security1")

    async def authorize(self, **values: Any) -> Admission:
        self.authorized = values
        if self.failure is not None:
            raise self.failure
        return self.admission

    async def refresh_session(self, **values: Any) -> SignerSession:
        self.refreshed = values
        if self.failure is not None:
            raise self.failure
        return self._session("session_replaced1")

    async def list_sessions(self, _principal: PrincipalContext) -> Sequence[SignerSession]:
        return (self._session("session_security1"),)

    async def list_leases(self, principal: PrincipalContext) -> Sequence[SignerSession]:
        return await self.list_sessions(principal)

    async def revoke_session(
        self, _principal: PrincipalContext, _session_id: str, _now: datetime
    ) -> bool:
        return self.revoked

    async def set_kill_switch(
        self, enabled: bool, reason: str, actor: PrincipalContext, now: datetime
    ) -> dict[str, object]:
        self.killed = enabled
        return {"enabled": enabled, "reason": reason, "actor_id": actor.id, "changed_at": now}

    async def get_kill_switch(self) -> dict[str, object]:
        return {
            "enabled": self.killed,
            "reason": "security test",
            "actor_id": OPERATOR.id,
            "changed_at": NOW,
        }

    @staticmethod
    def _session(session_id: str) -> SignerSession:
        return SignerSession(
            session_id,
            "",
            "https://signer.test",
            "https://discovery.test",
            _lease().expires_at,
            _lease(),
        )


def _service(repository: RecordingRepository | None = None) -> SignerService:
    return SignerService(
        repository or RecordingRepository(),
        "security-test-pepper-value",
        "security-webhook-secret",
        clock=lambda: NOW,
        token_factory=lambda: "security-session-token-value",
    )


def _client(
    repository: RecordingRepository,
    actor: PrincipalContext = HOLDER,
    *,
    credential_id: str | None = "cred_security12",
) -> TestClient:
    app = create_app(
        Settings(environment="test", _env_file=None),
        store=None,
        auth_service=None,
        account_service=None,
        onboarding_service=None,
        signer_service=_service(repository),
    )
    app.dependency_overrides[session_actor] = lambda: SessionActor(actor, credential_id)
    return TestClient(app)


def _payment() -> PaymentState:
    return PaymentState(
        "state_security1",
        "pm_security1234",
        LAST_UPDATE,
        ORCHESTRATOR_ADDRESS,
        "preflight",
        0,
        1,
        "0",
        3,
        2,
        "live",
        0,
        "",
    )


def test_signer_openapi_binds_secret_and_nonsecret_response_models() -> None:
    with _client(RecordingRepository()) as client:
        schema = client.app.openapi()  # type: ignore[attr-defined]
    create = schema["paths"]["/v1/sessions"]["post"]
    assert create["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/CreatedSessionResponse"
    )
    assert create["security"] == [{"bearerAuth": []}, {"cookieAuth": []}]
    listed = schema["paths"]["/v1/sessions"]["get"]
    assert listed["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/SessionListResponse"
    )
    metadata = schema["components"]["schemas"]["SessionMetadataResponse"]
    assert "token" not in metadata["properties"]
    authorize = schema["paths"]["/v1/authorize"]["post"]
    assert authorize["security"] == [{"signerSecret": []}]
    assert set(authorize["responses"]) == {"200", "400", "401", "402", "503"}
    assert authorize["responses"]["402"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/AuthorizeDenyResponse"
    )


@pytest.mark.asyncio
async def test_service_validates_scope_role_inputs_and_idempotency() -> None:
    with pytest.raises(ValueError, match="pepper"):
        SignerService(RecordingRepository(), "short", "webhook")
    signer = _service()
    unscoped = PrincipalContext(HOLDER.id, HOLDER.roles)
    no_role = PrincipalContext(HOLDER.id, frozenset(), HOLDER.tenant_id, HOLDER.account_id)
    for principal, error in ((unscoped, ValueError), (no_role, PermissionError)):
        with pytest.raises(error):
            await signer.create_session(
                principal, "live", None, "app", 1, "wei", 60, "operation-key-valid"
            )
    for cap, unit, key in (
        (0, "wei", "operation-key-valid"),
        (1, "usd", "operation-key-valid"),
        (1, "wei", "short"),
    ):
        with pytest.raises(ValueError):
            await signer.create_session(HOLDER, "live", None, "app", cap, unit, 60, key)
    with pytest.raises(PermissionError):
        await signer.refresh_session(no_role, "session_security1", None, "operation-key-valid")
    with pytest.raises(ValueError, match="Idempotency-Key"):
        await signer.refresh_session(HOLDER, "session_security1", None, "short")


@pytest.mark.asyncio
async def test_service_hashes_secrets_and_preserves_refresh_source() -> None:
    repository = RecordingRepository()
    signer = _service(repository)
    created = await signer.create_session(
        HOLDER,
        "live",
        "model-a",
        "app-a",
        9,
        "wei",
        61,
        "operation-create-0001",
        "cred_security12",
    )
    assert created.token == "och_ss_security-session-token-value"  # noqa: S105
    assert repository.created["credential_id"] == "cred_security12"
    assert repository.created["operation_key"] == "operation-create-0001"
    assert len(repository.created["request_hash"]) == 64
    assert repository.created["token_hash"] != created.token
    await signer.refresh_session(
        HOLDER,
        "session_security1",
        5,
        "operation-refresh-0001",
        "cred_security12",
    )
    assert repository.refreshed["credential_id"] == "cred_security12"
    assert repository.refreshed["operation_key"] == "operation-refresh-0001"
    assert repository.refreshed["token_hash"] == signer.digest(
        "session", "och_ss_security-session-token-value"
    )
    await signer.authorize("raw-bearer", repository.signer_id, _payment())
    assert repository.authorized["bearer_hash"] != b"raw-bearer"
    assert repository.authorized["state"] == _payment()


class FakeAuth:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.principal = AuthPrincipal(
            str(HOLDER.id),
            str(HOLDER.tenant_id),
            str(HOLDER.account_id),
            frozenset({AuthRole.CREDENTIAL_HOLDER}),
        )

    async def current_principal(self, token: str | None) -> SimpleNamespace:
        self.calls.append(("current", (token,)))
        return SimpleNamespace(principal=self.principal)

    async def csrf_protected(self, request: Request, *values: Any) -> SimpleNamespace:
        self.calls.append(("csrf", (request.method, *values)))
        return SimpleNamespace(principal=self.principal)


class FakeAccounts:
    def __init__(self, match: tuple[PrincipalContext, str] | None) -> None:
        self.match = match
        self.secret: str | None = None

    async def authenticate_credential_context(
        self, secret: str
    ) -> tuple[PrincipalContext, str] | None:
        self.secret = secret
        return self.match


def _request(method: str, app: FastAPI) -> Request:
    return Request({"type": "http", "method": method, "path": "/", "headers": [], "app": app})


@pytest.mark.asyncio
async def test_session_actor_separates_cookie_csrf_and_machine_credentials() -> None:
    app = FastAPI()
    auth = FakeAuth()
    app.state.auth = auth
    read = await session_actor(_request("GET", app), och_session="browser-token")
    assert read.principal == HOLDER
    assert auth.calls == [("current", ("browser-token",))]
    written = await session_actor(
        _request("POST", app),
        och_session="browser-token",
        och_csrf="csrf-cookie",
        x_csrf_token="csrf-header",  # noqa: S106 -- intentional CSRF fixture
    )
    assert written.principal == HOLDER
    assert auth.calls[-1][0] == "csrf"

    accounts = FakeAccounts((HOLDER, "cred_security12"))
    app.state.account_service = accounts
    prior_auth_calls = len(auth.calls)
    machine = await session_actor(
        _request("POST", app), authorization="Bearer och_live_machine-secret"
    )
    assert machine == SessionActor(HOLDER, "cred_security12")
    assert accounts.secret == "och_live_machine-secret"  # noqa: S105 -- credential fixture
    assert len(auth.calls) == prior_auth_calls

    injected_request = _request("POST", app)
    injected_request.state.principal = OPERATOR
    assert await session_actor(injected_request) == SessionActor(OPERATOR)


@pytest.mark.asyncio
async def test_session_actor_fails_closed_without_valid_authentication() -> None:
    app = FastAPI()
    with pytest.raises(SignerProblem) as missing:
        await session_actor(_request("GET", app))
    assert missing.value.status_code == 401
    app.state.account_service = FakeAccounts(None)
    with pytest.raises(SignerProblem) as invalid:
        await session_actor(_request("POST", app), authorization="Bearer och_live_invalid")
    assert invalid.value.status_code == 401


def test_session_secret_responses_and_error_adapters() -> None:
    repository = RecordingRepository()
    with _client(repository) as client:
        create = client.post(
            "/v1/sessions",
            headers={"Idempotency-Key": "http-create-operation-1"},
            json={"capability": "live", "app": "preflight", "requested_cap": "10", "unit": "wei"},
        )
        assert create.status_code == 201
        assert create.headers["cache-control"] == "no-store"
        assert create.headers["pragma"] == "no-cache"
        assert repository.created["credential_id"] == "cred_security12"
        refresh = client.post(
            "/v1/sessions/session_security1/refresh",
            headers={"Idempotency-Key": "http-refresh-operation1"},
            json={"requested_cap": "5"},
        )
        assert refresh.status_code == 201
        assert refresh.headers["cache-control"] == "no-store"
        assert repository.refreshed["credential_id"] == "cred_security12"

        for failure, expected in (
            (SignerConflict("already created"), 409),
            (PermissionError("denied"), 403),
            (ValueError("insufficient"), 402),
            (SQLAlchemyError("database"), 503),
        ):
            repository.failure = failure
            response = client.post(
                "/v1/sessions",
                headers={"Idempotency-Key": "http-create-operation-2"},
                json={
                    "capability": "live",
                    "app": "preflight",
                    "requested_cap": "10",
                    "unit": "wei",
                },
            )
            assert response.status_code == expected
            assert response.headers["content-type"].startswith("application/problem+json")


def test_native_authorize_denials_secret_and_storage_failure() -> None:
    repository = RecordingRepository()
    with _client(repository) as client:
        body = {"bearer": "b" * 32, "state": STATE}
        assert client.post("/v1/authorize", json=body).status_code == 401
        assert (
            client.post(
                "/v1/authorize", headers={"Authorization": "Bearer wrong"}, json=body
            ).status_code
            == 401
        )
        repository.admission = Admission(False, DenialReason.LEASE_EXHAUSTED)
        denied = client.post(
            "/v1/authorize",
            headers={"Authorization": "Bearer security-webhook-secret"},
            json=body,
        )
        assert denied.status_code == 402
        assert denied.json() == {"decision": "deny", "reason": "lease_exhausted"}
        repository.admission = Admission(False, DenialReason.LEDGER_UNAVAILABLE)
        unavailable = client.post(
            "/v1/authorize",
            headers={"Authorization": "Bearer security-webhook-secret"},
            json=body,
        )
        assert unavailable.status_code == 503
        assert unavailable.headers["content-type"].startswith("application/problem+json")
        repository.failure = SQLAlchemyError("database")
        failed = client.post(
            "/v1/authorize",
            headers={"Authorization": "Bearer security-webhook-secret"},
            json=body,
        )
        assert failed.status_code == 503
        assert "database" not in failed.text


@pytest.mark.parametrize(
    ("reason", "nested_status"),
    [
        (DenialReason.UNKNOWN_CREDENTIAL, 401),
        (DenialReason.LEASE_EXHAUSTED, 402),
        (DenialReason.LEDGER_UNAVAILABLE, 503),
    ],
)
def test_compat_denial_mapping_and_forwarded_authorization_ambiguity(
    reason: DenialReason, nested_status: int
) -> None:
    repository = RecordingRepository()
    repository.admission = Admission(False, reason)
    with _client(repository) as client:
        response = client.post(
            "/v1/compat/go-livepeer/authorize",
            headers={"Authorization": "Bearer security-webhook-secret"},
            json={"headers": {"Authorization": ["Bearer och_ss_valid"]}, "state": STATE},
        )
        assert response.status_code == 200
        assert response.json() == {"status": nested_status, "reason": reason, "expiry": 0}
        duplicate = client.post(
            "/v1/compat/go-livepeer/authorize",
            headers={"Authorization": "Bearer security-webhook-secret"},
            json={
                "headers": {
                    "Authorization": ["Bearer first"],
                    "authorization": ["Bearer second"],
                },
                "state": STATE,
            },
        )
        assert duplicate.status_code == 200
        assert repository.authorized["bearer_hash"] == _service(repository).digest("session", "")


def test_compat_success_storage_failure_and_input_bounds() -> None:
    repository = RecordingRepository()
    with _client(repository) as client:
        headers = {"Authorization": "Bearer security-webhook-secret"}
        allowed = client.post(
            "/v1/compat/go-livepeer/authorize",
            headers=headers,
            json={"headers": {"Authorization": ["Bearer och_ss_valid"]}, "state": STATE},
        )
        assert allowed.status_code == 200
        assert allowed.json()["maxPrice"] == {"price": "3", "currency": "wei", "unit": "seconds"}
        repository.admission = Admission(
            True,
            session_id="session_security1",
            lease=_lease(),
            rate_numerator=3,
            rate_denominator=2,
            quantity_unit="seconds",
        )
        fractional = client.post(
            "/v1/compat/go-livepeer/authorize",
            headers=headers,
            json={"headers": {}, "state": STATE},
        )
        assert "maxPrice" not in fractional.json()
        repository.failure = SQLAlchemyError("database-private-detail")
        unavailable = client.post(
            "/v1/compat/go-livepeer/authorize",
            headers=headers,
            json={"headers": {}, "state": STATE},
        )
        assert unavailable.json() == {"status": 503, "reason": "ledger_unavailable", "expiry": 0}
        assert "private-detail" not in unavailable.text

        repository.failure = None
        invalid_inputs = [
            {"headers": {"": []}, "state": STATE},
            {"headers": {"x": ["v"] * 9}, "state": STATE},
            {"headers": {"x": ["v" * 1025]}, "state": STATE},
            {"headers": {f"x-{index}": ["v" * 128] for index in range(64)}, "state": STATE},
        ]
        for body in invalid_inputs:
            assert (
                client.post(
                    "/v1/compat/go-livepeer/authorize", headers=headers, json=body
                ).status_code
                == 400
            )
        assert (
            client.post(
                "/v1/compat/go-livepeer/authorize",
                headers=headers,
                json={"headers": {}, "state": STATE | {"SequenceNumber": 2**64}},
            ).status_code
            == 400
        )


def test_compat_emits_nested_signer_decisions_instead_of_outer_http_success() -> None:
    class RecordingTelemetry:
        def __init__(self) -> None:
            self.calls: list[tuple[Outcome, Reason]] = []

        def record_signer(self, outcome: Outcome, reason: Reason = Reason.NONE) -> None:
            self.calls.append((outcome, reason))

    repository = RecordingRepository()
    telemetry = RecordingTelemetry()
    with _client(repository) as client:
        set_telemetry(cast(Any, telemetry))
        headers = {"Authorization": "Bearer security-webhook-secret"}
        body = {"headers": {"Authorization": ["Bearer och_ss_valid"]}, "state": STATE}
        assert (
            client.post("/v1/compat/go-livepeer/authorize", headers=headers, json=body).json()[
                "status"
            ]
            == 200
        )
        repository.admission = Admission(False, DenialReason.KILL_SWITCH_ACTIVE)
        assert (
            client.post("/v1/compat/go-livepeer/authorize", headers=headers, json=body).json()[
                "status"
            ]
            == 402
        )
        repository.failure = SQLAlchemyError("private database detail")
        assert (
            client.post("/v1/compat/go-livepeer/authorize", headers=headers, json=body).json()[
                "status"
            ]
            == 503
        )
    set_telemetry(Telemetry())
    assert telemetry.calls == [
        (Outcome.SUCCESS, Reason.NONE),
        (Outcome.DENIED, Reason.KILL_SWITCH),
        (Outcome.UNAVAILABLE, Reason.LEDGER_UNAVAILABLE),
    ]


@pytest.mark.asyncio
async def test_service_list_revoke_and_kill_switch_authorization() -> None:
    repository = RecordingRepository()
    signer = _service(repository)
    assert len(await signer.list_sessions(HOLDER)) == 1
    assert len(await signer.list_leases(HOLDER)) == 1
    await signer.revoke_session(HOLDER, "session_security1")
    repository.revoked = False
    with pytest.raises(ValueError, match="not found"):
        await signer.revoke_session(HOLDER, "session_missing1")
    with pytest.raises(PermissionError):
        await signer.get_kill_switch(HOLDER)
    with pytest.raises(PermissionError):
        await signer.set_kill_switch(True, "reason", HOLDER)
    changed = await signer.set_kill_switch(True, "incident", OPERATOR)
    assert changed["enabled"] is True
    assert (await signer.get_kill_switch(OPERATOR))["enabled"] is True


def test_http_lists_revoke_kill_permissions_and_path_bounds() -> None:
    repository = RecordingRepository()
    with _client(repository) as client:
        sessions = client.get("/v1/sessions").json()["items"]
        assert "token" not in sessions[0]
        assert client.get("/v1/leases").status_code == 200
        revoked = client.delete("/v1/sessions/session_security1")
        assert revoked.status_code == 204
        assert revoked.content == b""
        assert "content-type" not in revoked.headers
        repository.revoked = False
        assert client.delete("/v1/sessions/session_missing1").status_code == 404
        assert client.delete("/v1/sessions/x").status_code == 400
        assert (
            client.post(
                "/v1/sessions/session_security1/refresh",
                headers={"Idempotency-Key": "http-refresh-operation2"},
                json={"requested_cap": "0"},
            ).status_code
            == 400
        )
        assert client.get("/v1/operations/kill-switch").status_code == 403
        assert (
            client.put(
                "/v1/operations/kill-switch", json={"enabled": True, "reason": "incident"}
            ).status_code
            == 403
        )

    with _client(repository, OPERATOR, credential_id=None) as client:
        assert client.get("/v1/operations/kill-switch").status_code == 200
        changed = client.put(
            "/v1/operations/kill-switch", json={"enabled": True, "reason": "incident"}
        )
        assert changed.status_code == 200
        assert changed.json()["enabled"] is True
        assert (
            client.put(
                "/v1/operations/kill-switch", json={"enabled": True, "reason": ""}
            ).status_code
            == 400
        )
