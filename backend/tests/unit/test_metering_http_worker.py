"""Metering HTTP representation and standalone worker lifecycle tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from clearinghouse import metering_worker
from clearinghouse.adapters.http.accounts import AccountProblem, account_problem_handler
from clearinghouse.adapters.http.metering import router
from clearinghouse.application.metering import MeteringService
from clearinghouse.domain.accounts import AccountId, PrincipalContext, PrincipalId, Role, TenantId
from clearinghouse.domain.metering import (
    Charge,
    OpenReservation,
    ProcessResult,
    ReconciliationCase,
    SignedTicketEvent,
    TransportRecord,
    UsageEvent,
)
from clearinghouse.infrastructure.config import Settings

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
SIGNED_TIME = "2026-09-09T12:00:00.000000001Z"
SIGNED_TIME_NS = 1_788_955_200_000_000_001


class _ReadRepository:
    def __init__(self) -> None:
        self.usage = [self._usage(index) for index in range(2)]
        self.charges = [self._charge(index) for index in range(2)]
        self.cases = [self._case(index) for index in range(2)]
        self.reservations = [self._reservation(index) for index in range(2)]

    @staticmethod
    def _usage(index: int) -> UsageEvent:
        return UsageEvent(
            f"usage_0000000{index}",
            f"reservation_000{index}",
            f"lease_00000000{index}",
            "tenant_00000000",
            "account_0000000",
            "principal_000000",
            "fixed",
            None,
            1,
            "fixed",
            "ratecard_000000",
            1,
            2,
            "wei",
            "manifest_000000",
            "signer_00000000",
            f"event_00000000{index}",
            index,
            1,
            NOW,
            SIGNED_TIME,
            SIGNED_TIME_NS,
            NOW - timedelta(seconds=index),
        )

    @staticmethod
    def _charge(index: int) -> Charge:
        return Charge(
            f"charge_0000000{index}",
            f"usage_0000000{index}",
            f"reservation_000{index}",
            f"lease_00000000{index}",
            "tenant_00000000",
            "account_0000000",
            1,
            "wei",
            "ratecard_000000",
            1,
            2,
            "fixed",
            NOW - timedelta(seconds=index),
        )

    @staticmethod
    def _case(index: int) -> ReconciliationCase:
        return ReconciliationCase(
            f"recon_00000000{index}",
            f"reservation_000{index}",
            "tenant_00000000",
            "account_0000000",
            "missing_confirmation",
            "open",
            "sequence_gap",
            NOW - timedelta(seconds=index),
            None,
        )

    @staticmethod
    def _reservation(index: int) -> OpenReservation:
        return OpenReservation(
            f"reservation_000{index}",
            f"lease_00000000{index}",
            "tenant_00000000",
            "account_0000000",
            "pending",
            3,
            "wei",
            index,
            NOW if index == 0 else None,
            NOW - timedelta(seconds=index),
        )

    async def process(
        self,
        _record: TransportRecord,
        _event: SignedTicketEvent | None,
        _payload_sha256: str,
        _decode_error: str | None,
    ) -> ProcessResult:
        raise AssertionError("read tests must not process records")

    async def list_usage(
        self,
        _actor: PrincipalContext,
        _account_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> list[UsageEvent]:
        if cursor == "bad":
            raise ValueError("invalid cursor")
        return self.usage[: limit + 1]

    async def list_charges(
        self,
        _actor: PrincipalContext,
        _account_id: str | None,
        limit: int,
        _cursor: str | None,
    ) -> list[Charge]:
        return self.charges[: limit + 1]

    async def list_reconciliations(
        self, _actor: PrincipalContext, limit: int, _cursor: str | None
    ) -> list[ReconciliationCase]:
        return self.cases[: limit + 1]

    async def list_open_reservations(
        self,
        _actor: PrincipalContext,
        _account_id: str | None,
        limit: int,
        _cursor: str | None,
    ) -> list[OpenReservation]:
        return self.reservations[: limit + 1]

    async def health(self) -> dict[str, object]:
        return {
            "status": "ready",
            "open_cases": 0,
            "quarantined": 0,
            "unresolved": 0,
            "global_exposure_cap": "100",
            "global_open_exposure": "0",
            "last_checkpoint_at": NOW.isoformat(),
            "last_heartbeat_at": NOW.isoformat(),
        }

    async def checkpoint(self, _topic: str, _partition: int) -> int | None:
        return None

    async def heartbeat(self, _now: datetime) -> None:
        return None

    async def reconcile_pending(self, _now: datetime) -> int:
        return 0


def _client() -> tuple[TestClient, list[PrincipalContext]]:
    holder = PrincipalContext(
        PrincipalId("principal_holder0"),
        frozenset({Role.CREDENTIAL_HOLDER}),
        TenantId("tenant_00000000"),
        AccountId("account_0000000"),
    )
    actors = [holder]
    app = FastAPI()
    app.state.metering_service = MeteringService(_ReadRepository(), lambda _payload: None)
    app.add_exception_handler(AccountProblem, account_problem_handler)  # type: ignore[arg-type]

    @app.middleware("http")
    async def inject_actor(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.principal = actors[0]
        return await call_next(request)

    app.include_router(router)
    return TestClient(app), actors


def test_holder_reads_paginated_usage_and_open_reservations_without_caching() -> None:
    client, _actors = _client()

    usage = client.get("/v1/usage", params={"limit": 1})
    assert usage.status_code == 200
    assert usage.headers["cache-control"] == "no-store"
    assert usage.json()["page"]["next_cursor"]
    assert len(usage.json()["items"]) == 1
    source = usage.json()["items"][0]["source"]
    assert source["signed_current_time"] == SIGNED_TIME
    assert source["signed_current_time_unix_ns"] == str(SIGNED_TIME_NS)

    reservations = client.get("/v1/open-reservations", params={"limit": 1})
    assert reservations.status_code == 200
    assert reservations.headers["cache-control"] == "no-store"
    assert reservations.json()["page"]["next_cursor"]
    assert reservations.json()["items"][0] == {
        "id": "reservation_0000",
        "lease_id": "lease_000000000",
        "tenant_id": "tenant_00000000",
        "account_id": "account_0000000",
        "status": "pending",
        "reserved_amount": {"value": "3", "unit": "wei"},
        "sequence_number": "0",
        "signer_confirmed_at": NOW.isoformat(),
        "created_at": NOW.isoformat(),
    }


def test_operator_reads_all_metering_views_without_caching() -> None:
    client, actors = _client()
    actors[0] = PrincipalContext(PrincipalId("principal_operator0"), frozenset({Role.OPERATOR}))

    for path in (
        "/v1/usage?limit=1",
        "/v1/charges?limit=1",
        "/v1/operations/reconciliation?limit=1",
        "/v1/open-reservations?limit=1",
        "/v1/operations/metering-health",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_metering_http_translates_role_and_cursor_errors() -> None:
    client, _actors = _client()

    forbidden = client.get("/v1/operations/reconciliation")
    assert forbidden.status_code == 403
    assert forbidden.headers["content-type"].startswith("application/problem+json")
    assert forbidden.json()["title"] == "Forbidden"

    invalid = client.get("/v1/usage", params={"cursor": "bad"})
    assert invalid.status_code == 400
    assert invalid.headers["content-type"].startswith("application/problem+json")
    assert invalid.json()["title"] == "Invalid request"


class _OnePassService:
    def __init__(self, stop: asyncio.Event) -> None:
        self.stop = stop
        self.sweeps = 0
        self.heartbeats = 0

    async def reconcile_pending(self) -> int:
        self.sweeps += 1
        self.stop.set()
        return 2

    async def heartbeat(self) -> None:
        self.heartbeats += 1
        self.stop.set()


async def test_worker_sweep_and_heartbeat_stop_cleanly() -> None:
    sweep_stop = asyncio.Event()
    sweep_service = _OnePassService(sweep_stop)
    await metering_worker._sweep(sweep_service, 1, sweep_stop)  # type: ignore[arg-type]
    assert sweep_service.sweeps == 1

    heartbeat_stop = asyncio.Event()
    heartbeat_service = _OnePassService(heartbeat_stop)
    await metering_worker._heartbeat(heartbeat_service, 1, heartbeat_stop)  # type: ignore[arg-type]
    assert heartbeat_service.heartbeats == 1


class _WorkerStore:
    latest: _WorkerStore | None = None

    def __init__(self) -> None:
        self.session_factory = object()
        self.closed = False
        _WorkerStore.latest = self

    @classmethod
    def from_settings(cls, _settings: Settings) -> _WorkerStore:
        return cls()

    async def close(self) -> None:
        self.closed = True


class _BlockedService:
    async def reconcile_pending(self) -> int:
        await asyncio.Future()
        return 0

    async def heartbeat(self) -> None:
        await asyncio.Future()


class _FailingConsumer:
    async def run(self) -> None:
        raise RuntimeError("consumer failed")


class _WaitingConsumer:
    def __init__(self) -> None:
        self.cancelled = False

    async def run(self) -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _patch_worker(
    monkeypatch: pytest.MonkeyPatch, service: _BlockedService, consumer: object
) -> None:
    monkeypatch.setattr(metering_worker, "configure_logging", lambda _level: None)
    monkeypatch.setattr(metering_worker, "PostgresStore", _WorkerStore)
    monkeypatch.setattr(
        metering_worker, "PostgresMeteringRepository", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(metering_worker, "MeteringService", lambda *_args, **_kwargs: service)
    monkeypatch.setattr(
        metering_worker, "KafkaMeteringConsumer", lambda *_args, **_kwargs: consumer
    )


async def test_worker_propagates_task_failure_after_closing_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda *_args: None)
    _patch_worker(monkeypatch, _BlockedService(), _FailingConsumer())

    with pytest.raises(RuntimeError, match="consumer failed"):
        await metering_worker.run(Settings(_env_file=None))

    assert _WorkerStore.latest is not None and _WorkerStore.latest.closed


async def test_worker_signal_shutdown_cancels_tasks_and_closes_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    callbacks: list[Callable[[], None]] = []

    def capture_signal(_name: int, callback: Callable[[], None]) -> None:
        callbacks.append(callback)
        if len(callbacks) == 1:
            loop.call_soon(callback)

    monkeypatch.setattr(loop, "add_signal_handler", capture_signal)
    consumer = _WaitingConsumer()
    _patch_worker(monkeypatch, _BlockedService(), consumer)

    await metering_worker.run(Settings(_env_file=None))

    assert len(callbacks) == 2
    assert consumer.cancelled
    assert _WorkerStore.latest is not None and _WorkerStore.latest.closed
