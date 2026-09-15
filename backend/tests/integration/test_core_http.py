from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from contracts.ports.v2.protocols import DiscoveredPrice, DiscoverySnapshotUnavailable
from pydantic import SecretStr

from clearinghouse.infrastructure.simple_config import CoreSettings
from clearinghouse.infrastructure.sqlite import SqliteStore
from clearinghouse.simple_main import create_app


class Sender:
    code = ""

    async def send_code(self, email: str, code: str, expires_in_minutes: int) -> None:
        del email, expires_in_minutes
        self.code = code


class Discovery:
    async def discover(self, capability: str | None, model: str | None) -> list[DiscoveredPrice]:
        now = datetime.now(UTC)
        value = DiscoveredPrice(
            "https://runner.example.com/live",
            "0x0000000000000000000000000000000000000001",
            "live",
            "noop",
            {
                "orchestrator_url": "https://orch.example.com",
                "runner_id": "runner-1",
                "mode": "persistent",
            },
            2,
            1,
            "wei",
            "seconds",
            now,
        )
        if capability and capability != value.capability:
            return []
        if model and model != value.model:
            return []
        return [value]


class FlakyDiscovery(Discovery):
    unavailable = False

    async def discover(self, capability: str | None, model: str | None) -> list[DiscoveredPrice]:
        if self.unavailable:
            raise DiscoverySnapshotUnavailable("partial snapshot")
        return await super().discover(capability, model)


class ChangingDiscovery:
    calls = 0

    async def discover(self, capability: str | None, model: str | None) -> list[DiscoveredPrice]:
        del capability, model
        self.calls += 1
        observed = datetime.now(UTC) + timedelta(seconds=self.calls)
        return [
            DiscoveredPrice(
                f"https://runner.example.com/{index}",
                None,
                "live",
                f"model-{index}",
                {"orchestrator_url": "https://orch.example.com"},
                index + 1,
                1,
                "wei",
                "fixed",
                observed,
            )
            for index in range(2)
        ]


async def _login(client: httpx.AsyncClient, sender: Sender, email: str) -> dict[str, str]:
    assert (await client.post("/v1/auth/email/code", json={"email": email})).status_code == 202
    response = await client.post(
        "/v1/auth/email/verify", json={"email": email, "code": sender.code}
    )
    assert response.status_code == 200
    return {"Origin": "https://app.example.com", "X-CSRF-Token": client.cookies["och_csrf"]}


