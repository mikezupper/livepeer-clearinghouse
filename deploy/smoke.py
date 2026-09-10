"""Deterministic core-stack smoke without keys, funds, mail, or chain writes."""

from __future__ import annotations

import asyncio
import json
import os
from http.client import HTTPResponse
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from clearinghouse.infrastructure.config import Settings


def request(
    path: str, *, payload: object | None = None, headers: dict[str, str] | None = None
) -> HTTPResponse:
    base = os.environ.get("SMOKE_EDGE_URL", "http://edge:8080").rstrip("/")
    body = None if payload is None else json.dumps(payload).encode()
    probe = Request(  # noqa: S310 -- fixed deployment-controlled HTTP URL.
        base + path,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    return cast(
        HTTPResponse,
        urlopen(probe, timeout=5),  # noqa: S310 -- fixed deployment-controlled HTTP URL.
    )


def check_http(settings: Settings) -> None:
    for path in ("/", "/admin/", "/health/live", "/health/ready"):
        with request(path) as response:
            body = response.read(1_048_577)
            if response.status != 200 or not body or len(body) > 1_048_576:
                raise RuntimeError(f"smoke HTTP probe failed: {path}")
            if not response.headers.get("Content-Security-Policy"):
                raise RuntimeError("edge security headers are missing")
    # The keyless smoke intentionally omits remote-signer. Exact protocol paths
    # must therefore reach the absent signer upstream (502), never the user SPA.
    for path in (
        "/generate-live-payment",
        "/sign-orchestrator-info",
        "/discover-orchestrators",
    ):
        try:
            with request(path) as response:
                response.read(1_048_577)
        except HTTPError as error:
            if error.code == 502:
                continue
        raise RuntimeError(f"signer protocol route reached the wrong upstream: {path}")
    state = {
        "StateID": "smoke_invalid_state",
        "PMSessionID": "",
        "LastUpdate": "2026-09-09T00:00:00Z",
        "OrchestratorAddress": "0x" + "1" * 40,
        "App": "smoke",
        "AuthExpiry": 0,
        "SenderNonce": 0,
        "Balance": "0",
        "InitialPricePerUnit": 1,
        "InitialPixelsPerUnit": 1,
        "Type": "fixed",
        "SequenceNumber": 0,
        "AuthID": "",
    }
    with request(
        "/v1/compat/go-livepeer/authorize",
        payload={
            "headers": {"Authorization": ["Bearer smoke_invalid_session_credential"]},
            "state": state,
        },
        headers={"Authorization": "Bearer " + settings.signer_webhook_secret.get_secret_value()},
    ) as response:
        decision = json.loads(response.read(1_048_577))
    if (
        response.status != 200
        or decision.get("status") not in {401, 402, 403}
        or decision.get("expiry") != 0
    ):
        raise RuntimeError("authenticated signer webhook did not return a typed denial")


def signed_event(event_id: str) -> bytes:
    signed_time = "2026-09-09T00:00:00.000000001Z"
    return json.dumps(
        {
            "id": event_id,
            "type": "create_signed_ticket",
            "timestamp": "1788912000000",
            "gateway": "",
            "data": {
                "session_id": "smoke_unknown_state",
                "session_status": "new",
                "app": "smoke",
                "pipeline": "fixed",
                "request_id": "smoke_request_0000",
                "orch_address": "0x" + "1" * 40,
                "orch_url": "",
                "manifest_id": "smoke_manifest_000",
                "pm_session_id": "smoke_pm_session_0",
                "current_time": signed_time,
                "current_time_unix": 1788912000000,
                "previous_time": signed_time,
                "previous_time_unix": 1788912000000,
                "billable_secs": 0,
                "pixels": 0,
                "session_balance": "0",
                "computed_fee": "0",
                "cost": "0.0000000000",
                "sequence_number": 0,
                "num_tickets": 1,
                "auth_id": "smoke_unknown_session",
            },
        },
        separators=(",", ":"),
    ).encode()


async def publish_metering(settings: Settings) -> tuple[str, int]:
    event_id = str(uuid4())
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        client_id="clearinghouse-compose-smoke",
    )
    await producer.start()
    try:
        metadata = await producer.send_and_wait(
            settings.kafka_metering_topic, signed_event(event_id), key=event_id.encode()
        )
    finally:
        await producer.stop()
    return event_id, metadata.offset


async def check_metering(settings: Settings) -> None:
    event_id, offset = await publish_metering(settings)

    engine = create_async_engine(settings.database_url)
    try:
        for _ in range(40):
            async with engine.connect() as connection:
                result = (
                    await connection.execute(
                        text("""
                        SELECT outcome,reason,kafka_offset FROM metering_observations
                        WHERE producer_id=:producer AND transport_event_id=:event
                        """),
                        {"producer": settings.signer_id, "event": event_id},
                    )
                ).one_or_none()
                heartbeat = await connection.scalar(
                    text("""
                    SELECT last_seen_at FROM metering_worker_heartbeats
                    WHERE consumer_group=:consumer_group AND topic=:topic
                    """),
                    {
                        "consumer_group": settings.kafka_metering_group_id,
                        "topic": settings.kafka_metering_topic,
                    },
                )
            if result is not None:
                if tuple(result) != ("quarantined", "sequence_gap", offset):
                    raise RuntimeError("synthetic signer event had an unexpected durable outcome")
                if heartbeat is None:
                    raise RuntimeError("metering worker heartbeat is absent")
                return
            await asyncio.sleep(0.25)
    finally:
        await engine.dispose()
    raise RuntimeError("metering consumer did not persist the synthetic signer event")


async def run() -> None:
    settings = Settings()
    if os.environ.get("SMOKE_PUBLISH_ONLY") == "1":
        _event_id, offset = await publish_metering(settings)
        print(json.dumps({"status": "published", "offset": offset}, separators=(",", ":")))
        return
    check_http(settings)
    await check_metering(settings)
    print("compose smoke passed")


if __name__ == "__main__":
    asyncio.run(run())
