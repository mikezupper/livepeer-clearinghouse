from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from clearinghouse.adapters.http.accounts import AccountProblem, account_problem_handler
from clearinghouse.adapters.http.metering import router
from clearinghouse.application.metering import MeteringService
from clearinghouse.domain.accounts import AccountId, PrincipalContext, PrincipalId, Role, TenantId
from clearinghouse.domain.metering import Charge, OpenReservation, ReconciliationCase, UsageEvent

NOW = datetime(2026, 9, 9, tzinfo=UTC)


class Reads:
    async def process(self, *args: Any) -> Any:
        raise AssertionError

    async def checkpoint(self, _topic: str, _partition: int) -> int | None:
        return None

    async def heartbeat(self, _now: datetime) -> None:
        return None

    async def reconcile_pending(self, _now: datetime) -> int:
        return 0

    async def list_usage(self, *args: Any) -> list[UsageEvent]:
        if args[-1] == "bad":
            raise ValueError("invalid cursor")
        return [
            UsageEvent(
                f"usage_{number:016d}",
                "receipt_12345678",
                "lease_12345678",
                "tenant_12345678",
                "account_12345678",
                "principal_12345678",
                "fixed",
                None,
                1,
                "fixed",
                "rate_12345678",
                1,
                2,
                "wei",
                "manifest",
                "signer_12345678",
                "00000000-0000-4000-8000-000000000001",
                0,
                1,
                NOW,
                "2026-09-09T00:00:00.000000001Z",
                1788912000000000001,
                NOW,
            )
            for number in range(2)
        ]

    async def list_charges(self, *args: Any) -> list[Charge]:
        return [
            Charge(
                "charge_12345678",
                "usage_12345678",
                "receipt_12345678",
                "lease_12345678",
                "tenant_12345678",
                "account_12345678",
                1,
                "wei",
                "rate_12345678",
                1,
                2,
                "fixed",
                NOW,
            )
        ]

    async def list_reconciliations(self, *args: Any) -> list[ReconciliationCase]:
        return [
            ReconciliationCase(
                "recon_12345678",
                "receipt_12345678",
                "tenant_12345678",
                "account_12345678",
                "fee_mismatch",
                "open",
                "fee_mismatch",
                NOW,
                None,
            )
        ]

    async def list_open_reservations(self, *args: Any) -> list[OpenReservation]:
        return [
            OpenReservation(
                "receipt_12345678",
                "lease_12345678",
                "tenant_12345678",
                "account_12345678",
                "pending",
                1,
                "wei",
                0,
                None,
                NOW,
            )
        ]

    async def health(self) -> dict[str, object]:
        return {
            "status": "degraded",
            "open_cases": 1,
            "quarantined": 1,
            "unresolved": 0,
            "global_exposure_cap": "100",
            "global_open_exposure": "1",
            "last_checkpoint_at": None,
            "last_heartbeat_at": None,
        }


def client(actor: PrincipalContext | None, reads: Reads | None = None) -> TestClient:
    app = FastAPI()
    app.state.metering_service = MeteringService(reads or Reads(), lambda _payload: None)
    app.add_exception_handler(AccountProblem, account_problem_handler)  # type: ignore[arg-type]
    if actor is not None:

        @app.middleware("http")
        async def inject(request: Request, call_next):  # type: ignore[no-untyped-def]
            request.state.principal = actor
            return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_operator_metering_reads_are_paginated_private_and_exact() -> None:
    operator = PrincipalContext(PrincipalId("principal_operator0"), frozenset({Role.OPERATOR}))
    http = client(operator)
    usage = http.get("/v1/usage?limit=1")
    assert usage.status_code == 200 and usage.headers["cache-control"] == "no-store"
    assert usage.json()["page"]["next_cursor"]
    assert usage.json()["items"][0]["source"]["signed_current_time_unix_ns"].endswith("001")
    assert http.get("/v1/charges").json()["items"][0]["reservation_id"]
    assert http.get("/v1/open-reservations").json()["items"][0]["status"] == "pending"
    reconciliation = http.get("/v1/operations/reconciliation")
    assert reconciliation.headers["cache-control"] == "no-store"
    health = http.get("/v1/operations/metering-health")
    assert health.json()["status"] == "degraded" and health.headers["cache-control"] == "no-store"
    assert http.get("/v1/usage?cursor=bad").status_code == 400


def test_metering_reads_enforce_authentication_and_financial_scope() -> None:
    assert client(None).get("/v1/usage").status_code == 401
    holder = PrincipalContext(
        PrincipalId("principal_holder00"),
        frozenset({Role.CREDENTIAL_HOLDER}),
        TenantId("tenant_12345678"),
        AccountId("account_12345678"),
    )
    http = client(holder)
    assert http.get("/v1/usage?account_id=account_12345678").status_code == 200
    assert http.get("/v1/usage?account_id=account_87654321").status_code == 403
    assert http.get("/v1/operations/reconciliation").status_code == 403
    assert http.get("/v1/operations/metering-health").status_code == 403
    assert http.get("/v1/usage?account_id=x").status_code == 422
    assert http.get("/v1/usage?account_id=AAAAAAAA").status_code == 422


def test_metering_storage_failure_is_a_sanitized_service_unavailable_problem() -> None:
    class Unavailable(Reads):
        async def list_usage(self, *args: Any) -> list[UsageEvent]:
            raise SQLAlchemyError("private database detail")

    operator = PrincipalContext(PrincipalId("principal_operator0"), frozenset({Role.OPERATOR}))
    response = client(operator, Unavailable()).get("/v1/usage")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "private database detail" not in response.text


def test_metering_openapi_binds_closed_success_problem_and_cookie_contracts() -> None:
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    expected = {
        "/v1/usage": "UsagePageResponse",
        "/v1/charges": "ChargePageResponse",
        "/v1/operations/reconciliation": "ReconciliationPageResponse",
        "/v1/open-reservations": "OpenReservationPageResponse",
        "/v1/operations/metering-health": "MeteringHealthResponse",
    }
    for path, model in expected.items():
        operation = schema["paths"][path]["get"]
        success = operation["responses"]["200"]["content"]
        assert success == {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{model}"}}
        }
        assert operation["security"] == [{"cookieAuth": []}]
        for code in ("400", "401", "403", "503"):
            assert set(operation["responses"][code]["content"]) == {"application/problem+json"}
    for model in expected.values():
        assert schema["components"]["schemas"][model]["additionalProperties"] is False
    assert (
        schema["components"]["schemas"]["UsagePageResponse"]["properties"]["items"]["maxItems"]
        == 200
    )