async def test_complete_user_signer_and_admin_http_slice(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "core-http.db")
    await store.initialize()
    sender = Sender()
    settings = CoreSettings(
        _env_file=None,
        database_path=store.path,
        public_url="https://app.example.com",
        allowed_origins="https://app.example.com",
        admin_email="admin@example.com",
        signer_webhook_secret=SecretStr("signer-test-value"),
    )
    app = create_app(settings, store, sender, Discovery(), consume_kafka=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://app.example.com") as user:
        assert (await user.get("/v1/offers")).status_code == 401
        user_headers = await _login(user, sender, "user@example.com")
        offers = await user.get("/v1/offers")
        assert offers.status_code == 200
        assert offers.headers["X-Clearinghouse-Discovery-Stale"] == "false"
        offer_id = offers.json()["items"][0]["id"]
        assert (await user.get("/v1/offers?cursor=malformed")).status_code == 400
        assert (await user.get("/v1/workloads?limit=201")).status_code == 422
        summary = await user.get("/v1/summary")
        assert summary.status_code == 200
        assert summary.json()["workloads"] == 0
        assert (
            await user.post("/v1/workloads", json={"offer_id": "missing"}, headers=user_headers)
        ).status_code == 400
        discovery = await user.get("/v1/discovery")
        assert discovery.headers["X-Clearinghouse-Discovery-Stale"] == "false"
        assert discovery.json()[0]["runners"][0]["app"] == "live"
        created = await user.post(
            "/v1/workloads",
            json={
                "offer_id": offer_id,
                "client_reference": "sdk-job-1",
                "max_spend_wei": "25",
            },
            headers=user_headers,
        )
        assert created.status_code == 201
        workload = created.json()
        assert (
            await user.delete("/v1/workloads/work_missing", headers=user_headers)
        ).status_code == 404
        assert (await user.get("/v1/admin/overview")).status_code == 403
        assert (
            await user.put(
                "/v1/admin/global-stop",
                json={"enabled": True, "reason": "not allowed"},
                headers=user_headers,
            )
        ).status_code == 403
        unauthenticated = await user.post(
            "/v1/compat/go-livepeer/authorize",
            json={
                "headers": {},
                "state": {
                    "StateID": "state-unauthorized",
                    "OrchestratorAddress": "0x0000000000000000000000000000000000000001",
                    "InitialPricePerUnit": 2,
                    "InitialPixelsPerUnit": 1,
                    "SequenceNumber": 0,
                },
            },
        )
        assert unauthenticated.status_code == 401
        assert workload["sdk_token"] and workload["token"].startswith("och_work_")
        callback = await user.post(
            "/v1/compat/go-livepeer/authorize",
            headers={"Authorization": "Bearer signer-test-value"},
            json={
                "headers": {"Authorization": [f"Bearer {workload['token']}"]},
                "state": {
                    "StateID": "state-1",
                    "PMSessionID": "pm-1",
                    "OrchestratorAddress": "0x0000000000000000000000000000000000000001",
                    "InitialPricePerUnit": 2,
                    "InitialPixelsPerUnit": 1,
                    "SequenceNumber": 0,
                    "AuthID": "",
                    "App": "live",
                    "Type": "live",
                    "LastUpdate": "2026-09-11T12:00:00.123456789Z",
                },
            },
        )
        assert callback.json()["auth_id"] == workload["id"]
        cost = (await user.get("/v1/costs")).json()["items"][0]
        assert cost["quoted_fee"] == "0"
        assert (cost["spend_ceiling"], cost["pending_fee"], cost["remaining_spend"]) == (
            "25",
            "20",
            "5",
        )

    async with httpx.AsyncClient(
        transport=transport, base_url="https://app.example.com"
    ) as administrator:
        admin_headers = await _login(administrator, sender, "admin@example.com")
        overview = await administrator.get("/v1/admin/overview")
        assert overview.status_code == 200
        assert overview.json()["users"] == 2
        users = await administrator.get("/v1/admin/users")
        assert len(users.json()["items"]) == 2
        assert users.json()["next_cursor"] is None
        assert (await administrator.get("/v1/admin/workloads")).status_code == 200
        stopped = await administrator.put(
            "/v1/admin/global-stop",
            json={"enabled": True, "reason": "maintenance"},
            headers=admin_headers,
        )
        assert stopped.json()["enabled"] is True

    async with httpx.AsyncClient(transport=transport, base_url="https://app.example.com") as signer:
        blocked = await signer.post(
            "/v1/compat/go-livepeer/authorize",
            headers={"Authorization": "Bearer signer-test-value"},
            json={
                "headers": {"Authorization": [f"Bearer {workload['token']}"]},
                "state": {
                    "StateID": "state-1",
                    "OrchestratorAddress": "0x0000000000000000000000000000000000000001",
                    "InitialPricePerUnit": 2,
                    "InitialPixelsPerUnit": 1,
                    "SequenceNumber": 1,
                },
            },
        )
    assert (blocked.json()["status"], blocked.json()["reason"]) == (503, "clearinghouse_stopped")
    await store.close()


async def test_offer_cursor_returns_typed_conflict_after_discovery_refresh(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "stale-http.db")
    await store.initialize()
    sender = Sender()
    settings = CoreSettings(
        _env_file=None,
        database_path=store.path,
        public_url="https://app.example.com",
        allowed_origins="https://app.example.com",
    )
    app = create_app(settings, store, sender, ChangingDiscovery(), consume_kafka=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.example.com"
    ) as client:
        await _login(client, sender, "user@example.com")
        first = await client.get("/v1/offers", params={"limit": 1})
        cursor = first.json()["next_cursor"]
        assert cursor
        assert (await client.get("/v1/discovery")).status_code == 200
        stale = await client.get("/v1/offers", params={"limit": 1, "cursor": cursor})
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "stale_cursor"
    await store.close()


async def test_discovery_marks_stale_fallback_and_fails_without_safe_snapshot(
    tmp_path: Path,
) -> None:
    store = SqliteStore(tmp_path / "stale-http.db")
    await store.initialize()
    provider = FlakyDiscovery()
    settings = CoreSettings(
        _env_file=None,
        database_path=store.path,
        discovery_ttl_seconds=3600,
    )
    app = create_app(settings, store, Sender(), provider, consume_kafka=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8080"
    ) as browser:
        fresh = await browser.get("/v1/discovery")
        assert fresh.status_code == 200
        assert fresh.headers["X-Clearinghouse-Discovery-Stale"] == "false"

        provider.unavailable = True
        stale = await browser.get("/v1/discovery")
        assert stale.status_code == 200
        assert stale.json() == fresh.json()
        assert stale.headers["X-Clearinghouse-Discovery-Stale"] == "true"
        assert stale.headers["Warning"] == '110 - "Response is stale"'
    await store.close()

    empty_store = SqliteStore(tmp_path / "empty-http.db")
    await empty_store.initialize()
    empty_app = create_app(settings, empty_store, Sender(), provider, consume_kafka=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=empty_app), base_url="http://localhost:8080"
    ) as browser:
        unavailable = await browser.get("/v1/discovery")
        assert unavailable.status_code == 503
        assert unavailable.json() == {"detail": "priced discovery is temporarily unavailable"}
    await empty_store.close()
