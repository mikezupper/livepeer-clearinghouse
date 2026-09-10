"""Standalone supervised Kafka metering and reconciliation process."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import cast

import structlog

from clearinghouse.adapters.kafka import KafkaMeteringConsumer, decode_go_livepeer
from clearinghouse.application.metering import MeteringService
from clearinghouse.infrastructure.config import (
    MeteringWorkerSettings,
    Settings,
    get_metering_worker_settings,
)
from clearinghouse.infrastructure.database import PostgresStore
from clearinghouse.infrastructure.logging import configure_logging
from clearinghouse.infrastructure.metering import PostgresMeteringRepository
from clearinghouse.infrastructure.telemetry import (
    Component,
    Telemetry,
    TelemetryConfig,
    get_telemetry,
    set_telemetry,
)


async def _sweep(service: MeteringService, interval: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        processed = await service.reconcile_pending()
        if processed:
            structlog.get_logger().info("metering_reconciliation_sweep", processed=processed)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


async def _heartbeat(
    service: MeteringService,
    interval: int,
    stop: asyncio.Event,
    poll_age: Callable[[], Awaitable[int | None]] | None = None,
) -> None:
    while not stop.is_set():
        try:
            age = await poll_age() if poll_age is not None else 0
        except Exception:
            age = None
            structlog.get_logger().warning("metering_broker_probe_failed")
        if age is not None and age <= max(10, interval * 2):
            await service.heartbeat()
            observe_health = getattr(service, "observe_health", None)
            if observe_health is not None:
                await observe_health()
            get_telemetry().record_consumer_heartbeat(age)
            get_telemetry().record_adapter_health(Component.METERING, "redpanda", healthy=True)
        else:
            get_telemetry().record_adapter_health(Component.METERING, "redpanda", healthy=False)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


async def run(settings: Settings | MeteringWorkerSettings | None = None) -> None:
    resolved = settings or get_metering_worker_settings()
    configure_logging(resolved.log_level)
    telemetry = Telemetry.configure(
        TelemetryConfig(
            service_name="clearinghouse-metering",
            endpoint=resolved.otel_exporter_otlp_endpoint,
            export_interval_millis=resolved.otel_metric_export_interval_millis,
        )
    )
    set_telemetry(telemetry)
    store = PostgresStore.from_settings(cast(Settings, resolved))
    engine = getattr(store, "engine", None)
    if engine is not None:
        telemetry.instrument_sqlalchemy(engine)
    repository = PostgresMeteringRepository(
        store.session_factory,
        consumer_group=resolved.kafka_metering_group_id,
        topic=resolved.kafka_metering_topic,
        signer_id=resolved.signer_id,
        reconciliation_batch_size=resolved.metering_reconciliation_batch_size,
        confirmation_grace_seconds=resolved.metering_confirmation_grace_seconds,
        heartbeat_stale_seconds=resolved.metering_heartbeat_stale_seconds,
    )
    service = MeteringService(
        repository, decode_go_livepeer, max_payload_bytes=resolved.metering_max_payload_bytes
    )
    consumer = KafkaMeteringConsumer(
        service,
        bootstrap_servers=resolved.kafka_bootstrap_servers,
        topic=resolved.kafka_metering_topic,
        group_id=resolved.kafka_metering_group_id,
        client_id=resolved.kafka_metering_client_id,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(name, stop.set)
    consumer_task = asyncio.create_task(consumer.run(), name="metering-consumer")
    sweep_task = asyncio.create_task(
        _sweep(service, resolved.metering_reconciliation_interval_seconds, stop),
        name="metering-reconciler",
    )
    heartbeat_task = asyncio.create_task(
        _heartbeat(
            service,
            resolved.metering_heartbeat_interval_seconds,
            stop,
            getattr(consumer, "readiness_poll_age", None),
        ),
        name="metering-heartbeat",
    )
    stop_task = asyncio.create_task(stop.wait(), name="metering-stop")
    try:
        done, _ = await asyncio.wait(
            {consumer_task, sweep_task, heartbeat_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            if task is not stop_task:
                task.result()
    finally:
        stop.set()
        for task in (consumer_task, sweep_task, heartbeat_task, stop_task):
            task.cancel()
        await asyncio.gather(
            consumer_task,
            sweep_task,
            heartbeat_task,
            stop_task,
            return_exceptions=True,
        )
        try:
            await store.close()
        finally:
            telemetry.shutdown()
            set_telemetry(Telemetry())


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
