from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from clearinghouse.adapters.http.access import create_access_router
from clearinghouse.application.access import AccessService
from clearinghouse.infrastructure.simple_config import CoreSettings
from clearinghouse.infrastructure.sqlite import SqliteStore


class Sender:
    code = ""

    async def send_code(self, email: str, code: str, expires_in_minutes: int) -> None:
        del email, expires_in_minutes
        self.code = code


@pytest.fixture
async def client(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = SqliteStore(tmp_path / "http.db")
    await store.initialize()
    sender = Sender()
    settings = CoreSettings(
        _env_file=None,
        database_path=store.path,
        admin_email="admin@example.com",
    )
    service = AccessService(
        store,
        sender,
        pepper="test-pepper-is-long-enough",
        code_factory=lambda: "123456",
    )
    app = FastAPI()
    app.include_router(create_access_router(service, settings))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8080"
    ) as value:
        yield value, sender
    await store.close()


async def test_email_session_and_credential_http_journey(client) -> None:  # type: ignore[no-untyped-def]
    browser, sender = client
    assert (await browser.get("/v1/auth/providers")).json() == {"providers": ["email"]}
    assert (
        await browser.post("/v1/auth/email/code", json={"email": "user@example.com"})
    ).status_code == 202
    assert sender.code == "123456"
    verified = await browser.post(
        "/v1/auth/email/verify", json={"email": "user@example.com", "code": sender.code}
    )
    assert verified.status_code == 200
    assert verified.json()["account_id"].startswith("acct_")
    assert (await browser.get("/v1/auth/session")).status_code == 200
    csrf = browser.cookies["och_csrf"]
    forbidden = await browser.post("/v1/credentials", json={"name": "Python"})
    assert forbidden.status_code == 403, forbidden.text
    headers = {"Origin": "http://localhost:8080", "X-CSRF-Token": csrf}
    created = await browser.post("/v1/credentials", json={"name": "Python"}, headers=headers)
    assert created.status_code == 201
    token = created.json()["token"]
    sdk = await browser.get("/v1/credentials", headers={"Authorization": f"Bearer {token}"})
    assert sdk.status_code == 200
    sdk_created = await browser.post(
        "/v1/credentials",
        json={"name": "Automation"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sdk_created.status_code == 201
    assert (
        await browser.delete(
            f"/v1/credentials/{sdk_created.json()['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 204
    assert (
        await browser.delete(
            "/v1/credentials/cred_missing", headers={"Authorization": f"Bearer {token}"}
        )
    ).status_code == 404
    credential_id = created.json()["id"]
    assert (
        await browser.delete(f"/v1/credentials/{credential_id}", headers=headers)
    ).status_code == 204
    assert (await browser.delete("/v1/auth/session", headers=headers)).status_code == 204
    assert (await browser.get("/v1/auth/session")).status_code == 401


async def test_auth_http_rejects_invalid_and_disabled_flows(client) -> None:  # type: ignore[no-untyped-def]
    browser, _sender = client
    assert (await browser.get("/v1/credentials")).status_code == 401
    assert (await browser.post("/v1/auth/email/code", json={"email": "invalid"})).status_code == 400
    assert (
        await browser.post(
            "/v1/auth/email/verify", json={"email": "user@example.com", "code": "000000"}
        )
    ).status_code == 400
    assert (await browser.get("/v1/auth/oauth/google/start")).status_code == 404
    assert (
        await browser.get("/v1/auth/oauth/google/callback", params={"code": "x", "state": "x"})
    ).status_code == 400


def test_oauth_requires_complete_configuration() -> None:
    with pytest.raises(ValueError, match="requires client id and secret"):
        CoreSettings(_env_file=None, auth_google_enabled=True)
    with pytest.raises(ValueError, match="partial github"):
        CoreSettings(_env_file=None, auth_github_client_id="client")
    configured = CoreSettings(
        _env_file=None,
        auth_github_enabled=True,
        auth_github_client_id="client",
        auth_github_client_secret=SecretStr("fixture-oauth-value"),
    )
    assert configured.enabled_oauth == frozenset({"github"})
