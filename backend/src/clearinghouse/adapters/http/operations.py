"""Private operational read endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, JsonValue, PlainSerializer
from sqlalchemy.exc import SQLAlchemyError

from clearinghouse.adapters.http.accounts import (
    COOKIE_SECURITY,
    AccountProblem,
    Actor,
    ProtectedActor,
    account_responses,
)
from clearinghouse.application.operations import OperationsService
from clearinghouse.domain.operations import (
    AuditEvent,
    JobStatus,
    LegalHold,
    OperationsJob,
    OperationsStatus,
    ProjectionCheck,
    ProjectionStatus,
    RetentionRun,
)
from clearinghouse.infrastructure.metering import encode_cursor

router = APIRouter(prefix="/v1/operations", tags=["operations"])


def service(request: Request) -> OperationsService:
    return cast(OperationsService, request.app.state.operations_service)


Service = Annotated[OperationsService, Depends(service)]
OpaqueCursor = Annotated[
    str | None, Query(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_-]+$")
]
IdempotencyKey = Annotated[str, Header(min_length=16, max_length=200)]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


IsoDatetime = Annotated[
    datetime, PlainSerializer(lambda value: value.isoformat(), return_type=str, when_used="json")
]


class ReconcileBody(ApiModel):
    mode: str = Field(pattern=r"^(check|repair)$")
    reason: str = Field(min_length=1, max_length=1000)


class RetentionBody(ApiModel):
    category: str = Field(pattern=r"^(auth_ephemeral|browser_sessions|operational_detail)$")
    mode: str = Field(pattern=r"^(dry_run|apply)$")
    cutoff_at: datetime
    batch_size: int = Field(default=200, ge=1, le=1000)
    reason: str = Field(min_length=1, max_length=1000)


class PlaceHoldBody(ApiModel):
    hold_id: str = Field(min_length=1, max_length=200)
    category: str = Field(
        pattern=r"^(auth_ephemeral|browser_sessions|operational_detail|financial|audit|backups)$"
    )
    scope_type: str = Field(pattern=r"^(global|tenant|account|principal)$")
    scope_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    review_at: datetime


class ReleaseHoldBody(ApiModel):
    reason: str = Field(min_length=1, max_length=1000)


class PageResponse(ApiModel):
    next_cursor: str | None = Field(default=None, max_length=512)


class AuditEventResponse(ApiModel):
    id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=200)
    target_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    request_id: str = Field(min_length=1, max_length=200)
    occurred_at: IsoDatetime


class AuditEventPageResponse(ApiModel):
    items: list[AuditEventResponse] = Field(max_length=200)
    page: PageResponse


class AdapterPortResponse(ApiModel):
    name: Literal[
        "identity", "signer", "custody", "metering", "pricing", "collection", "events_out"
    ]
    contract_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+$")
    capabilities: list[str] = Field(max_length=128)


class BuiltinAdapterResponse(ApiModel):
    selector: str = Field(min_length=1, max_length=128)


class PythonAdapterResponse(ApiModel):
    group: Literal["livepeer.clearinghouse.adapters.v1"]
    name: str = Field(min_length=1, max_length=128)
    distribution: str | None = Field(default=None, min_length=1, max_length=128)


class HttpAdapterResponse(ApiModel):
    base_url: AnyHttpUrl
    authentication: Literal["bearer", "mtls"]
    timeout_ms: int = Field(ge=1, le=60000)


class AdapterManifestResponse(ApiModel):
    manifest_version: Literal["1.0"]
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    source: Literal["builtin", "python_entry_point", "http_bridge"]
    builtin: BuiltinAdapterResponse | None = None
    python_entry_point: PythonAdapterResponse | None = None
    http_bridge: HttpAdapterResponse | None = None
    ports: list[AdapterPortResponse] = Field(min_length=1, max_length=7)
    configuration_schema: dict[str, JsonValue] | None = None


class MigrationStatusResponse(ApiModel):
    current: str | None = Field(default=None, max_length=200)
    expected: str = Field(min_length=1, max_length=200)
    ready: bool


class ReadyStatusResponse(ApiModel):
    ready: bool


class MeteringStatusResponse(ApiModel):
    ready: bool
    last_heartbeat_at: IsoDatetime | None
    last_checkpoint_at: IsoDatetime | None


class AdapterStatusResponse(ApiModel):
    configured: int = Field(ge=0)


class IntegrityStatusResponse(ApiModel):
    kill_switch: bool
    latest_status: ProjectionStatus | None
    last_checked_at: IsoDatetime | None


class JobSummaryResponse(ApiModel):
    running: int = Field(ge=0)
    failed_or_escalated: int = Field(ge=0)


class RetentionStatusResponse(ApiModel):
    open_legal_holds: int = Field(ge=0)
    last_success_at: IsoDatetime | None


class BackupStatusResponse(ApiModel):
    latest_verified_at: IsoDatetime | None


class OperationsStatusResponse(ApiModel):
    status: Literal["ready", "degraded"]
    checked_at: IsoDatetime
    migration: MigrationStatusResponse
    database: ReadyStatusResponse
    metering: MeteringStatusResponse
    adapters: AdapterStatusResponse
    integrity: IntegrityStatusResponse
    jobs: JobSummaryResponse
    retention: RetentionStatusResponse
    backup: BackupStatusResponse


class OperationsJobResponse(ApiModel):
    id: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=128)
    mode: str = Field(min_length=1, max_length=64)
    status: JobStatus
    initiator_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    parameters: dict[str, JsonValue]
    result: dict[str, JsonValue] | None
    started_at: IsoDatetime
    completed_at: IsoDatetime | None


class OperationsJobPageResponse(ApiModel):
    items: list[OperationsJobResponse] = Field(max_length=200)
    page: PageResponse


class ProjectionCheckResponse(ApiModel):
    id: str = Field(min_length=1, max_length=200)
    job_id: str = Field(min_length=1, max_length=200)
    mode: Literal["check", "repair"]
    status: ProjectionStatus
    ledger_high_watermark: str = Field(min_length=1, max_length=512)
    lease_high_watermark: str = Field(min_length=1, max_length=512)
    account_drift_count: int = Field(ge=0)
    global_drift: bool
    authoritative_violation_count: int = Field(ge=0)
    shadow_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    details: dict[str, JsonValue]
    created_at: IsoDatetime


class ProjectionCheckPageResponse(ApiModel):
    items: list[ProjectionCheckResponse] = Field(max_length=200)
    page: PageResponse


class RetentionRunResponse(ApiModel):
    id: str = Field(min_length=1, max_length=200)
    job_id: str = Field(min_length=1, max_length=200)
    category: Literal["auth_ephemeral", "browser_sessions", "operational_detail"]
    mode: Literal["dry_run", "apply"]
    cutoff_at: IsoDatetime
    high_watermark: str = Field(min_length=1, max_length=512)
    scanned_count: int = Field(ge=0)
    deleted_count: int = Field(ge=0)
    held_count: int = Field(ge=0)
    created_at: IsoDatetime


class LegalHoldResponse(ApiModel):
    id: str = Field(min_length=1, max_length=200)
    category: Literal[
        "auth_ephemeral",
        "browser_sessions",
        "operational_detail",
        "financial",
        "audit",
        "backups",
    ]
    scope_type: Literal["global", "tenant", "account", "principal"]
    scope_id: str = Field(min_length=1, max_length=200)
    approver_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    review_at: IsoDatetime
    placed_at: IsoDatetime
    released_at: IsoDatetime | None


class LegalHoldPageResponse(ApiModel):
    items: list[LegalHoldResponse] = Field(max_length=200)
    page: PageResponse


async def _read(call: Any) -> Any:
    try:
        return await call
    except PermissionError as error:
        raise AccountProblem(403, "Forbidden") from error
    except ValueError as error:
        raise AccountProblem(400, "Invalid request") from error
    except SQLAlchemyError as error:
        raise AccountProblem(503, "Operations storage is unavailable") from error


def _event(value: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse.model_validate(
        {
            "id": value.id,
            "actor_id": value.actor_id,
            "action": value.action,
            "target_id": value.target_id,
            "reason": value.reason,
            "request_id": value.request_id,
            "occurred_at": value.occurred_at.isoformat(),
        }
    )


def _status(value: OperationsStatus) -> OperationsStatusResponse:
    return OperationsStatusResponse.model_validate(
        {
            "status": value.status,
            "checked_at": value.checked_at.isoformat(),
            "migration": {
                "current": value.migration_current,
                "expected": value.migration_expected,
                "ready": value.migration_current == value.migration_expected,
            },
            "database": {"ready": value.database_ready},
            "metering": {
                "ready": value.metering_ready,
                "last_heartbeat_at": value.last_consumer_heartbeat_at.isoformat()
                if value.last_consumer_heartbeat_at
                else None,
                "last_checkpoint_at": value.last_checkpoint_at.isoformat()
                if value.last_checkpoint_at
                else None,
            },
            "adapters": {"configured": value.adapter_count},
            "integrity": {
                "kill_switch": value.kill_switch,
                "latest_status": value.latest_projection_status,
                "last_checked_at": value.latest_projection_at.isoformat()
                if value.latest_projection_at
                else None,
            },
            "jobs": {"running": value.open_jobs, "failed_or_escalated": value.failed_jobs},
            "retention": {
                "open_legal_holds": value.open_legal_holds,
                "last_success_at": value.latest_retention_at.isoformat()
                if value.latest_retention_at
                else None,
            },
            "backup": {
                "latest_verified_at": value.latest_backup_verified_at.isoformat()
                if value.latest_backup_verified_at
                else None
            },
        }
    )


def _job(value: OperationsJob) -> OperationsJobResponse:
    return OperationsJobResponse.model_validate(
        {
            "id": value.id,
            "kind": value.kind,
            "mode": value.mode,
            "status": value.status,
            "initiator_id": value.initiator_id,
            "reason": value.reason,
            "parameters": value.parameters,
            "result": value.result,
            "started_at": value.started_at.isoformat(),
            "completed_at": value.completed_at.isoformat() if value.completed_at else None,
        }
    )


def _projection(value: ProjectionCheck) -> ProjectionCheckResponse:
    return ProjectionCheckResponse.model_validate(
        {
            "id": value.id,
            "job_id": value.job_id,
            "mode": value.mode,
            "status": value.status,
            "ledger_high_watermark": value.ledger_high_watermark,
            "lease_high_watermark": value.lease_high_watermark,
            "account_drift_count": value.account_drift_count,
            "global_drift": value.global_drift,
            "authoritative_violation_count": value.authoritative_violation_count,
            "shadow_sha256": value.shadow_sha256,
            "details": value.details,
            "created_at": value.created_at.isoformat(),
        }
    )


def _retention(value: RetentionRun) -> RetentionRunResponse:
    return RetentionRunResponse.model_validate(
        {
            "id": value.id,
            "job_id": value.job_id,
            "category": value.category,
            "mode": value.mode,
            "cutoff_at": value.cutoff_at.isoformat(),
            "high_watermark": value.high_watermark,
            "scanned_count": value.scanned_count,
            "deleted_count": value.deleted_count,
            "held_count": value.held_count,
            "created_at": value.created_at.isoformat(),
        }
    )


def _hold(value: LegalHold) -> LegalHoldResponse:
    return LegalHoldResponse.model_validate(
        {
            "id": value.id,
            "category": value.category,
            "scope_type": value.scope_type,
            "scope_id": value.scope_id,
            "approver_id": value.approver_id,
            "reason": value.reason,
            "review_at": value.review_at.isoformat(),
            "placed_at": value.placed_at.isoformat(),
            "released_at": value.released_at.isoformat() if value.released_at else None,
        }
    )


@router.get(
    "/audit-events",
    operation_id="listAuditEvents",
    status_code=status.HTTP_200_OK,
    response_model=AuditEventPageResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def list_audit_events(
    operations: Service,
    actor: Actor,
    response: Response,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditEventPageResponse:
    response.headers["Cache-Control"] = "no-store"
    values = list(await _read(operations.list_audit_events(actor, limit, cursor)))
    visible = values[:limit]
    next_cursor = None
    if len(values) > limit:
        last = visible[-1]
        next_cursor = encode_cursor(last.occurred_at, last.id)
    return AuditEventPageResponse(
        items=[_event(value) for value in visible], page=PageResponse(next_cursor=next_cursor)
    )


@router.get(
    "/adapters",
    operation_id="listAdapters",
    status_code=status.HTTP_200_OK,
    response_model=list[AdapterManifestResponse],
    response_model_exclude_none=True,
    responses=account_responses(403),
    openapi_extra=COOKIE_SECURITY,
)
async def list_adapters(
    operations: Service, actor: Actor, response: Response
) -> list[AdapterManifestResponse]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return [
            AdapterManifestResponse.model_validate(value)
            for value in operations.list_adapters(actor)
        ]
    except PermissionError as error:
        raise AccountProblem(403, "Forbidden") from error


@router.get(
    "/status",
    operation_id="getOperationsStatus",
    status_code=status.HTTP_200_OK,
    response_model=OperationsStatusResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def operations_status(
    operations: Service, actor: Actor, response: Response
) -> OperationsStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    return _status(await _read(operations.status(actor)))


@router.get(
    "/jobs",
    operation_id="listOperationsJobs",
    status_code=status.HTTP_200_OK,
    response_model=OperationsJobPageResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def list_jobs(
    operations: Service,
    actor: Actor,
    response: Response,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> OperationsJobPageResponse:
    response.headers["Cache-Control"] = "no-store"
    values = list(await _read(operations.list_jobs(actor, limit, cursor)))
    visible = values[:limit]
    next_cursor = None
    if len(values) > limit:
        next_cursor = encode_cursor(visible[-1].started_at, visible[-1].id)
    return OperationsJobPageResponse(
        items=[_job(value) for value in visible], page=PageResponse(next_cursor=next_cursor)
    )


@router.get(
    "/projection-checks",
    operation_id="listProjectionChecks",
    status_code=status.HTTP_200_OK,
    response_model=ProjectionCheckPageResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def list_projection_checks(
    operations: Service,
    actor: Actor,
    response: Response,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ProjectionCheckPageResponse:
    response.headers["Cache-Control"] = "no-store"
    values = list(await _read(operations.list_projection_checks(actor, limit, cursor)))
    visible = values[:limit]
    next_cursor = None
    if len(values) > limit:
        next_cursor = encode_cursor(visible[-1].created_at, visible[-1].id)
    return ProjectionCheckPageResponse(
        items=[_projection(value) for value in visible], page=PageResponse(next_cursor=next_cursor)
    )


@router.post(
    "/reconcile",
    operation_id="reconcileProjections",
    status_code=status.HTTP_200_OK,
    response_model=ProjectionCheckResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def reconcile_projections(
    body: ReconcileBody,
    operations: Service,
    actor: ProtectedActor,
    response: Response,
    idempotency_key: IdempotencyKey,
) -> ProjectionCheckResponse:
    response.headers["Cache-Control"] = "no-store"
    return _projection(
        await _read(
            operations.reconcile_projections(
                actor, body.mode, body.reason, idempotency_key, datetime.now(UTC)
            )
        )
    )


@router.post(
    "/retention",
    operation_id="runRetention",
    status_code=status.HTTP_200_OK,
    response_model=RetentionRunResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def run_retention(
    body: RetentionBody,
    operations: Service,
    actor: ProtectedActor,
    response: Response,
    idempotency_key: IdempotencyKey,
) -> RetentionRunResponse:
    response.headers["Cache-Control"] = "no-store"
    return _retention(
        await _read(
            operations.run_retention(
                actor,
                body.category,
                body.mode,
                body.cutoff_at,
                body.batch_size,
                body.reason,
                idempotency_key,
                datetime.now(UTC),
            )
        )
    )


@router.get(
    "/legal-holds",
    operation_id="listLegalHolds",
    status_code=status.HTTP_200_OK,
    response_model=LegalHoldPageResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def list_legal_holds(
    operations: Service,
    actor: Actor,
    response: Response,
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> LegalHoldPageResponse:
    response.headers["Cache-Control"] = "no-store"
    values = list(await _read(operations.list_legal_holds(actor, limit, cursor)))
    visible = values[:limit]
    next_cursor = None
    if len(values) > limit:
        next_cursor = encode_cursor(visible[-1].placed_at, visible[-1].id)
    return LegalHoldPageResponse(
        items=[_hold(value) for value in visible], page=PageResponse(next_cursor=next_cursor)
    )


@router.post(
    "/legal-holds",
    operation_id="placeLegalHold",
    status_code=status.HTTP_200_OK,
    response_model=LegalHoldResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def place_legal_hold(
    body: PlaceHoldBody,
    operations: Service,
    actor: ProtectedActor,
    response: Response,
) -> LegalHoldResponse:
    response.headers["Cache-Control"] = "no-store"
    return _hold(
        await _read(
            operations.place_legal_hold(
                actor,
                body.hold_id,
                body.category,
                body.scope_type,
                body.scope_id,
                body.reason,
                body.review_at,
                datetime.now(UTC),
            )
        )
    )


@router.post(
    "/legal-holds/{hold_id}/release",
    operation_id="releaseLegalHold",
    status_code=status.HTTP_200_OK,
    response_model=LegalHoldResponse,
    responses=account_responses(400, 403, 503),
    openapi_extra=COOKIE_SECURITY,
)
async def release_legal_hold(
    hold_id: Annotated[str, Path(min_length=1, max_length=200)],
    body: ReleaseHoldBody,
    operations: Service,
    actor: ProtectedActor,
    response: Response,
) -> LegalHoldResponse:
    response.headers["Cache-Control"] = "no-store"
    return _hold(
        await _read(operations.release_legal_hold(actor, hold_id, body.reason, datetime.now(UTC)))
    )
