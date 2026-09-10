"""Machine-readable one-shot operations CLI used by supervised deployment jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clearinghouse.application.operations import OperationsService
from clearinghouse.domain.accounts import PrincipalContext, PrincipalId, Role
from clearinghouse.domain.operations import (
    BackupEvent,
    KeyRotationEvent,
    OperationsStatus,
    ProjectionCheck,
    RetentionRun,
)
from clearinghouse.infrastructure.operations import PostgresOperationsRepository

type CliResult = OperationsStatus | ProjectionCheck | RetentionRun | BackupEvent | KeyRotationEvent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clearinghouse-operations")
    parser.add_argument("--database-url", default=os.getenv("CLEARINGHOUSE_DATABASE_URL"))
    parser.add_argument("--actor-id", default=os.getenv("CLEARINGHOUSE_OPERATIONS_ACTOR_ID"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--mode", required=True, choices=("check", "repair"))
    reconcile.add_argument("--reason", required=True)
    reconcile.add_argument("--idempotency-key")
    retention = commands.add_parser("retention")
    retention.add_argument("--mode", required=True, choices=("dry-run", "apply"))
    retention.add_argument(
        "--category",
        default="operational_detail",
        choices=("auth_ephemeral", "browser_sessions", "operational_detail"),
    )
    retention.add_argument("--reason", required=True)
    retention.add_argument("--batch-size", type=int, default=200)
    retention.add_argument("--retention-days", type=int, default=30)
    retention.add_argument("--idempotency-key")
    backup = commands.add_parser("backup")
    backup.add_argument(
        "--action", required=True, choices=("created", "verified", "restore-verified", "expired")
    )
    backup.add_argument("--artifact-id", required=True)
    backup.add_argument("--backup-type", required=True, choices=("logical", "base", "wal"))
    backup.add_argument("--location-sha256", required=True)
    backup.add_argument("--checksum-sha256", required=True)
    backup.add_argument("--key-id", required=True)
    backup.add_argument("--high-watermark", required=True)
    backup.add_argument("--retention-until", required=True)
    backup.add_argument("--reason", required=True)
    backup.add_argument("--idempotency-key", required=True)
    rotation = commands.add_parser("rotation")
    rotation.add_argument("--rotation-id", required=True)
    rotation.add_argument(
        "--purpose",
        required=True,
        choices=(
            "auth_pepper",
            "credential_pepper",
            "session_pepper",
            "backup_encryption",
            "signer_webhook",
        ),
    )
    rotation.add_argument("--action", required=True, choices=("started", "activated", "retired"))
    rotation.add_argument("--key-id", required=True)
    rotation.add_argument("--prior-key-id")
    rotation.add_argument("--reason", required=True)
    rotation.add_argument("--idempotency-key", required=True)
    return parser


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported JSON value {type(value).__name__}")


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


async def _actor(sessions: async_sessionmaker[Any], actor_id: str | None) -> PrincipalContext:
    if actor_id is None:
        raise ValueError("CLEARINGHOUSE_OPERATIONS_ACTOR_ID or --actor-id is required")
    async with sessions() as session:
        exists = (
            await session.execute(
                text("""SELECT EXISTS(SELECT 1 FROM principals p JOIN principal_roles r
                  ON r.principal_id=p.id WHERE p.id=:id AND p.status='active'
                  AND p.tenant_id IS NULL AND p.account_id IS NULL AND r.role='operator')"""),
                {"id": actor_id},
            )
        ).scalar_one()
    if not exists:
        raise ValueError("operations actor is not an active unscoped operator")
    return PrincipalContext(PrincipalId(actor_id), frozenset({Role.OPERATOR}))


async def _run(arguments: argparse.Namespace) -> int:
    if not arguments.database_url:
        raise ValueError("CLEARINGHOUSE_DATABASE_URL or --database-url is required")
    engine = create_async_engine(arguments.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        service = OperationsService(
            PostgresOperationsRepository(
                sessions,
                (),
                heartbeat_stale_seconds=int(
                    os.getenv("CLEARINGHOUSE_METERING_HEARTBEAT_STALE_SECONDS", "60")
                ),
                consumer_group=os.getenv(
                    "CLEARINGHOUSE_KAFKA_METERING_GROUP_ID", "clearinghouse-metering-v1"
                ),
                topic=os.getenv("CLEARINGHOUSE_KAFKA_METERING_TOPIC", "gateway-events"),
                signer_id=os.getenv("CLEARINGHOUSE_SIGNER_ID", "signer-reference"),
            )
        )
        now = datetime.now(UTC)
        result: CliResult
        exit_code = 0
        if arguments.command == "status":
            result = await service.repository.status("20260910_0008")
            if result.status != "ready":
                exit_code = 2
        else:
            actor = await _actor(sessions, arguments.actor_id)
        if arguments.command == "reconcile":
            key = arguments.idempotency_key or f"ops-cli-{uuid4().hex}"
            result = await service.reconcile_projections(
                actor, arguments.mode, arguments.reason, key, now
            )
            if result.status.value in {"drift", "escalated"}:
                exit_code = 2
        elif arguments.command == "retention":
            if not 1 <= arguments.retention_days <= 3650:
                raise ValueError("retention days must be between 1 and 3650")
            key = arguments.idempotency_key or f"ops-cli-{uuid4().hex}"
            result = await service.run_retention(
                actor,
                arguments.category,
                arguments.mode.replace("-", "_"),
                now - timedelta(days=arguments.retention_days),
                arguments.batch_size,
                arguments.reason,
                key,
                now,
            )
            if arguments.mode == "dry-run" and result.scanned_count:
                exit_code = 2
        elif arguments.command == "backup":
            result = await service.record_backup_event(
                actor,
                arguments.action.replace("-", "_"),
                arguments.artifact_id,
                arguments.backup_type,
                arguments.location_sha256,
                arguments.checksum_sha256,
                arguments.key_id,
                arguments.high_watermark,
                _timestamp(arguments.retention_until),
                arguments.reason,
                arguments.idempotency_key,
                now,
            )
        elif arguments.command == "rotation":
            result = await service.record_key_rotation(
                actor,
                arguments.rotation_id,
                arguments.purpose,
                arguments.action,
                arguments.key_id,
                arguments.prior_key_id,
                arguments.reason,
                arguments.idempotency_key,
                now,
            )
        print(json.dumps(asdict(result), default=_json_default, sort_keys=True))
        return exit_code
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    """Run one bounded operation and emit one JSON document to stdout."""
    try:
        return asyncio.run(_run(_parser().parse_args(argv)))
    except (ValueError, argparse.ArgumentError) as error:
        print(json.dumps({"status": "refused", "error": str(error)}), file=sys.stderr)
        return 3
    except SQLAlchemyError:
        print(
            json.dumps({"status": "unavailable", "error": "operations storage unavailable"}),
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
