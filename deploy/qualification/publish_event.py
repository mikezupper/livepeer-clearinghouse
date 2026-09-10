"""Publish one bounded qualification event through the configured Kafka broker."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]

MAX_EVENT_BYTES = 65_536


async def publish(payload: bytes) -> dict[str, int]:
    """Send a single event and return only non-sensitive transport coordinates."""
    producer = AIOKafkaProducer(
        bootstrap_servers=os.environ["CLEARINGHOUSE_KAFKA_BOOTSTRAP_SERVERS"],
        client_id="clearinghouse-qualification-journey",
    )
    await producer.start()
    try:
        envelope: Any = json.loads(payload)
        event_id = envelope.get("id") if isinstance(envelope, dict) else None
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("qualification event id is missing")
        metadata = await producer.send_and_wait(
            os.environ["CLEARINGHOUSE_KAFKA_METERING_TOPIC"], payload, key=event_id.encode()
        )
        return {"partition": metadata.partition, "offset": metadata.offset}
    finally:
        await producer.stop()


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
    if not 1 <= len(payload) <= MAX_EVENT_BYTES:
        raise ValueError("qualification event must be bounded and non-empty")
    print(json.dumps(asyncio.run(publish(payload)), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
