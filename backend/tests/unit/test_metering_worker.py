from __future__ import annotations

import asyncio

import pytest

from clearinghouse.metering_worker import _heartbeat, _sweep


class Service:
    def __init__(self) -> None:
        self.sweeps = 0
        self.beats = 0

    async def reconcile_pending(self) -> int:
        self.sweeps += 1
        return 1

    async def heartbeat(self) -> None:
        self.beats += 1


@pytest.mark.asyncio
async def test_worker_heartbeat_and_reconciliation_loops_can_stop_cleanly() -> None:
    service = Service()
    stop = asyncio.Event()
    sweep = asyncio.create_task(_sweep(service, 60, stop))  # type: ignore[arg-type]
    heartbeat = asyncio.create_task(_heartbeat(service, 60, stop))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    stop.set()
    await asyncio.gather(sweep, heartbeat)
    assert service.sweeps == 1 and service.beats == 1


@pytest.mark.asyncio
async def test_worker_does_not_refresh_readiness_when_kafka_poll_is_stale() -> None:
    service = Service()
    stop = asyncio.Event()

    async def stale() -> None:
        return None

    heartbeat = asyncio.create_task(_heartbeat(service, 60, stop, stale))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    stop.set()
    await heartbeat
    assert service.beats == 0
