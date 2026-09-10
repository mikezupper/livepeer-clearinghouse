"""FastAPI account route contract and security tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from clearinghouse.adapters.http.accounts import (
    AccountProblem,
    _raise,
    account_problem_handler,
    router,
)
from clearinghouse.application.accounts import (
    AccountService,
    DomainError,
    Forbidden,
    InvalidRequest,
    NotFound,
)
from clearinghouse.application.authentication import AuthPolicy, AuthService
from clearinghouse.domain.accounts import AccountId, PrincipalContext, PrincipalId, Role, TenantId
from clearinghouse.domain.auth import AuthProvider
from clearinghouse.domain.auth import Principal as AuthPrincipal
from clearinghouse.domain.auth import Role as AuthRole
from clearinghouse.infrastructure.accounts import MemoryAccountsRepository
from clearinghouse.infrastructure.config import Settings
from clearinghouse.main import create_app

from .test_authentication import (
    CapturingSender,
    Clock,
    FakeOAuth,
    MemoryAuthRepository,
)


def build_client() -> tuple[TestClient, list[PrincipalContext]]:
    actor = PrincipalContext(PrincipalId("principal_operator000"), frozenset({Role.OPERATOR}))
    actors = [actor]
    app = FastAPI()
    app.state.account_service = AccountService(
        MemoryAccountsRepository(), b"http-test-pepper-is-long-enough"
    )
    app.add_exception_handler(AccountProblem, account_problem_handler)  # type: ignore[arg-type]

    @app.middleware("http")
    async def inject_actor(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = actors[0]
        return await call_next(request)

    app.include_router(router)
    return TestClient(app), actors


def test_crud_grant_catalog_and_validation_routes() -> None:
    client, actors = build_client()
    tenant = client.post("/v1/tenants", json={"display_name": "Acme"}).json()
    account = client.post(
        "/v1/accounts",
        json={
            "tenant_id": tenant["id"],
            "display_name": "Primary",
            "unit": "wei",
            "exposure_cap": "1000",
        },
    ).json()
    principal = client.post(
        "/v1/principals",
        json={
            "tenant_id": tenant["id"],
            "account_id": account["id"],
            "display_name": "Builder",
            "roles": ["credential_holder"],
        },
    ).json()
    issued = client.post(
        "/v1/credentials",
        json={"account_id": account["id"], "principal_id": principal["id"], "label": "CLI"},
    )
    assert issued.status_code == 201 and len(issued.json()["secret"]) >= 32
    credential_id = issued.json()["credential"]["id"]
    assert len(client.get("/v1/tenants").json()["items"]) == 1
    assert client.get(f"/v1/tenants/{tenant['id']}").status_code == 200
    assert (
        client.patch(f"/v1/tenants/{tenant['id']}", json={"display_name": "Acme Two"}).status_code
        == 200
    )
    assert len(client.get(f"/v1/accounts?tenant_id={tenant['id']}").json()["items"]) == 1
    assert client.get(f"/v1/accounts/{account['id']}").status_code == 200
    assert (
        client.patch(
            f"/v1/accounts/{account['id']}",
            json={"exposure_cap": "900", "reason": "reduce exposure"},
        ).status_code
        == 200
    )
    assert len(client.get("/v1/principals").json()) == 1
    assert (
        client.patch(
            f"/v1/principals/{principal['id']}",
            json={"status": "suspended", "reason": "pause"},
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/v1/principals/{principal['id']}",
            json={"status": "active", "reason": "resume"},
        ).status_code
        == 200
    )
    pending = client.post("/v1/principals", json={"roles": []}).json()
    assert (
        client.post(
            f"/v1/principals/{pending['id']}/scope",
            json={
                "tenant_id": tenant["id"],
                "account_id": account["id"],
                "roles": ["credential_holder"],
                "reason": "onboard",
            },
        ).status_code
        == 200
    )
    assert len(client.get("/v1/credentials").json()) == 1
    rotated = client.post(f"/v1/credentials/{credential_id}/rotate")
    assert rotated.status_code == 200
    assert client.delete(f"/v1/credentials/{rotated.json()['credential']['id']}").status_code == 204
    rate = {
        "capability": "live.video",
        "rate": {
            "numerator": "1",
            "denominator": "2",
            "charge_unit": "wei",
            "quantity_unit": "seconds",
        },
        "effective_at": "2026-09-09T00:00:00Z",
    }
    assert client.post("/v1/rate-cards", json=rate).status_code == 201
    assert len(client.get("/v1/rate-cards").json()) == 1
    assert (
        client.put(
            f"/v1/accounts/{account['id']}/capabilities",
            json={"capability": "live.video", "allowed": True, "reason": "approved"},
        ).status_code
        == 204
    )
    key = "http-idempotency-key"
    body = {
        "account_id": account["id"],
        "kind": "credit",
        "amount": {"amount": "50", "unit": "wei"},
        "reason": "prepaid",
    }
    assert client.post("/v1/grants", headers={"Idempotency-Key": key}, json=body).status_code == 201
    changed = {**body, "amount": {"amount": "51", "unit": "wei"}}
    conflict = client.post("/v1/grants", headers={"Idempotency-Key": key}, json=changed)
    assert conflict.status_code == 409
    assert conflict.headers["content-type"].startswith("application/problem+json")
    assert "detail" not in conflict.json() and conflict.json()["status"] == 409
    assert client.get(f"/v1/balances/{account['id']}").json()["posted"]["amount"] == "50"
    assert len(client.get(f"/v1/grants?account_id={account['id']}").json()["items"]) == 1
    invalid = client.post(
        "/v1/accounts",
        json={
            "tenant_id": tenant["id"],
            "display_name": "Too large",
            "unit": "wei",
            "exposure_cap": "9" * 79,
        },
    )
    assert invalid.status_code == 422
    actors[0] = PrincipalContext(
        PrincipalId(principal["id"]),
        frozenset({Role.CREDENTIAL_HOLDER}),
        TenantId(tenant["id"]),
        AccountId(account["id"]),
    )
    catalog = client.get("/v1/catalog")
    assert catalog.status_code == 200 and catalog.json()[0]["capability"] == "live.video"


def test_unauthenticated_routes_fail_closed() -> None:
    app = FastAPI()
    app.state.account_service = AccountService(
        MemoryAccountsRepository(), b"http-test-pepper-is-long-enough"
    )
    app.add_exception_handler(AccountProblem, account_problem_handler)  # type: ignore[arg-type]
    app.include_router(router)
    response = TestClient(app).get("/v1/tenants")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


def test_bounded_pages_are_non_overlapping_and_tenant_scoped() -> None:
    client, actors = build_client()
    tenants = [
        client.post("/v1/tenants", json={"display_name": name}).json() for name in ("A", "B")
    ]
    accounts = [
        client.post(
            "/v1/accounts",
            json={
                "tenant_id": tenant["id"],
                "display_name": f"Account {index}",
                "unit": "wei",
                "exposure_cap": "100",
            },
        ).json()
        for index, tenant in enumerate(tenants)
    ]
    for index, account in enumerate(accounts):
        response = client.post(
            "/v1/grants",
            headers={"Idempotency-Key": f"pagination-grant-{index}"},
            json={
                "account_id": account["id"],
                "kind": "credit",
                "amount": {"amount": "10", "unit": "wei"},
                "reason": "pagination fixture",
            },
        )
        assert response.status_code == 201

    for path in ("/v1/tenants", "/v1/accounts", "/v1/grants"):
        first = client.get(f"{path}?limit=1")
        assert first.status_code == 200
        cursor = first.json()["page"]["next_cursor"]
        assert isinstance(cursor, str)
        second = client.get(f"{path}?limit=1&cursor={cursor}")
        assert second.status_code == 200
        assert first.json()["items"][0]["id"] != second.json()["items"][0]["id"]
        assert second.json()["page"]["next_cursor"] is None
        malformed = client.get(f"{path}?limit=1&cursor=bad")
        assert malformed.status_code == 400
        assert malformed.headers["content-type"].startswith("application/problem+json")

    actors[0] = PrincipalContext(
        PrincipalId("principal_tenantadmin0"),
        frozenset({Role.TENANT_ADMIN}),
        TenantId(tenants[0]["id"]),
    )
    visible_tenants = client.get("/v1/tenants?limit=1").json()
    assert [item["id"] for item in visible_tenants["items"]] == [tenants[0]["id"]]
    visible_accounts = client.get(f"/v1/accounts?tenant_id={tenants[1]['id']}").json()
    assert [item["id"] for item in visible_accounts["items"]] == [accounts[0]["id"]]
    visible_grants = client.get("/v1/grants").json()
    assert [item["account_id"] for item in visible_grants["items"]] == [accounts[0]["id"]]


def test_problem_translation_covers_stable_statuses() -> None:
    for error, expected in (
        (Forbidden("no"), 403),
        (NotFound("gone"), 404),
        (InvalidRequest("bad"), 400),
        (DomainError("unexpected"), 500),
    ):
        with pytest.raises(AccountProblem) as problem:
            _raise(error)
        assert problem.value.status_code == expected


def test_account_runtime_openapi_has_closed_success_and_problem_contracts() -> None:
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]

    expected_operations = {
        ("/v1/tenants", "get"),
        ("/v1/tenants", "post"),
        ("/v1/tenants/{tenant_id}", "get"),
        ("/v1/tenants/{tenant_id}", "patch"),
        ("/v1/accounts", "get"),
        ("/v1/accounts", "post"),
        ("/v1/accounts/{account_id}", "get"),
        ("/v1/accounts/{account_id}", "patch"),
        ("/v1/accounts/{account_id}/capabilities", "put"),
        ("/v1/principals", "get"),
        ("/v1/principals", "post"),
        ("/v1/principals/{principal_id}", "patch"),
        ("/v1/principals/{principal_id}/scope", "post"),
        ("/v1/credentials", "get"),
        ("/v1/credentials", "post"),
        ("/v1/credentials/{credential_id}/rotate", "post"),
        ("/v1/credentials/{credential_id}", "delete"),
        ("/v1/grants", "get"),
        ("/v1/grants", "post"),
        ("/v1/balances/{account_id}", "get"),
        ("/v1/rate-cards", "get"),
        ("/v1/rate-cards", "post"),
        ("/v1/catalog", "get"),
    }
    assert expected_operations == {
        (path, method)
        for path, item in paths.items()
        for method in item
        if method in {"get", "post", "patch", "put", "delete"}
    }

    for path, method in expected_operations:
        operation = paths[path][method]
        assert operation["security"] == [{"cookieAuth": []}]
        success = next(
            response for code, response in operation["responses"].items() if 200 <= int(code) < 300
        )
        if int(next(code for code in operation["responses"] if 200 <= int(code) < 300)) != 204:
            schema = success["content"]["application/json"]["schema"]
            assert schema.get("additionalProperties") is not True
            assert "$ref" in schema or schema.get("type") == "array"

        unauthorized = operation["responses"]["401"]
        assert set(unauthorized["content"]) == {"application/problem+json"}
        assert (
            unauthorized["content"]["application/problem+json"]["schema"]["additionalProperties"]
            is False
        )

    create_account = paths["/v1/accounts"]["post"]["responses"]
    assert {"400", "401", "403"} <= set(create_account)
    assert "application/problem+json" in create_account["400"]["content"]
    assert set(create_account["403"]["content"]) == {"application/problem+json"}
    assert paths["/v1/grants"]["post"]["responses"]["409"]["content"].keys() == {
        "application/problem+json"
    }


def test_real_auth_dependency_validates_csrf_once_for_mutation() -> None:
    auth_repository = MemoryAuthRepository()
    sender = CapturingSender()
    auth = AuthService(
        auth_repository,
        sender,
        FakeOAuth(),
        AuthPolicy(pepper="integrated-test-pepper"),
        enabled_providers=frozenset({AuthProvider.EMAIL}),
        clock=Clock(),
    )
    accounts = AccountService(MemoryAccountsRepository(), b"integrated-credential-pepper")
    settings = Settings(
        environment="test",
        auth_cookie_secure=False,
        auth_allowed_origins="http://app.example",
        _env_file=None,
    )
    app = create_app(settings, auth_service=auth, account_service=accounts)
    with TestClient(app, base_url="http://api.example") as client:
        assert (
            client.post("/v1/auth/email/code", json={"email": "operator@example.com"}).status_code
            == 202
        )
        code = sender.messages[0][1]
        assert (
            client.post(
                "/v1/auth/email/verify",
                json={"email": "operator@example.com", "code": code},
            ).status_code
            == 200
        )
        for key, session in auth_repository.sessions.items():
            auth_repository.sessions[key] = replace(
                session,
                principal=AuthPrincipal(
                    session.principal.id, None, None, frozenset({AuthRole.OPERATOR})
                ),
            )
        csrf = str(client.cookies.get("och_csrf"))
        rejected = client.post(
            "/v1/tenants",
            json={"display_name": "Denied"},
            headers={"Origin": "http://app.example", "X-CSRF-Token": "wrong"},
        )
        assert rejected.status_code == 403
        accepted = client.post(
            "/v1/tenants",
            json={"display_name": "Accepted"},
            headers={"Origin": "http://app.example", "X-CSRF-Token": csrf},
        )
        assert accepted.status_code == 201
        assert client.get("/v1/tenants").status_code == 200
