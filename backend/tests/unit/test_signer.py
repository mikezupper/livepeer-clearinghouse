"""Exact signer domain, service, and HTTP boundary tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clearinghouse.adapters.http.signer import SessionActor, session_actor
from clearinghouse.application.signer import SignerService
from clearinghouse.domain.accounts import AccountId, PrincipalContext, PrincipalId, Role, TenantId
from clearinghouse.domain.signer import (
    Admission,
    Lease,
    PaymentState,
    SignerSession,
    ceil_ratio,
    epoch_nanoseconds,
    reservation_quantity,
    state_digest,
)
from clearinghouse.infrastructure.config import Settings
from clearinghouse.main import create_app

NOW = datetime(2026, 9, 9, tzinfo=UTC)
ACTOR = PrincipalContext(
    PrincipalId("principal_12345678"),
    frozenset({Role.CREDENTIAL_HOLDER}),
    TenantId("tenant_12345678"),
    AccountId("account_12345678"),
)


def lease() -> Lease:
    return Lease("lease_12345678", 100, 90, 10, 0, "wei", NOW + timedelta(hours=1))


def payment(**changes: Any) -> PaymentState:
    values: dict[str, Any] = {
        "state_id": "state_12345678",
        "pm_session_id": "pm_12345678",
        "last_update": "2026-09-09T12:00:00.123456789Z",
        "orchestrator_address": "0x" + "a" * 40,
        "app": "preflight",
        "auth_expiry": 0,
        "sender_nonce": 7,
        "balance": "10/3",
        "initial_price_per_unit": 3,
        "initial_pixels_per_unit": 2,
        "payment_type": "live",
        "sequence_number": 0,
        "auth_id": "",
    }
    values.update(changes)
    return PaymentState(**values)


class FakeSignerRepository:
    signer_id = "signer_12345678"

    def __init__(self) -> None:
        self.initialized = False
        self.admission = Admission(True, session_id="ssn_12345678", lease=lease())
        self.created_credential: str | None = None
        self.killed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def create_session(self, **values: Any) -> SignerSession:
        self.created_credential = values["credential_id"]
        return SignerSession(
            "ssn_12345678",
            "",
            "https://signer.test",
            "https://signer.test",
            lease().expires_at,
            lease(),
        )

    async def authorize(self, **_values: Any) -> Admission:
        return self.admission

    async def refresh_session(self, **_values: Any) -> SignerSession:
        return SignerSession(
            "ssn_replacement",
            "",
            "https://signer.test",
            "https://signer.test",
            lease().expires_at,
            lease(),
        )

    async def list_sessions(self, _principal: PrincipalContext) -> Sequence[SignerSession]:
        return (await self.create_session(credential_id=None),)

    async def list_leases(self, principal: PrincipalContext) -> Sequence[SignerSession]:
        return await self.list_sessions(principal)

    async def revoke_session(
        self, _principal: PrincipalContext, session_id: str, _now: datetime
    ) -> bool:
        return session_id == "ssn_12345678"

    async def set_kill_switch(
        self, enabled: bool, reason: str, actor: PrincipalContext, now: datetime
    ) -> dict[str, object]:
        self.killed = enabled
        return {"enabled": enabled, "reason": reason, "actor_id": actor.id, "changed_at": now}

    async def get_kill_switch(self) -> dict[str, object]:
        return {"enabled": self.killed, "reason": "test", "changed_at": NOW, "actor_id": None}


def service(repository: FakeSignerRepository | None = None) -> SignerService:
    return SignerService(
        repository or FakeSignerRepository(),
        "signer-pepper-with-adequate-length",
        "webhook-secret",
        clock=lambda: NOW,
        token_factory=lambda: "deterministic-token",
    )


def test_exact_time_quantity_rate_and_digest() -> None:
    assert epoch_nanoseconds("1970-01-01T00:00:00.000000001Z") == 1
    assert epoch_nanoseconds("1970-01-01T01:00:00+01:00") == 0
    with pytest.raises(ValueError):
        epoch_nanoseconds("not-a-time")
    assert reservation_quantity("fixed", 0) == (1, "fixed")
    assert reservation_quantity("live", 1_000_000_001) == (2, "seconds")
    assert reservation_quantity("live", 0) == (10, "seconds")
    assert reservation_quantity("lv2v", 0) == (1_658_880_000, "720p-pixel-seconds")
    with pytest.raises(ValueError):
        reservation_quantity("unknown", 0)
    assert ceil_ratio(3, 5, 2) == 8
    with pytest.raises(ValueError):
        ceil_ratio(1, 1, 0)
    assert state_digest(payment()) == state_digest(payment())
    assert state_digest(payment(sender_nonce=8)) != state_digest(payment())


@pytest.mark.asyncio
async def test_service_mints_once_and_enforces_roles() -> None:
    repository = FakeSignerRepository()
    signer = service(repository)
    await signer.initialize()
    assert repository.initialized
    value = await signer.create_session(
        ACTOR, "live", None, "preflight", 100, "wei", 60, "operation-key-0001", "cred_12345678"
    )
    assert value.token == "och_ss_deterministic-token"  # noqa: S105
    assert repository.created_credential == "cred_12345678"
    assert signer.verify_webhook("webhook-secret")
    assert not signer.verify_webhook("wrong")
    with pytest.raises(PermissionError):
        await signer.create_session(
            PrincipalContext(ACTOR.id, frozenset(), ACTOR.tenant_id, ACTOR.account_id),
            "live",
            None,
            "preflight",
            1,
            "wei",
            60,
            "operation-key-0002",
        )
    with pytest.raises(ValueError):
        await signer.create_session(
            ACTOR, "live", None, "preflight", 0, "wei", 60, "operation-key-0003"
        )


def test_http_sessions_compat_and_operations() -> None:
    repository = FakeSignerRepository()
    signer = service(repository)
    app = create_app(
        Settings(environment="test", _env_file=None),
        store=None,
        auth_service=None,
        account_service=None,
        onboarding_service=None,
        signer_service=signer,
    )
    app.dependency_overrides[session_actor] = lambda: SessionActor(ACTOR)
    state = {
        "StateID": "state_12345678",
        "PMSessionID": "pm_12345678",
        "LastUpdate": "2026-09-09T12:00:00.123456789Z",
        "OrchestratorAddress": "0x" + "a" * 40,
        "App": "preflight",
        "AuthExpiry": 0,
        "SenderNonce": 7,
        "Balance": "10/3",
        "InitialPricePerUnit": 3,
        "InitialPixelsPerUnit": 2,
        "Type": "live",
        "SequenceNumber": 0,
        "AuthID": "",
    }
    with TestClient(app) as client:
        response = client.post(
            "/v1/sessions",
            headers={"Idempotency-Key": "http-operation-0001"},
            json={"capability": "live", "app": "preflight", "requested_cap": "100", "unit": "wei"},
        )
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["token"] == "och_ss_deterministic-token"  # noqa: S105
        assert client.get("/v1/sessions").status_code == 200
        assert client.get("/v1/leases").status_code == 200
        assert client.delete("/v1/sessions/ssn_12345678").status_code == 204
        assert (
            client.post(
                "/v1/compat/go-livepeer/authorize",
                headers={"Authorization": "Bearer webhook-secret"},
                json={"headers": {"Authorization": ["Bearer och_ss_value"]}, "state": state},
            ).json()["status"]
            == 200
        )
        denied = client.post(
            "/v1/compat/go-livepeer/authorize",
            headers={"Authorization": "Bearer wrong"},
            json={"headers": {}, "state": state},
        )
        assert denied.status_code == 401
        assert denied.headers["content-type"].startswith("application/problem+json")
