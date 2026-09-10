"""Immutable operational read models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One append-only administrative action."""

    id: str
    tenant_id: str | None
    actor_id: str
    action: str
    target_id: str
    reason: str
    request_id: str
    occurred_at: datetime


class JobStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ESCALATED = "escalated"


class ProjectionStatus(StrEnum):
    CONSISTENT = "consistent"
    DRIFT = "drift"
    REPAIRED = "repaired"
    ESCALATED = "escalated"


@dataclass(frozen=True, slots=True)
class OperationsJob:
    id: str
    kind: str
    mode: str
    status: JobStatus
    initiator_id: str
    reason: str
    parameters: dict[str, Any]
    result: dict[str, Any] | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProjectionCheck:
    id: str
    job_id: str
    mode: str
    status: ProjectionStatus
    ledger_high_watermark: str
    lease_high_watermark: str
    account_drift_count: int
    global_drift: bool
    authoritative_violation_count: int
    shadow_sha256: str
    details: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RetentionRun:
    id: str
    job_id: str
    category: str
    mode: str
    cutoff_at: datetime
    high_watermark: str
    scanned_count: int
    deleted_count: int
    held_count: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BackupEvent:
    id: str
    artifact_id: str
    sequence: int
    action: str
    backup_type: str
    location_sha256: str
    checksum_sha256: str
    key_id: str
    high_watermark: str
    retention_until: datetime
    initiator_id: str
    reason: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class KeyRotationEvent:
    id: str
    rotation_id: str
    sequence: int
    purpose: str
    action: str
    key_id: str
    prior_key_id: str | None
    initiator_id: str
    reason: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class LegalHold:
    id: str
    category: str
    scope_type: str
    scope_id: str
    approver_id: str
    reason: str
    review_at: datetime
    placed_at: datetime
    released_at: datetime | None


@dataclass(frozen=True, slots=True)
class OperationsStatus:
    status: str
    checked_at: datetime
    migration_current: str | None
    migration_expected: str
    database_ready: bool
    metering_ready: bool
    last_consumer_heartbeat_at: datetime | None
    last_checkpoint_at: datetime | None
    adapter_count: int
    kill_switch: bool
    open_jobs: int
    failed_jobs: int
    open_legal_holds: int
    latest_backup_verified_at: datetime | None
    latest_retention_at: datetime | None
    latest_projection_status: ProjectionStatus | None
    latest_projection_at: datetime | None
