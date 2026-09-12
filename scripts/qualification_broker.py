#!/usr/bin/env python3
"""Qualify duplicate and delayed signer events through the local Redpanda stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from scripts.qualify_gateway import (
    QualificationError,
    Settings,
    load_env,
    request_json,
    safe_offer,
    select_offer,
    write_evidence,
)

ROOT = Path(__file__).resolve().parents[1]


def post_json(url: str, secret: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(  # noqa: S310 - operator-configured local HTTP(S) origin
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310
            value = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise QualificationError(f"signer authorization fixture failed: {error}") from error
    if not isinstance(value, dict):
        raise QualificationError("signer authorization fixture returned invalid JSON")
    return value


def event_payload(
    *,
    event_id: str,
    auth_id: str,
    state_id: str,
    capability: str,
    orchestrator_address: str,
    sequence: int,
    computed_fee: int,
) -> bytes:
    current = datetime.now(UTC)
    previous = current - timedelta(seconds=1)
    return json.dumps(
        {
            "id": event_id,
            "type": "create_signed_ticket",
            "timestamp": str(int(current.timestamp() * 1000)),
            "gateway": "clearinghouse-broker-qualification",
            "data": {
                "session_id": state_id,
                "session_status": "continuing",
                "app": capability,
                "pipeline": capability,
                "request_id": f"broker-{sequence}",
                "orch_address": orchestrator_address,
                "manifest_id": "broker-qualification",
                "pm_session_id": "broker-qualification",
                "current_time": current.isoformat().replace("+00:00", "Z"),
                "current_time_unix": int(current.timestamp() * 1000),
                "previous_time": previous.isoformat().replace("+00:00", "Z"),
                "previous_time_unix": int(previous.timestamp() * 1000),
                "billable_secs": 1,
                "pixels": 0,
                "session_balance": "0",
                "computed_fee": str(computed_fee),
                "cost": str(computed_fee),
                "sequence_number": sequence,
                "num_tickets": 1,
                "auth_id": auth_id,
            },
        },
        separators=(",", ":"),
    ).encode()


def produce(payload: bytes, *, compose_env: Path, topic: str) -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise QualificationError("docker is required for broker qualification")
    completed = subprocess.run(  # noqa: S603 - fixed docker/rpk command
        [
            docker,
            "compose",
            "--env-file",
            str(compose_env),
            "exec",
            "-T",
            "redpanda",
            "rpk",
            "topic",
            "produce",
            topic,
            "-X",
            "brokers=127.0.0.1:9092",
        ],
        cwd=ROOT,
        input=payload + b"\n",
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode:
        raise QualificationError(
            "Redpanda replay failed: " + completed.stderr.decode(errors="replace")[-500:]
        )
    return completed.stdout.decode(errors="replace").strip()[-500:]


def workload_cost(settings: Settings, workload_id: str, expected_events: int) -> dict[str, object]:
    deadline = time.monotonic() + settings.metering_timeout_seconds
    while time.monotonic() < deadline:
        response, _ = request_json(settings, "/v1/costs")
        items = response.get("items", []) if isinstance(response, dict) else []
        value = next(
            (
                item
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("workload"), dict)
                and item["workload"].get("id") == workload_id
                and int(str(item.get("event_count", 0))) >= expected_events
            ),
            None,
        )
        if isinstance(value, dict):
            return value
        time.sleep(1)
    raise QualificationError("broker event did not reconcile before timeout")


def qualify(settings: Settings, compose_env: Path, topic: str, signer_secret: str) -> Path:
    offers, _ = request_json(settings, "/v1/offers")
    if not isinstance(offers, dict):
        raise QualificationError("offers response must be an object")
    offer = select_offer(offers.get("items"), settings)
    created, _ = request_json(
        settings,
        "/v1/workloads",
        method="POST",
        payload={"offer_id": str(offer["id"]), "client_reference": "broker-qualification"},
    )
    if not isinstance(created, dict):
        raise QualificationError("workload response must be an object")
    workload_id = str(created.get("id", ""))
    token = str(created.get("token", ""))
    price = offer.get("price")
    if not workload_id or not token or not isinstance(price, dict):
        raise QualificationError("workload fixture is incomplete")
    numerator = int(str(price["numerator"]))
    denominator = int(str(price["denominator"]))
    orchestrator_address = str(offer.get("orchestrator_address") or "0x0")
    state_id = f"broker-{uuid4().hex}"
    decision = post_json(
        f"{settings.clearinghouse_url}/v1/compat/go-livepeer/authorize",
        signer_secret,
        {
            "headers": {"Authorization": [f"Bearer {token}"]},
            "state": {
                "StateID": state_id,
                "PMSessionID": "broker-qualification",
                "OrchestratorAddress": orchestrator_address,
                "InitialPricePerUnit": numerator,
                "InitialPixelsPerUnit": denominator,
                "SequenceNumber": 0,
                "App": settings.capability,
                "Type": "live",
            },
        },
    )
    if decision.get("status") != 200 or decision.get("auth_id") != workload_id:
        raise QualificationError(
            f"broker fixture authorization was rejected: {decision.get('reason')}"
        )
    fee = (numerator + denominator - 1) // denominator
    original = event_payload(
        event_id=str(uuid4()),
        auth_id=workload_id,
        state_id=state_id,
        capability=settings.capability,
        orchestrator_address=orchestrator_address,
        sequence=0,
        computed_fee=fee,
    )
    evidence: dict[str, object] = {
        "schema": "livepeer.clearinghouse.billing-qualification.v1",
        "case_id": "signer-event-replay",
        "source": "controlled-broker",
        "status": "failed",
        "maximum_authorized_wei": "0",
        "actual_authorized_wei": "0",
        "selected_offer": safe_offer(offer),
        "workload_id": workload_id,
        "event_hashes": [],
        "producer_results": [],
    }
    try:
        evidence["producer_results"].append(produce(original, compose_env=compose_env, topic=topic))  # type: ignore[union-attr]
        evidence["event_hashes"].append(hashlib.sha256(original).hexdigest())  # type: ignore[union-attr]
        first = workload_cost(settings, workload_id, 1)
        evidence["producer_results"].append(produce(original, compose_env=compose_env, topic=topic))  # type: ignore[union-attr]
        time.sleep(3)
        duplicate = workload_cost(settings, workload_id, 1)
        if duplicate.get("event_count") != first.get("event_count"):
            raise QualificationError("duplicate broker event increased the aggregate")
        request_json(settings, f"/v1/workloads/{workload_id}", method="DELETE")
        delayed = event_payload(
            event_id=str(uuid4()),
            auth_id=workload_id,
            state_id=state_id,
            capability=settings.capability,
            orchestrator_address=orchestrator_address,
            sequence=1,
            computed_fee=fee,
        )
        evidence["producer_results"].append(produce(delayed, compose_env=compose_env, topic=topic))  # type: ignore[union-attr]
        evidence["event_hashes"].append(hashlib.sha256(delayed).hexdigest())  # type: ignore[union-attr]
        final = workload_cost(settings, workload_id, 2)
        evidence.update({"status": "passed", "cost": final, "cleanup": "workload-revoked"})
    finally:
        with suppress(QualificationError):
            request_json(settings, f"/v1/workloads/{workload_id}", method="DELETE")
    return write_evidence(settings, evidence)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("qualification.env"))
    parser.add_argument("--compose-env", type=Path, default=Path(".env"))
    arguments = parser.parse_args()
    try:
        values = load_env(arguments.env_file)
        compose_values = load_env(arguments.compose_env)
        settings = Settings.from_values(
            {
                **values,
                "QUAL_CASE_ID": "signer-event-replay",
                "QUAL_CAPABILITY": "livepeer-example/flux-klein",
                "QUAL_MODEL": "",
                "QUAL_ORCHESTRATOR": "",
                "QUAL_EXPECT_UNIT": "seconds",
                "QUAL_EXPECT_CURRENCY": "wei",
                "QUAL_MAX_PRICE_NUMERATOR": "2000000000",
                "QUAL_MAX_PRICE_DENOMINATOR": "1",
            }
        )
        secret = compose_values.get("CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET", "")
        if not secret:
            raise QualificationError("CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET is required")
        path = qualify(
            settings,
            arguments.compose_env,
            compose_values.get("CLEARINGHOUSE_KAFKA_METERING_TOPIC", "livepeer-gateway-events"),
            secret,
        )
        print(f"signer-event-replay: passed ({path})")
        return 0
    except QualificationError as error:
        print(f"broker qualification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
