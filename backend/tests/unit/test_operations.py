"""Operational read authorization, transport, and manifest security tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from clearinghouse.application.operations import OperationsService
from clearinghouse.domain.accounts import PrincipalContext, PrincipalId, Role, TenantId
from clearinghouse.domain.operations import (
    AuditEvent,
    BackupEvent,
    JobStatus,
    KeyRotationEvent,
    LegalHold,
    OperationsJob,
    OperationsStatus,
    ProjectionCheck,
    ProjectionStatus,
    RetentionRun,
)
from clearinghouse.infrastructure.operations import PostgresOperationsRepository
from clearinghouse.main import create_app
from clearinghouse.operations_worker import _json_default, _parser, _timestamp
from clearinghouse.operations_worker import main as operations_main

NOW = datetime(2026, 9, 10, tzinfo=UTC)
OPERATOR = PrincipalContext(PrincipalId("principal_operator0"), frozenset({Role.OPERATOR}))
ADMIN = PrincipalContext(
    PrincipalId("principal_admin000"),
    frozenset({Role.TENANT_ADMIN}),
    TenantId("tenant_12345678"),
)
HOLDER = PrincipalContext(
    PrincipalId("principal_holder00"),
    frozenset({Role.CREDENTIAL_HOLDER}),
    TenantId("tenant_12345678"),
)


def event(number: int = 1) -> AuditEvent:
    return AuditEvent(
        f"audit_{number:016d}",
        "tenant_12345678",
        "principal_operator0",
        "account.updated",
        "account_12345678",
        "approved",
        "request_12345678",
        NOW,
    )


class Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[PrincipalContext, int, str | None]] = []

    async def list_audit_events(
        self, actor: PrincipalContext, limit: int, cursor: str | None
    ) -> Sequence[AuditEvent]:
        self.calls.append((actor, limit, cursor))
        if cursor == "database-error":
            raise SQLAlchemyError("private database host")
        if cursor == "invalid-cursor":
            raise ValueError("private cursor detail")
        return [event(1), event(2)]

    def list_adapters(self) -> Sequence[Mapping[str, object]]:
        return [
            {
                "manifest_version": "1.0",
                "name": "reference_distribution",
                "version": "0.1.0",
                "source": "builtin",
                "builtin": {"selector": "reference_distribution"},
                "ports": [{"name": "identity", "contract_version": "1.0", "capabilities": []}],
            }
        ]

    async def status(self, expected_migration: str) -> OperationsStatus:
        return OperationsStatus(
            "ready",
            NOW,
            expected_migration,
            expected_migration,
            True,
            True,
            NOW,
            NOW,
            1,
            False,
            0,
            0,
            0,
            NOW,
            NOW,
            ProjectionStatus.CONSISTENT,
            NOW,
        )

    async def reconcile_projections(
        self,
        actor: PrincipalContext,
        mode: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> ProjectionCheck:
        return ProjectionCheck(
            "projection_12345678",
            "job_12345678",
            mode,
            ProjectionStatus.CONSISTENT,
            "ledger:1",
            "lease:1",
            0,
            False,
            0,
            "a" * 64,
            {},
            now,
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
        return RetentionRun(
            "retention_12345678",
            "job_12345678",
            category,
            mode,
            cutoff_at,
            "empty",
            0,
            0,
            0,
            now,
        )

    async def list_jobs(self, limit: int, cursor: str | None) -> Sequence[OperationsJob]:
        return [
            OperationsJob(
                f"job_{number:08d}",
                "retention",
                "dry_run",
                JobStatus.SUCCEEDED,
                str(OPERATOR.id),
                "review",
                {},
                {},
                NOW,
                NOW,
            )
            for number in (1, 2)
        ]

    async def list_projection_checks(
        self, limit: int, cursor: str | None
    ) -> Sequence[ProjectionCheck]:
        return [
            ProjectionCheck(
                f"projection_{number:08d}",
                "job_12345678",
                "check",
                ProjectionStatus.CONSISTENT,
                "ledger:1",
                "lease:1",
                0,
                False,
                0,
                "a" * 64,
                {},
                NOW,
            )
            for number in (1, 2)
        ]

    async def list_legal_holds(self, limit: int, cursor: str | None) -> Sequence[LegalHold]:
        return [
            LegalHold(
                f"hold_{number:08d}",
                "audit",
                "global",
                "global",
                str(OPERATOR.id),
                "investigation",
                NOW + timedelta(days=30),
                NOW,
                None,
            )
            for number in (1, 2)
        ]

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
        return LegalHold(
            hold_id, category, scope_type, scope_id, str(actor.id), reason, review_at, now, None
        )

    async def release_legal_hold(
        self, actor: PrincipalContext, hold_id: str, reason: str, now: datetime
    ) -> LegalHold:
        value = await self.place_legal_hold(
            actor, hold_id, "audit", "global", "global", reason, now + timedelta(days=1), now
        )
        return LegalHold(
            value.id,
            value.category,
            value.scope_type,
            value.scope_id,
            value.approver_id,
            value.reason,
            value.review_at,
            value.placed_at,
            now,
        )

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
        return BackupEvent(
            "backup_event_12345678",
            artifact_id,
            1,
            action,
            backup_type,
            location_sha256,
            checksum_sha256,
            key_id,
            high_watermark,
            retention_until,
            str(actor.id),
            reason,
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
        return KeyRotationEvent(
            "rotation_event_12345678",
            rotation_id,
            1,
            purpose,
            action,
            key_id,
            prior_key_id,
            str(actor.id),
            reason,
            now,
        )


class Store:
    def unit_of_work(self) -> Any:
        raise AssertionError

    async def readiness(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def client(actor: PrincipalContext | None, repository: Repository | None = None) -> TestClient:
    app = create_app(
        store=Store(), operations_service=OperationsService(repository or Repository())
    )
    if actor is not None:

        @app.middleware("http")
        async def inject(request: Request, call_next):  # type: ignore[no-untyped-def]
            request.state.principal = actor
            return await call_next(request)

    return TestClient(app)


@pytest.mark.asyncio
async def test_service_enforces_roles_scope_and_bounds_before_repository() -> None:
    repository = Repository()
    service = OperationsService(repository)
    assert len(await service.list_audit_events(OPERATOR, 1, None)) == 2
    assert len(await service.list_audit_events(ADMIN, 200, "cursor")) == 2
    assert repository.calls == [(OPERATOR, 1, None), (ADMIN, 200, "cursor")]
    with pytest.raises(PermissionError):
        await service.list_audit_events(HOLDER, 1, None)
    with pytest.raises(PermissionError):
        await service.list_audit_events(
            PrincipalContext(PrincipalId("principal_unscoped0"), frozenset({Role.TENANT_ADMIN})),
            1,
            None,
        )
    with pytest.raises(ValueError):
        await service.list_audit_events(OPERATOR, 0, None)
    assert service.list_adapters(OPERATOR)
    with pytest.raises(PermissionError):
        service.list_adapters(ADMIN)


def test_http_pages_audit_and_keeps_operational_responses_private() -> None:
    repository = Repository()
    http = client(OPERATOR, repository)
    response = http.get("/v1/operations/audit-events?limit=1")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["items"] == [
        {
            "id": "audit_0000000000000001",
            "actor_id": "principal_operator0",
            "action": "account.updated",
            "target_id": "account_12345678",
            "reason": "approved",
            "request_id": "request_12345678",
            "occurred_at": "2026-09-10T00:00:00+00:00",
        }
    ]
    assert response.json()["page"]["next_cursor"]
    adapters = http.get("/v1/operations/adapters")
    assert adapters.status_code == 200 and adapters.headers["cache-control"] == "no-store"
    assert adapters.json()[0]["ports"][0]["name"] == "identity"


def test_http_auth_validation_scope_and_storage_errors_are_sanitized() -> None:
    unauthenticated = client(None).get("/v1/operations/audit-events")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["cache-control"] == "no-store"
    assert client(HOLDER).get("/v1/operations/audit-events").status_code == 403
    assert client(ADMIN).get("/v1/operations/adapters").status_code == 403
    malformed = client(OPERATOR).get("/v1/operations/audit-events?limit=0")
    assert malformed.status_code == 400 and malformed.headers["cache-control"] == "no-store"
    assert client(OPERATOR).get("/v1/operations/audit-events?cursor=*").status_code == 400
    unavailable = client(OPERATOR).get("/v1/operations/audit-events?cursor=database-error")
    assert unavailable.status_code == 503
    assert unavailable.headers["cache-control"] == "no-store"
    assert "private database host" not in unavailable.text
    invalid = client(OPERATOR).get("/v1/operations/audit-events?cursor=invalid-cursor")
    assert invalid.status_code == 400 and "private cursor detail" not in invalid.text


def test_manifest_validation_redacts_secret_schema_examples_and_returns_copies() -> None:
    manifest: dict[str, object] = {
        "manifest_version": "1.0",
        "name": "safe_bridge",
        "version": "1.2.3",
        "source": "http_bridge",
        "http_bridge": {
            "base_url": "https://adapter.internal/v1",
            "authentication": "bearer",
            "timeout_ms": 1000,
        },
        "ports": [{"name": "metering", "contract_version": "1.0", "capabilities": ["usage.read"]}],
        "configuration_schema": {
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "default": "do-not-expose", "examples": ["x"]},
                "batch_size": {"type": "integer", "default": 20},
            },
        },
    }
    repository = PostgresOperationsRepository(object(), [manifest])  # type: ignore[arg-type]
    first = repository.list_adapters()[0]
    schema = first["configuration_schema"]
    assert isinstance(schema, dict)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    secret = properties["api_key"]
    assert isinstance(secret, dict)
    assert secret == {"type": "string", "writeOnly": True}
    assert properties["batch_size"] == {"type": "integer", "default": 20}
    first["name"] = "mutated"  # type: ignore[index]
    assert repository.list_adapters()[0]["name"] == "safe_bridge"
    manifest["source"] = "builtin"
    with pytest.raises(ValidationError):
        PostgresOperationsRepository(object(), [manifest])  # type: ignore[arg-type]
    manifest["source"] = "http_bridge"
    manifest["http_bridge"] = {
        "base_url": "https://adapter.internal/v1?api_key=do-not-expose",
        "authentication": "bearer",
        "timeout_ms": 1000,
    }
    with pytest.raises(ValidationError):
        PostgresOperationsRepository(object(), [manifest])  # type: ignore[arg-type]


def test_composed_openapi_operation_ids_match_the_canonical_contract() -> None:
    schema = create_app(store=Store(), operations_service=OperationsService(Repository())).openapi()
    expected = {
        ("/v1/operations/audit-events", "get"): "listAuditEvents",
        ("/v1/operations/adapters", "get"): "listAdapters",
        ("/v1/operations/status", "get"): "getOperationsStatus",
        ("/v1/operations/jobs", "get"): "listOperationsJobs",
        ("/v1/operations/projection-checks", "get"): "listProjectionChecks",
        ("/v1/operations/reconcile", "post"): "reconcileProjections",
        ("/v1/operations/retention", "post"): "runRetention",
        ("/v1/operations/legal-holds", "get"): "listLegalHolds",
        ("/v1/operations/legal-holds", "post"): "placeLegalHold",
        ("/v1/operations/legal-holds/{hold_id}/release", "post"): "releaseLegalHold",
    }
    for (path, method), operation_id in expected.items():
        assert schema["paths"][path][method]["operationId"] == operation_id


def test_operations_openapi_binds_closed_success_problem_and_cookie_contracts() -> None:
    schema = create_app(store=Store(), operations_service=OperationsService(Repository())).openapi()
    expected = {
        ("/v1/operations/audit-events", "get"): "AuditEventPageResponse",
        ("/v1/operations/status", "get"): "OperationsStatusResponse",
        ("/v1/operations/jobs", "get"): "OperationsJobPageResponse",
        ("/v1/operations/projection-checks", "get"): "ProjectionCheckPageResponse",
        ("/v1/operations/reconcile", "post"): "ProjectionCheckResponse",
        ("/v1/operations/retention", "post"): "RetentionRunResponse",
        ("/v1/operations/legal-holds", "get"): "LegalHoldPageResponse",
        ("/v1/operations/legal-holds", "post"): "LegalHoldResponse",
        ("/v1/operations/legal-holds/{hold_id}/release", "post"): "LegalHoldResponse",
    }
    for (path, method), model in expected.items():
        operation = schema["paths"][path][method]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model}"
        }
        assert operation["security"] == [{"cookieAuth": []}]
        for code in ("400", "401", "403", "503"):
            assert set(operation["responses"][code]["content"]) == {"application/problem+json"}
        assert schema["components"]["schemas"][model]["additionalProperties"] is False

    adapters = schema["paths"]["/v1/operations/adapters"]["get"]
    assert adapters["responses"]["200"]["content"]["application/json"]["schema"]["items"] == {
        "$ref": "#/components/schemas/AdapterManifestResponse"
    }
    assert adapters["security"] == [{"cookieAuth": []}]
    for code in ("401", "403"):
        assert set(adapters["responses"][code]["content"]) == {"application/problem+json"}


@pytest.mark.asyncio
async def test_control_service_enforces_operator_bounds_and_aware_times() -> None:
    service = OperationsService(Repository())
    status = await service.status(OPERATOR)
    assert status.status == "ready"
    with pytest.raises(PermissionError):
        await service.status(ADMIN)
    result = await service.reconcile_projections(
        OPERATOR, "check", "scheduled integrity check", "i" * 16, NOW
    )
    assert result.status is ProjectionStatus.CONSISTENT
    with pytest.raises(ValueError):
        await service.reconcile_projections(OPERATOR, "apply", "x", "i" * 16, NOW)
    with pytest.raises(ValueError):
        await service.reconcile_projections(OPERATOR, "check", "x", "short", NOW)
    retention = await service.run_retention(
        OPERATOR,
        "auth_ephemeral",
        "dry_run",
        NOW - timedelta(days=1),
        10,
        "scheduled cleanup",
        "r" * 16,
        NOW,
    )
    assert retention.deleted_count == 0
    with pytest.raises(ValueError):
        await service.run_retention(OPERATOR, "financial", "apply", NOW, 0, "x", "r" * 16, NOW)
    hold = await service.place_legal_hold(
        OPERATOR,
        "hold_12345678",
        "audit",
        "global",
        "global",
        "incident investigation",
        NOW + timedelta(days=30),
        NOW,
    )
    assert hold.released_at is None
    released = await service.release_legal_hold(
        OPERATOR, hold.id, "investigation complete", NOW + timedelta(days=1)
    )
    assert released.released_at is not None
    backup = await service.record_backup_event(
        OPERATOR,
        "created",
        "backup_12345678",
        "logical",
        "a" * 64,
        "b" * 64,
        "backup-key-1",
        "lsn:1",
        NOW + timedelta(days=30),
        "scheduled backup",
        "backup-request-1234",
        NOW,
    )
    assert backup.action == "created"
    rotation = await service.record_key_rotation(
        OPERATOR,
        "rotation_12345678",
        "backup_encryption",
        "started",
        "backup-key-2",
        "backup-key-1",
        "scheduled rotation",
        "rotation-request-1",
        NOW,
    )
    assert rotation.action == "started"
    with pytest.raises(ValueError):
        await service.record_backup_event(
            OPERATOR,
            "created",
            "backup_12345678",
            "logical",
            "not-a-digest",
            "b" * 64,
            "backup-key-1",
            "lsn:1",
            NOW + timedelta(days=30),
            "scheduled backup",
            "backup-request-1234",
            NOW,
        )
    assert await service.list_jobs(OPERATOR, 1, None)
    assert await service.list_projection_checks(OPERATOR, 1, None)
    assert await service.list_legal_holds(OPERATOR, 1, None)


def test_control_http_is_private_csrf_protected_and_machine_readable() -> None:
    http = client(OPERATOR)
    status = http.get("/v1/operations/status")
    assert status.status_code == 200
    assert status.headers["cache-control"] == "no-store"
    assert status.json()["migration"]["ready"] is True
    assert status.json()["metering"]["ready"] is True
    for path in ("jobs", "projection-checks", "legal-holds"):
        page = http.get(f"/v1/operations/{path}?limit=1")
        assert page.status_code == 200 and page.json()["page"]["next_cursor"]
    for path in ("audit-events", "jobs", "projection-checks", "legal-holds"):
        complete = http.get(f"/v1/operations/{path}")
        assert complete.status_code == 200 and complete.json()["page"]["next_cursor"] is None
    headers = {"Idempotency-Key": "operation-key-1234"}
    reconcile = http.post(
        "/v1/operations/reconcile",
        headers=headers,
        json={"mode": "check", "reason": "scheduled check"},
    )
    assert reconcile.status_code == 200
    assert reconcile.json()["status"] == "consistent"
    retention = http.post(
        "/v1/operations/retention",
        headers=headers,
        json={
            "category": "auth_ephemeral",
            "mode": "dry_run",
            "cutoff_at": "2026-09-09T00:00:00Z",
            "batch_size": 10,
            "reason": "scheduled cleanup",
        },
    )
    assert retention.status_code == 200
    placed = http.post(
        "/v1/operations/legal-holds",
        json={
            "hold_id": "hold_12345678",
            "category": "audit",
            "scope_type": "global",
            "scope_id": "global",
            "reason": "incident investigation",
            "review_at": "2026-10-10T00:00:00Z",
        },
    )
    assert placed.status_code == 200
    released = http.post(
        "/v1/operations/legal-holds/hold_12345678/release",
        json={"reason": "investigation complete"},
    )
    assert released.status_code == 200
    assert released.json()["released_at"] is not None
    assert client(ADMIN).get("/v1/operations/status").status_code == 403


@pytest.mark.asyncio
async def test_control_service_rejects_every_unsafe_boundary_shape() -> None:
    service = OperationsService(Repository())
    with pytest.raises(ValueError, match="reason"):
        await service.reconcile_projections(OPERATOR, "check", "", "x" * 16, NOW)
    with pytest.raises(ValueError, match="limit"):
        await service.list_jobs(OPERATOR, 0, None)
    with pytest.raises(ValueError, match="timezone"):
        await service.reconcile_projections(
            OPERATOR, "check", "review", "x" * 16, datetime(2026, 9, 10)
        )
    retention_base = (
        OPERATOR,
        "auth_ephemeral",
        "dry_run",
        NOW - timedelta(days=1),
        10,
        "review",
        "r" * 16,
        NOW,
    )
    for replacement in (
        {"mode": "delete"},
        {"batch_size": 0},
        {"idempotency_key": "short"},
        {"cutoff_at": NOW},
    ):
        names = (
            "actor",
            "category",
            "mode",
            "cutoff_at",
            "batch_size",
            "reason",
            "idempotency_key",
            "now",
        )
        values = dict(zip(names, retention_base, strict=True)) | replacement
        with pytest.raises(ValueError):
            await service.run_retention(**values)  # type: ignore[arg-type]
    hold_base = (
        OPERATOR,
        "hold_12345678",
        "audit",
        "global",
        "global",
        "review",
        NOW + timedelta(days=1),
        NOW,
    )
    for replacement in (
        {"category": "secret"},
        {"scope_type": "unknown"},
        {"hold_id": ""},
        {"review_at": NOW},
    ):
        names = (
            "actor",
            "hold_id",
            "category",
            "scope_type",
            "scope_id",
            "reason",
            "review_at",
            "now",
        )
        values = dict(zip(names, hold_base, strict=True)) | replacement
        with pytest.raises(ValueError):
            await service.place_legal_hold(**values)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="identifier"):
        await service.release_legal_hold(OPERATOR, "", "review", NOW)

    backup_base = (
        OPERATOR,
        "created",
        "backup_12345678",
        "logical",
        "a" * 64,
        "b" * 64,
        "backup-key-1",
        "lsn:1",
        NOW + timedelta(days=1),
        "review",
        "b" * 16,
        NOW,
    )
    backup_names = (
        "actor",
        "action",
        "artifact_id",
        "backup_type",
        "location_sha256",
        "checksum_sha256",
        "key_id",
        "high_watermark",
        "retention_until",
        "reason",
        "idempotency_key",
        "now",
    )
    for replacement in (
        {"action": "uploaded"},
        {"backup_type": "snapshot"},
        {"artifact_id": ""},
        {"high_watermark": ""},
        {"location_sha256": "A" * 64},
        {"idempotency_key": "short"},
        {"retention_until": NOW},
    ):
        values = dict(zip(backup_names, backup_base, strict=True)) | replacement
        with pytest.raises(ValueError):
            await service.record_backup_event(**values)  # type: ignore[arg-type]

    rotation_base = (
        OPERATOR,
        "rotation_12345678",
        "backup_encryption",
        "started",
        "backup-key-2",
        "backup-key-1",
        "review",
        "k" * 16,
        NOW,
    )
    rotation_names = (
        "actor",
        "rotation_id",
        "purpose",
        "action",
        "key_id",
        "prior_key_id",
        "reason",
        "idempotency_key",
        "now",
    )
    for replacement in (
        {"purpose": "database"},
        {"rotation_id": ""},
        {"prior_key_id": ""},
        {"idempotency_key": "short"},
    ):
        values = dict(zip(rotation_names, rotation_base, strict=True)) | replacement
        with pytest.raises(ValueError):
            await service.record_key_rotation(**values)  # type: ignore[arg-type]


def test_operations_cli_boundary_requires_database_and_parses_safe_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLEARINGHOUSE_DATABASE_URL", raising=False)
    assert operations_main(["status"]) == 3
    backup = _parser().parse_args(
        [
            "backup",
            "--action",
            "restore-verified",
            "--artifact-id",
            "backup_12345678",
            "--backup-type",
            "logical",
            "--location-sha256",
            "a" * 64,
            "--checksum-sha256",
            "b" * 64,
            "--key-id",
            "backup-key-1",
            "--high-watermark",
            "0/1",
            "--retention-until",
            "2026-10-10T00:00:00Z",
            "--reason",
            "isolated restore verified",
            "--idempotency-key",
            "backup-request-1234",
        ]
    )
    assert backup.action == "restore-verified"
    assert _timestamp(backup.retention_until).tzinfo is not None
    assert _json_default(NOW) == NOW.isoformat()
    assert _json_default(ProjectionStatus.REPAIRED) == "repaired"
    with pytest.raises(TypeError, match="unsupported JSON"):
        _json_default(object())
    with pytest.raises(ValueError, match="timezone"):
        _timestamp("2026-10-10T00:00:00")
