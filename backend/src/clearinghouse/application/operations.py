"""Authorization and orchestration for operational read models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from clearinghouse.domain.accounts import PrincipalContext, Role
from clearinghouse.domain.operations import (
    AuditEvent,
    BackupEvent,
    KeyRotationEvent,
    LegalHold,
    OperationsJob,
    OperationsStatus,
    ProjectionCheck,
    RetentionRun,
)


class OperationsRepository(Protocol):
    async def list_audit_events(
        self, actor: PrincipalContext, limit: int, cursor: str | None
    ) -> Sequence[AuditEvent]: ...

    def list_adapters(self) -> Sequence[Mapping[str, object]]: ...

    async def status(self, expected_migration: str) -> OperationsStatus: ...

    async def reconcile_projections(
        self,
        actor: PrincipalContext,
        mode: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> ProjectionCheck: ...

    async def run_retention(
        self,
        actor: PrincipalContext,
        category: str,
        mode: str,
        cutoff_at: datetime,
        batch_size: int,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> RetentionRun: ...

    async def list_jobs(self, limit: int, cursor: str | None) -> Sequence[OperationsJob]: ...

    async def list_projection_checks(
        self, limit: int, cursor: str | None
    ) -> Sequence[ProjectionCheck]: ...

    async def list_legal_holds(self, limit: int, cursor: str | None) -> Sequence[LegalHold]: ...

    async def place_legal_hold(
        self,
        actor: PrincipalContext,
        hold_id: str,
        category: str,
        scope_type: str,
        scope_id: str,
        reason: str,
        review_at: datetime,
        now: datetime,
    ) -> LegalHold: ...

    async def release_legal_hold(
        self, actor: PrincipalContext, hold_id: str, reason: str, now: datetime
    ) -> LegalHold: ...

    async def record_backup_event(
        self,
        actor: PrincipalContext,
        action: str,
        artifact_id: str,
        backup_type: str,
        location_sha256: str,
        checksum_sha256: str,
        key_id: str,
        high_watermark: str,
        retention_until: datetime,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> BackupEvent: ...

    async def record_key_rotation(
        self,
        actor: PrincipalContext,
        rotation_id: str,
        purpose: str,
        action: str,
        key_id: str,
        prior_key_id: str | None,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> KeyRotationEvent: ...


class OperationsService:
    """Expose private operations data only to authorized principals."""

    def __init__(self, repository: OperationsRepository) -> None:
        self.repository = repository

    async def list_audit_events(
        self, actor: PrincipalContext, limit: int, cursor: str | None
    ) -> Sequence[AuditEvent]:
        if not actor.is_operator and (
            Role.TENANT_ADMIN not in actor.roles or actor.tenant_id is None
        ):
            raise PermissionError("operator or tenant administrator role required")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        return await self.repository.list_audit_events(actor, limit, cursor)

    def list_adapters(self, actor: PrincipalContext) -> Sequence[Mapping[str, object]]:
        if not actor.is_operator:
            raise PermissionError("operator role required")
        return self.repository.list_adapters()

    @staticmethod
    def _operator(actor: PrincipalContext) -> None:
        if not actor.is_operator:
            raise PermissionError("operator role required")

    @staticmethod
    def _reason(reason: str) -> None:
        if not 1 <= len(reason) <= 1000:
            raise ValueError("reason must be between 1 and 1000 characters")

    @staticmethod
    def _page(limit: int) -> None:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")

    async def status(self, actor: PrincipalContext) -> OperationsStatus:
        self._operator(actor)
        return await self.repository.status("20260910_0008")

    async def reconcile_projections(
        self,
        actor: PrincipalContext,
        mode: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> ProjectionCheck:
        self._operator(actor)
        self._reason(reason)
        if mode not in {"check", "repair"}:
            raise ValueError("mode must be check or repair")
        if not 16 <= len(idempotency_key) <= 200:
            raise ValueError("idempotency key must be between 16 and 200 characters")
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return await self.repository.reconcile_projections(
            actor, mode, reason, idempotency_key, now
        )

    async def run_retention(
        self,
        actor: PrincipalContext,
        category: str,
        mode: str,
        cutoff_at: datetime,
        batch_size: int,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> RetentionRun:
        self._operator(actor)
        self._reason(reason)
        if category not in {"auth_ephemeral", "browser_sessions", "operational_detail"}:
            raise ValueError("unsupported purge category")
        if mode not in {"dry_run", "apply"}:
            raise ValueError("mode must be dry_run or apply")
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch size must be between 1 and 1000")
        if not 16 <= len(idempotency_key) <= 200:
            raise ValueError("idempotency key must be between 16 and 200 characters")
        if cutoff_at.tzinfo is None or now.tzinfo is None or cutoff_at >= now:
            raise ValueError("cutoff must be timezone-aware and in the past")
        return await self.repository.run_retention(
            actor,
            category,
            mode,
            cutoff_at,
            batch_size,
            reason,
            idempotency_key,
            now,
        )

    async def list_jobs(
        self, actor: PrincipalContext, limit: int, cursor: str | None
    ) -> Sequence[OperationsJob]:
        self._operator(actor)
        self._page(limit)
        return await self.repository.list_jobs(limit, cursor)

    async def list_projection_checks(
        self, actor: PrincipalContext, limit: int, cursor: str | None
    ) -> Sequence[ProjectionCheck]:
        self._operator(actor)
        self._page(limit)
        return await self.repository.list_projection_checks(limit, cursor)

    async def list_legal_holds(
        self, actor: PrincipalContext, limit: int, cursor: str | None
    ) -> Sequence[LegalHold]:
        self._operator(actor)
        self._page(limit)
        return await self.repository.list_legal_holds(limit, cursor)

    async def place_legal_hold(
        self,
        actor: PrincipalContext,
        hold_id: str,
        category: str,
        scope_type: str,
        scope_id: str,
        reason: str,
        review_at: datetime,
        now: datetime,
    ) -> LegalHold:
        self._operator(actor)
        self._reason(reason)
        if category not in {
            "auth_ephemeral",
            "browser_sessions",
            "operational_detail",
            "financial",
            "audit",
            "backups",
        }:
            raise ValueError("unsupported legal hold category")
        if scope_type not in {"global", "tenant", "account", "principal"}:
            raise ValueError("unsupported legal hold scope")
        if not 1 <= len(hold_id) <= 200 or not 1 <= len(scope_id) <= 200:
            raise ValueError("hold and scope identifiers must be bounded")
        if now.tzinfo is None or review_at.tzinfo is None or review_at <= now:
            raise ValueError("review time must be timezone-aware and in the future")
        return await self.repository.place_legal_hold(
            actor, hold_id, category, scope_type, scope_id, reason, review_at, now
        )

    async def release_legal_hold(
        self,
        actor: PrincipalContext,
        hold_id: str,
        reason: str,
        now: datetime,
    ) -> LegalHold:
        self._operator(actor)
        self._reason(reason)
        if not 1 <= len(hold_id) <= 200 or now.tzinfo is None:
            raise ValueError("invalid hold identifier or timestamp")
        return await self.repository.release_legal_hold(actor, hold_id, reason, now)

    async def record_backup_event(
        self,
        actor: PrincipalContext,
        action: str,
        artifact_id: str,
        backup_type: str,
        location_sha256: str,
        checksum_sha256: str,
        key_id: str,
        high_watermark: str,
        retention_until: datetime,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> BackupEvent:
        self._operator(actor)
        self._reason(reason)
        if action not in {"created", "verified", "restore_verified", "expired"}:
            raise ValueError("unsupported backup action")
        if backup_type not in {"logical", "base", "wal"}:
            raise ValueError("unsupported backup type")
        if not 1 <= len(artifact_id) <= 200 or not 1 <= len(key_id) <= 128:
            raise ValueError("backup identifiers must be bounded")
        if not 1 <= len(high_watermark) <= 512:
            raise ValueError("backup high-watermark must be bounded")
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in (location_sha256, checksum_sha256)
        ):
            raise ValueError("backup digests must be lowercase sha256")
        if not 16 <= len(idempotency_key) <= 200:
            raise ValueError("idempotency key must be between 16 and 200 characters")
        if now.tzinfo is None or retention_until.tzinfo is None or retention_until <= now:
            raise ValueError("backup retention must be timezone-aware and in the future")
        return await self.repository.record_backup_event(
            actor,
            action,
            artifact_id,
            backup_type,
            location_sha256,
            checksum_sha256,
            key_id,
            high_watermark,
            retention_until,
            reason,
            idempotency_key,
            now,
        )

    async def record_key_rotation(
        self,
        actor: PrincipalContext,
        rotation_id: str,
        purpose: str,
        action: str,
        key_id: str,
        prior_key_id: str | None,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> KeyRotationEvent:
        self._operator(actor)
        self._reason(reason)
        if purpose not in {
            "auth_pepper",
            "credential_pepper",
            "session_pepper",
            "backup_encryption",
            "signer_webhook",
        } or action not in {"started", "activated", "retired"}:
            raise ValueError("unsupported key rotation")
        if not 1 <= len(rotation_id) <= 200 or not 1 <= len(key_id) <= 128:
            raise ValueError("rotation identifiers must be bounded")
        if prior_key_id is not None and not 1 <= len(prior_key_id) <= 128:
            raise ValueError("prior key identifier must be bounded")
        if not 16 <= len(idempotency_key) <= 200 or now.tzinfo is None:
            raise ValueError("invalid rotation idempotency or timestamp")
        return await self.repository.record_key_rotation(
            actor,
            rotation_id,
            purpose,
            action,
            key_id,
            prior_key_id,
            reason,
            idempotency_key,
            now,
        )
