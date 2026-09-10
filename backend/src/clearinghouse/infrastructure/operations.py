"""PostgreSQL audit reads and validated public adapter manifests."""

# ruff: noqa: E501, S608 -- fixed queries and readable SQL evidence clauses.

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clearinghouse import __version__
from clearinghouse.domain.accounts import PrincipalContext, Role
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
from clearinghouse.infrastructure.metering import _decode_cursor

_NAME = r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$"
_CAPABILITY = r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
_SEMVER = (
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_CONTRACT = r"^[1-9][0-9]*\.[0-9]+$"
_SENSITIVE = re.compile(r"(?:secret|password|token|api[-_]?key|credential|private[-_]?key)", re.I)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdapterPort(_StrictModel):
    name: Literal[
        "identity", "signer", "custody", "metering", "pricing", "collection", "events_out"
    ]
    contract_version: str = Field(pattern=_CONTRACT)
    capabilities: list[str] = Field(max_length=128)

    @model_validator(mode="after")
    def validate_capabilities(self) -> Self:
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("adapter capabilities must be unique")
        if any(
            len(value) > 128 or re.fullmatch(_CAPABILITY, value) is None
            for value in self.capabilities
        ):
            raise ValueError("invalid adapter capability")
        return self


class BuiltinSource(_StrictModel):
    selector: str = Field(max_length=128, pattern=_NAME)


class PythonSource(_StrictModel):
    group: Literal["livepeer.clearinghouse.adapters.v1"]
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    distribution: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"
    )


class HttpSource(_StrictModel):
    base_url: AnyHttpUrl
    authentication: Literal["bearer", "mtls"]
    timeout_ms: int = Field(ge=1, le=60000)

    @model_validator(mode="after")
    def reject_secret_bearing_url(self) -> Self:
        if (
            self.base_url.username is not None
            or self.base_url.password is not None
            or self.base_url.query is not None
            or self.base_url.fragment is not None
        ):
            raise ValueError("adapter URL cannot contain credentials, query, or fragment")
        return self


class AdapterManifest(_StrictModel):
    manifest_version: Literal["1.0"]
    name: str = Field(max_length=128, pattern=_NAME)
    version: str = Field(pattern=_SEMVER)
    description: str | None = Field(default=None, max_length=1024)
    source: Literal["builtin", "python_entry_point", "http_bridge"]
    builtin: BuiltinSource | None = None
    python_entry_point: PythonSource | None = None
    http_bridge: HttpSource | None = None
    ports: list[AdapterPort] = Field(min_length=1, max_length=7)
    configuration_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        sources = {
            "builtin": self.builtin,
            "python_entry_point": self.python_entry_point,
            "http_bridge": self.http_bridge,
        }
        if (
            sources[self.source] is None
            or sum(value is not None for value in sources.values()) != 1
        ):
            raise ValueError("exactly the declared adapter source is required")
        if len({port.name for port in self.ports}) != len(self.ports):
            raise ValueError("adapter port declarations must be unique")
        return self


def _redact_schema(value: object, *, sensitive: bool = False) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            child_sensitive = sensitive or bool(_SENSITIVE.search(key))
            if child_sensitive and key in {"default", "const", "examples", "enum"}:
                continue
            result[key] = _redact_schema(item, sensitive=child_sensitive)
        if sensitive:
            result["writeOnly"] = True
        return result
    if isinstance(value, list):
        return [_redact_schema(item, sensitive=sensitive) for item in value]
    return value


def reference_adapter_manifest(ports: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Describe only capabilities actually wired into this process."""
    return {
        "manifest_version": "1.0",
        "name": "reference_distribution",
        "version": __version__,
        "description": "Built-in Open Clearinghouse reference adapters active in this process.",
        "source": "builtin",
        "builtin": {"selector": "reference_distribution"},
        "ports": list(ports),
    }


class PostgresOperationsRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        manifests: Sequence[Mapping[str, object]],
        heartbeat_stale_seconds: int = 60,
        consumer_group: str = "clearinghouse-metering-v1",
        topic: str = "gateway-events",
        signer_id: str = "signer-reference",
    ) -> None:
        self.sessions = sessions
        self.heartbeat_stale_seconds = heartbeat_stale_seconds
        self.consumer_group = consumer_group
        self.topic = topic
        self.signer_id = signer_id
        validated: list[str] = []
        for manifest in manifests:
            model = AdapterManifest.model_validate(manifest)
            public = model.model_dump(mode="json", exclude_none=True)
            if model.configuration_schema is not None:
                public["configuration_schema"] = _redact_schema(model.configuration_schema)
            validated.append(json.dumps(public, sort_keys=True, separators=(",", ":")))
        self._manifests = tuple(validated)

    def list_adapters(self) -> Sequence[Mapping[str, object]]:
        return tuple(json.loads(value) for value in self._manifests)

    async def list_audit_events(
        self, actor: PrincipalContext, limit: int, cursor: str | None
    ) -> Sequence[AuditEvent]:
        if not actor.is_operator and (
            Role.TENANT_ADMIN not in actor.roles or actor.tenant_id is None
        ):
            raise PermissionError("operator or tenant administrator role required")
        params: dict[str, object] = {"limit": limit + 1}
        predicates: list[str] = []
        if not actor.is_operator:
            params["tenant_id"] = str(actor.tenant_id)
            predicates.append("tenant_id = :tenant_id")
        if cursor is not None:
            cursor_time, cursor_id = _decode_cursor(cursor)
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
            predicates.append("(occurred_at,id) < (:cursor_time,:cursor_id)")
        where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(f"""
                    SELECT id,tenant_id,actor_id,action,target_id,reason,request_id,occurred_at
                    FROM audit_events {where}
                    ORDER BY occurred_at DESC,id DESC LIMIT :limit
                    """),
                    params,
                )
            ).mappings()
            return [AuditEvent(**dict(row)) for row in rows]

    async def status(self, expected_migration: str) -> OperationsStatus:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    text("""
                    SELECT
                      (SELECT version_num FROM alembic_version LIMIT 1) migration_current,
                      (SELECT kill_switch FROM global_exposure WHERE singleton) kill_switch,
                      (SELECT count(*) FROM operations_jobs WHERE status='running') open_jobs,
                      (SELECT count(*) FROM operations_jobs WHERE status IN ('failed','escalated')) failed_jobs,
                      (SELECT count(*) FROM (SELECT DISTINCT ON (hold_id) hold_id,action
                         FROM legal_hold_events ORDER BY hold_id,sequence DESC) h
                         WHERE h.action='placed') open_holds,
                      (SELECT max(occurred_at) FROM backup_events WHERE action IN ('verified','restore_verified')) backup_at,
                      (SELECT max(created_at) FROM retention_runs WHERE mode='apply') retention_at,
                      (SELECT status FROM projection_checks ORDER BY created_at DESC,id DESC LIMIT 1) projection_status,
                      (SELECT created_at FROM projection_checks ORDER BY created_at DESC,id DESC LIMIT 1) projection_at,
                      (SELECT max(last_seen_at) FROM metering_worker_heartbeats
                        WHERE consumer_group=:consumer_group AND topic=:topic
                          AND signer_id=:signer_id) heartbeat_at,
                      (SELECT max(updated_at) FROM metering_checkpoints
                        WHERE consumer_group=:consumer_group AND topic=:topic) checkpoint_at,
                      now() checked_at
                    """),
                    {
                        "consumer_group": self.consumer_group,
                        "topic": self.topic,
                        "signer_id": self.signer_id,
                    },
                )
            ).one()
        metering_ready = (
            row.heartbeat_at is not None
            and (row.checked_at - row.heartbeat_at).total_seconds() <= self.heartbeat_stale_seconds
        )
        ready = row.migration_current == expected_migration and metering_ready
        return OperationsStatus(
            status="ready" if ready else "degraded",
            checked_at=row.checked_at,
            migration_current=row.migration_current,
            migration_expected=expected_migration,
            database_ready=True,
            metering_ready=metering_ready,
            last_consumer_heartbeat_at=row.heartbeat_at,
            last_checkpoint_at=row.checkpoint_at,
            adapter_count=len(self._manifests),
            kill_switch=row.kill_switch,
            open_jobs=row.open_jobs,
            failed_jobs=row.failed_jobs,
            open_legal_holds=row.open_holds,
            latest_backup_verified_at=row.backup_at,
            latest_retention_at=row.retention_at,
            latest_projection_status=(
                ProjectionStatus(row.projection_status) if row.projection_status else None
            ),
            latest_projection_at=row.projection_at,
        )

    @staticmethod
    def _request_hash(value: Mapping[str, object]) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()

    async def _existing_job(
        self,
        session: AsyncSession,
        actor: PrincipalContext,
        idempotency_key: str,
        request_hash: str,
    ) -> Mapping[str, Any] | None:
        row = (
            (
                await session.execute(
                    text("""SELECT * FROM operations_jobs
                  WHERE initiator_id=:actor AND idempotency_key=:key FOR UPDATE"""),
                    {"actor": str(actor.id), "key": idempotency_key},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None and row["request_sha256"] != request_hash:
            raise ValueError("idempotency key was already used for a different request")
        return dict(row) if row is not None else None

    @staticmethod
    def _projection(row: Mapping[str, Any]) -> ProjectionCheck:
        return ProjectionCheck(
            id=row["id"],
            job_id=row["job_id"],
            mode=row["mode"],
            status=ProjectionStatus(row["status"]),
            ledger_high_watermark=row["ledger_high_watermark"],
            lease_high_watermark=row["lease_high_watermark"],
            account_drift_count=row["account_drift_count"],
            global_drift=row["global_drift"],
            authoritative_violation_count=row["authoritative_violation_count"],
            shadow_sha256=row["shadow_sha256"],
            details=dict(row["details"]),
            created_at=row["created_at"],
        )

    async def reconcile_projections(
        self,
        actor: PrincipalContext,
        mode: str,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> ProjectionCheck:
        request = {"kind": "projection_reconciliation", "mode": mode, "reason": reason}
        request_hash = self._request_hash(request)
        async with self.sessions.begin() as session:
            await session.execute(text("SET LOCAL statement_timeout = '30s'"))
            await session.execute(text("SELECT pg_advisory_xact_lock(730018)"))
            existing = await self._existing_job(session, actor, idempotency_key, request_hash)
            if existing is not None:
                row = (
                    (
                        await session.execute(
                            text("SELECT * FROM projection_checks WHERE job_id=:job"),
                            {"job": existing["id"]},
                        )
                    )
                    .mappings()
                    .one()
                )
                return self._projection(dict(row))
            job_id = f"job_{uuid4().hex}"
            await session.execute(
                text("""INSERT INTO operations_jobs(id,kind,mode,status,initiator_id,
                  idempotency_key,request_sha256,reason,parameters,started_at)
                  VALUES (:id,'projection_reconciliation',:mode,'running',:actor,:key,:hash,
                    :reason,CAST(:parameters AS jsonb),:now)"""),
                {
                    "id": job_id,
                    "mode": mode,
                    "actor": str(actor.id),
                    "key": idempotency_key,
                    "hash": request_hash,
                    "reason": reason,
                    "parameters": json.dumps({"mode": mode}),
                    "now": now,
                },
            )
            await session.execute(text("SELECT * FROM global_exposure WHERE singleton FOR UPDATE"))
            account_count = (
                await session.execute(text("SELECT count(*) FROM accounts"))
            ).scalar_one()
            if account_count > 100_000:
                raise ValueError("projection reconciliation exceeds the supported account bound")
            await session.execute(text("SELECT id FROM accounts ORDER BY id FOR UPDATE"))
            await session.execute(
                text("SELECT account_id FROM account_exposures ORDER BY account_id FOR UPDATE")
            )
            rows = (
                (
                    await session.execute(
                        text("""
                    SELECT a.id account_id,a.unit,a.exposure_cap,e.open_lease_exposure current_exposure,
                      COALESCE((SELECT sum(l.available+l.pending) FROM leases l
                        WHERE l.account_id=a.id),0) expected_exposure,
                      COALESCE((SELECT sum(p.amount) FROM ledger_postings p
                        WHERE p.tenant_id=a.tenant_id AND p.account_code='payer:'||a.id
                          AND p.unit=a.unit),0) posted_balance
                    FROM accounts a JOIN account_exposures e ON e.account_id=a.id
                    ORDER BY a.id
                    """)
                    )
                )
                .mappings()
                .all()
            )
            global_row = (
                await session.execute(
                    text("SELECT exposure_cap,open_exposure FROM global_exposure WHERE singleton")
                )
            ).one()
            expected_global = sum(int(row["expected_exposure"]) for row in rows)
            drift = [row for row in rows if row["current_exposure"] != row["expected_exposure"]]
            global_drift = int(global_row.open_exposure) != expected_global
            violations = [
                row
                for row in rows
                if int(row["expected_exposure"])
                > min(int(row["exposure_cap"]), int(row["posted_balance"]))
            ]
            ledger_bad = (
                await session.execute(
                    text("""SELECT count(*) FROM (SELECT transaction_id,unit
                      FROM ledger_postings GROUP BY transaction_id,unit
                      HAVING count(*)<2 OR sum(amount)<>0) invalid""")
                )
            ).scalar_one()
            violation_count = len(violations) + int(ledger_bad)
            ledger_high = (
                await session.execute(
                    text(
                        "SELECT COALESCE(max(created_at::text||':'||id),'empty') FROM ledger_transactions"
                    )
                )
            ).scalar_one()
            lease_high = (
                await session.execute(
                    text("SELECT COALESCE(max(created_at::text||':'||id),'empty') FROM leases")
                )
            ).scalar_one()
            shadow = [
                {"account_id": row["account_id"], "exposure": str(row["expected_exposure"])}
                for row in rows
            ]
            shadow_hash = hashlib.sha256(
                json.dumps(shadow, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            status = ProjectionStatus.CONSISTENT
            if violation_count:
                status = ProjectionStatus.ESCALATED
                await session.execute(
                    text("""UPDATE global_exposure SET kill_switch=true,
                      reason=:reason,changed_at=:now,actor_id=:actor WHERE singleton"""),
                    {
                        "reason": "projection reconciliation found authoritative drift: " + reason,
                        "now": now,
                        "actor": str(actor.id),
                    },
                )
            elif drift or global_drift:
                status = ProjectionStatus.DRIFT
                if mode == "repair":
                    for row in drift:
                        await session.execute(
                            text("""UPDATE account_exposures SET open_lease_exposure=:value
                              WHERE account_id=:account"""),
                            {"value": row["expected_exposure"], "account": row["account_id"]},
                        )
                    if global_drift:
                        await session.execute(
                            text("UPDATE global_exposure SET open_exposure=:value WHERE singleton"),
                            {"value": expected_global},
                        )
                    status = ProjectionStatus.REPAIRED
            check_id = f"projection_{uuid4().hex}"
            details = {
                "account_ids": [row["account_id"] for row in drift][:1000],
                "expected_global_exposure": str(expected_global),
                "violating_account_ids": [row["account_id"] for row in violations][:1000],
            }
            await session.execute(
                text("""INSERT INTO projection_checks(id,job_id,mode,status,
                  ledger_high_watermark,lease_high_watermark,account_drift_count,global_drift,
                  authoritative_violation_count,shadow_sha256,details,created_at)
                  VALUES (:id,:job,:mode,:status,:ledger,:lease,:drift,:global_drift,
                    :violations,:shadow,CAST(:details AS jsonb),:now)"""),
                {
                    "id": check_id,
                    "job": job_id,
                    "mode": mode,
                    "status": status.value,
                    "ledger": ledger_high,
                    "lease": lease_high,
                    "drift": len(drift),
                    "global_drift": global_drift,
                    "violations": violation_count,
                    "shadow": shadow_hash,
                    "details": json.dumps(details),
                    "now": now,
                },
            )
            job_status = "escalated" if status is ProjectionStatus.ESCALATED else "succeeded"
            result = {"projection_check_id": check_id, "status": status.value}
            await session.execute(
                text("""UPDATE operations_jobs SET status=:status,result=CAST(:result AS jsonb),
                  completed_at=:now WHERE id=:id AND status='running'"""),
                {"status": job_status, "result": json.dumps(result), "now": now, "id": job_id},
            )
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM projection_checks WHERE id=:id"), {"id": check_id}
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(row))

    @staticmethod
    def _retention(row: Mapping[str, Any]) -> RetentionRun:
        return RetentionRun(**dict(row))

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
        request = {
            "kind": "retention",
            "category": category,
            "mode": mode,
            "cutoff_at": cutoff_at.isoformat(),
            "batch_size": batch_size,
            "reason": reason,
        }
        request_hash = self._request_hash(request)
        async with self.sessions.begin() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(730019)"))
            existing = await self._existing_job(session, actor, idempotency_key, request_hash)
            if existing is not None:
                row = (
                    (
                        await session.execute(
                            text("SELECT * FROM retention_runs WHERE job_id=:job"),
                            {"job": existing["id"]},
                        )
                    )
                    .mappings()
                    .one()
                )
                return self._retention(dict(row))
            job_id = f"job_{uuid4().hex}"
            await session.execute(
                text("""INSERT INTO operations_jobs(id,kind,mode,status,initiator_id,
                  idempotency_key,request_sha256,reason,parameters,started_at)
                  VALUES (:id,'retention',:mode,'running',:actor,:key,:hash,:reason,
                    CAST(:parameters AS jsonb),:now)"""),
                {
                    "id": job_id,
                    "mode": mode,
                    "actor": str(actor.id),
                    "key": idempotency_key,
                    "hash": request_hash,
                    "reason": reason,
                    "parameters": json.dumps(request),
                    "now": now,
                },
            )
            held = (
                await session.execute(
                    text("""SELECT EXISTS(SELECT 1 FROM (
                      SELECT DISTINCT ON (hold_id) category,action FROM legal_hold_events
                      ORDER BY hold_id,sequence DESC) h WHERE h.action='placed'
                        AND h.category=:category)"""),
                    {"category": category},
                )
            ).scalar_one()
            table, predicate, ordering = {
                "auth_ephemeral": (
                    "auth_email_challenges",
                    "expires_at<:cutoff",
                    "created_at,id",
                ),
                "browser_sessions": (
                    "auth_browser_sessions",
                    "expires_at<:cutoff OR (revoked_at IS NOT NULL AND revoked_at<:cutoff)",
                    "created_at,id",
                ),
                "operational_detail": (
                    "metering_worker_heartbeats",
                    "last_seen_at<:cutoff",
                    "last_seen_at,consumer_group,topic,signer_id",
                ),
            }[category]
            candidates = (
                (
                    await session.execute(
                        text(f"""SELECT * FROM {table} WHERE {predicate}
                      ORDER BY {ordering} LIMIT :limit FOR UPDATE SKIP LOCKED"""),
                        {"cutoff": cutoff_at, "limit": batch_size},
                    )
                )
                .mappings()
                .all()
            )
            deleted = 0
            if mode == "apply" and not held:
                for candidate in candidates:
                    target_id = str(candidate.get("id") or "|".join(map(str, candidate.values())))
                    if table in {"auth_email_challenges", "auth_browser_sessions"}:
                        await session.execute(
                            text("""INSERT INTO secret_purge_events(id,job_id,target_table,
                              target_id_sha256,former_key_id,reason,occurred_at)
                              VALUES (:id,:job,:table,:target,:key_id,:reason,:now)"""),
                            {
                                "id": f"purge_{uuid4().hex}",
                                "job": job_id,
                                "table": table,
                                "target": hashlib.sha256(target_id.encode()).hexdigest(),
                                "key_id": candidate["key_id"],
                                "reason": reason,
                                "now": now,
                            },
                        )
                    if table == "metering_worker_heartbeats":
                        await session.execute(
                            text("""DELETE FROM metering_worker_heartbeats WHERE
                              consumer_group=:consumer_group AND topic=:topic AND signer_id=:signer_id"""),
                            dict(candidate),
                        )
                    else:
                        await session.execute(
                            text(f"DELETE FROM {table} WHERE id=:id"), {"id": candidate["id"]}
                        )
                    deleted += 1
            run_id = f"retention_{uuid4().hex}"
            watermark = str(candidates[-1].get("id", "empty")) if candidates else "empty"
            held_count = len(candidates) if held else 0
            await session.execute(
                text("""INSERT INTO retention_runs(id,job_id,category,mode,cutoff_at,
                  high_watermark,scanned_count,deleted_count,held_count,created_at)
                  VALUES (:id,:job,:category,:mode,:cutoff,:watermark,:scanned,:deleted,:held,:now)"""),
                {
                    "id": run_id,
                    "job": job_id,
                    "category": category,
                    "mode": mode,
                    "cutoff": cutoff_at,
                    "watermark": watermark,
                    "scanned": len(candidates),
                    "deleted": deleted,
                    "held": held_count,
                    "now": now,
                },
            )
            result = {"retention_run_id": run_id, "deleted_count": deleted}
            await session.execute(
                text("""UPDATE operations_jobs SET status='succeeded',
                  result=CAST(:result AS jsonb),completed_at=:now WHERE id=:job"""),
                {"result": json.dumps(result), "now": now, "job": job_id},
            )
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM retention_runs WHERE id=:id"), {"id": run_id}
                    )
                )
                .mappings()
                .one()
            )
            return self._retention(dict(row))

    @staticmethod
    def _job(row: Mapping[str, Any]) -> OperationsJob:
        return OperationsJob(
            id=row["id"],
            kind=row["kind"],
            mode=row["mode"],
            status=JobStatus(row["status"]),
            initiator_id=row["initiator_id"],
            reason=row["reason"],
            parameters=dict(row["parameters"]),
            result=dict(row["result"]) if row["result"] is not None else None,
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    async def _paged(
        self, table: str, limit: int, cursor: str | None
    ) -> Sequence[Mapping[str, Any]]:
        params: dict[str, object] = {"limit": limit + 1}
        where = ""
        if cursor is not None:
            cursor_time, cursor_id = _decode_cursor(cursor)
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
            column = "started_at" if table == "operations_jobs" else "created_at"
            where = f"WHERE ({column},id)<(:cursor_time,:cursor_id)"
        column = "started_at" if table == "operations_jobs" else "created_at"
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            f"SELECT * FROM {table} {where} ORDER BY {column} DESC,id DESC LIMIT :limit"
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    async def list_jobs(self, limit: int, cursor: str | None) -> Sequence[OperationsJob]:
        return [self._job(row) for row in await self._paged("operations_jobs", limit, cursor)]

    async def list_projection_checks(
        self, limit: int, cursor: str | None
    ) -> Sequence[ProjectionCheck]:
        return [
            self._projection(row) for row in await self._paged("projection_checks", limit, cursor)
        ]

    @staticmethod
    def _hold(row: Mapping[str, Any]) -> LegalHold:
        return LegalHold(
            id=row["hold_id"],
            category=row["category"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            approver_id=row["approver_id"],
            reason=row["placed_reason"],
            review_at=row["review_at"],
            placed_at=row["placed_at"],
            released_at=row["released_at"],
        )

    async def _legal_hold(self, hold_id: str) -> tuple[LegalHold, str | None] | None:
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        text("""SELECT p.hold_id,p.category,p.scope_type,p.scope_id,
                          p.approver_id,p.reason placed_reason,p.review_at,
                          p.occurred_at placed_at,r.reason released_reason,
                          r.occurred_at released_at
                        FROM legal_hold_events p LEFT JOIN legal_hold_events r
                          ON r.hold_id=p.hold_id AND r.action='released'
                        WHERE p.hold_id=:id AND p.action='placed'"""),
                        {"id": hold_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        mapped = dict(row)
        return self._hold(mapped), mapped["released_reason"]

    async def list_legal_holds(self, limit: int, cursor: str | None) -> Sequence[LegalHold]:
        params: dict[str, object] = {"limit": limit + 1}
        predicates = ["p.action='placed'"]
        if cursor is not None:
            cursor_time, cursor_id = _decode_cursor(cursor)
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
            predicates.append("(p.occurred_at,p.hold_id)<(:cursor_time,:cursor_id)")
        where = "WHERE " + " AND ".join(predicates)
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        text(f"""SELECT p.hold_id,p.category,p.scope_type,p.scope_id,
                      p.approver_id,p.reason placed_reason,p.review_at,p.occurred_at placed_at,
                      r.occurred_at released_at
                    FROM legal_hold_events p LEFT JOIN legal_hold_events r
                      ON r.hold_id=p.hold_id AND r.action='released'
                    {where}
                    ORDER BY p.occurred_at DESC,p.hold_id DESC LIMIT :limit"""),
                        params,
                    )
                )
                .mappings()
                .all()
            )
        return [self._hold(dict(row)) for row in rows]

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
        async with self.sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:id))"), {"id": hold_id}
            )
            existing = (
                (
                    await session.execute(
                        text("""SELECT p.hold_id,p.category,p.scope_type,p.scope_id,
                          p.approver_id,p.reason placed_reason,p.review_at,
                          p.occurred_at placed_at,r.occurred_at released_at
                        FROM legal_hold_events p LEFT JOIN legal_hold_events r
                          ON r.hold_id=p.hold_id AND r.action='released'
                        WHERE p.hold_id=:id AND p.action='placed'"""),
                        {"id": hold_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                match = self._hold(dict(existing))
                if (
                    match.category,
                    match.scope_type,
                    match.scope_id,
                    match.reason,
                    match.review_at,
                ) != (category, scope_type, scope_id, reason, review_at):
                    raise ValueError("legal hold identifier was reused for different input")
                return match
            await session.execute(
                text("""INSERT INTO legal_hold_events(id,hold_id,sequence,action,category,
                  scope_type,scope_id,approver_id,reason,review_at,occurred_at)
                  VALUES (:event,:hold,1,'placed',:category,:scope,:scope_id,:actor,:reason,
                    :review,:now) ON CONFLICT (hold_id,sequence) DO NOTHING"""),
                {
                    "event": f"hold_event_{uuid4().hex}",
                    "hold": hold_id,
                    "category": category,
                    "scope": scope_type,
                    "scope_id": scope_id,
                    "actor": str(actor.id),
                    "reason": reason,
                    "review": review_at,
                    "now": now,
                },
            )
        found = await self._legal_hold(hold_id)
        if found is None:
            raise ValueError("legal hold already exists or is not visible")
        match, _ = found
        if (
            match.category,
            match.scope_type,
            match.scope_id,
            match.reason,
            match.review_at,
        ) != (category, scope_type, scope_id, reason, review_at):
            raise ValueError("legal hold identifier was reused for different input")
        return match

    async def release_legal_hold(
        self, actor: PrincipalContext, hold_id: str, reason: str, now: datetime
    ) -> LegalHold:
        async with self.sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:id))"), {"id": hold_id}
            )
            placed = (
                (
                    await session.execute(
                        text("""SELECT * FROM legal_hold_events WHERE hold_id=:id
                      ORDER BY sequence DESC LIMIT 1 FOR UPDATE"""),
                        {"id": hold_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if placed is None:
                raise ValueError("legal hold is not active")
            if placed["action"] == "released":
                found = await self._legal_hold(hold_id)
                if found is not None and found[1] == reason:
                    return found[0]
                raise ValueError("legal hold is not active")
            await session.execute(
                text("""INSERT INTO legal_hold_events(id,hold_id,sequence,action,category,
                  scope_type,scope_id,approver_id,reason,review_at,occurred_at)
                  VALUES (:event,:hold,:sequence,'released',:category,:scope,:scope_id,:actor,
                    :reason,:review,:now)"""),
                {
                    "event": f"hold_event_{uuid4().hex}",
                    "hold": hold_id,
                    "sequence": int(placed["sequence"]) + 1,
                    "category": placed["category"],
                    "scope": placed["scope_type"],
                    "scope_id": placed["scope_id"],
                    "actor": str(actor.id),
                    "reason": reason,
                    "review": placed["review_at"],
                    "now": now,
                },
            )
        found = await self._legal_hold(hold_id)
        if found is None:
            raise ValueError("legal hold is not visible")
        return found[0]

    @staticmethod
    def _backup(row: Mapping[str, Any]) -> BackupEvent:
        return BackupEvent(**dict(row))

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
        request = {
            "kind": "backup",
            "action": action,
            "artifact_id": artifact_id,
            "backup_type": backup_type,
            "location_sha256": location_sha256,
            "checksum_sha256": checksum_sha256,
            "key_id": key_id,
            "high_watermark": high_watermark,
            "retention_until": retention_until.isoformat(),
            "reason": reason,
        }
        request_hash = self._request_hash(request)
        async with self.sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:id))"),
                {"id": "backup:" + artifact_id},
            )
            existing = await self._existing_job(session, actor, idempotency_key, request_hash)
            if existing is not None:
                event_id = existing["result"]["backup_event_id"]
                row = (
                    (
                        await session.execute(
                            text("SELECT * FROM backup_events WHERE id=:id"), {"id": event_id}
                        )
                    )
                    .mappings()
                    .one()
                )
                return self._backup(dict(row))
            job_id = f"job_{uuid4().hex}"
            event_id = f"backup_event_{uuid4().hex}"
            sequence = (
                await session.execute(
                    text(
                        "SELECT COALESCE(max(sequence),0)+1 FROM backup_events WHERE artifact_id=:id"
                    ),
                    {"id": artifact_id},
                )
            ).scalar_one()
            await session.execute(
                text("""INSERT INTO operations_jobs(id,kind,mode,status,initiator_id,
                  idempotency_key,request_sha256,reason,parameters,started_at)
                  VALUES (:id,'backup',:mode,'running',:actor,:key,:hash,:reason,
                    CAST(:parameters AS jsonb),:now)"""),
                {
                    "id": job_id,
                    "mode": "verify" if action in {"verified", "restore_verified"} else "record",
                    "actor": str(actor.id),
                    "key": idempotency_key,
                    "hash": request_hash,
                    "reason": reason,
                    "parameters": json.dumps(request),
                    "now": now,
                },
            )
            await session.execute(
                text("""INSERT INTO backup_events(id,artifact_id,sequence,action,backup_type,
                  location_sha256,checksum_sha256,key_id,high_watermark,retention_until,
                  initiator_id,reason,occurred_at) VALUES (:id,:artifact,:sequence,:action,
                  :backup_type,:location,:checksum,:key_id,:high_watermark,:retention_until,
                  :actor,:reason,:now)"""),
                {
                    "id": event_id,
                    "artifact": artifact_id,
                    "sequence": sequence,
                    "action": action,
                    "backup_type": backup_type,
                    "location": location_sha256,
                    "checksum": checksum_sha256,
                    "key_id": key_id,
                    "high_watermark": high_watermark,
                    "retention_until": retention_until,
                    "actor": str(actor.id),
                    "reason": reason,
                    "now": now,
                },
            )
            result = {"backup_event_id": event_id, "action": action}
            await session.execute(
                text("""UPDATE operations_jobs SET status='succeeded',
                  result=CAST(:result AS jsonb),completed_at=:now WHERE id=:id"""),
                {"result": json.dumps(result), "now": now, "id": job_id},
            )
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM backup_events WHERE id=:id"), {"id": event_id}
                    )
                )
                .mappings()
                .one()
            )
        return self._backup(dict(row))

    @staticmethod
    def _rotation(row: Mapping[str, Any]) -> KeyRotationEvent:
        return KeyRotationEvent(**dict(row))

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
        request = {
            "kind": "key_rotation",
            "rotation_id": rotation_id,
            "purpose": purpose,
            "action": action,
            "key_id": key_id,
            "prior_key_id": prior_key_id,
            "reason": reason,
        }
        request_hash = self._request_hash(request)
        async with self.sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:id))"),
                {"id": "rotation:" + rotation_id},
            )
            existing = await self._existing_job(session, actor, idempotency_key, request_hash)
            if existing is not None:
                event_id = existing["result"]["key_rotation_event_id"]
                row = (
                    (
                        await session.execute(
                            text("SELECT * FROM key_rotation_events WHERE id=:id"),
                            {"id": event_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                return self._rotation(dict(row))
            job_id = f"job_{uuid4().hex}"
            event_id = f"rotation_event_{uuid4().hex}"
            sequence = (
                await session.execute(
                    text(
                        "SELECT COALESCE(max(sequence),0)+1 FROM key_rotation_events WHERE rotation_id=:id"
                    ),
                    {"id": rotation_id},
                )
            ).scalar_one()
            await session.execute(
                text("""INSERT INTO operations_jobs(id,kind,mode,status,initiator_id,
                  idempotency_key,request_sha256,reason,parameters,started_at)
                  VALUES (:id,'key_rotation','record','running',:actor,:key,:hash,:reason,
                    CAST(:parameters AS jsonb),:now)"""),
                {
                    "id": job_id,
                    "actor": str(actor.id),
                    "key": idempotency_key,
                    "hash": request_hash,
                    "reason": reason,
                    "parameters": json.dumps(request),
                    "now": now,
                },
            )
            await session.execute(
                text("""INSERT INTO key_rotation_events(id,rotation_id,sequence,purpose,
                  action,key_id,prior_key_id,initiator_id,reason,occurred_at)
                  VALUES (:id,:rotation,:sequence,:purpose,:action,:key_id,:prior_key_id,
                    :actor,:reason,:now)"""),
                {
                    "id": event_id,
                    "rotation": rotation_id,
                    "sequence": sequence,
                    "purpose": purpose,
                    "action": action,
                    "key_id": key_id,
                    "prior_key_id": prior_key_id,
                    "actor": str(actor.id),
                    "reason": reason,
                    "now": now,
                },
            )
            result = {"key_rotation_event_id": event_id, "action": action}
            await session.execute(
                text("""UPDATE operations_jobs SET status='succeeded',
                  result=CAST(:result AS jsonb),completed_at=:now WHERE id=:id"""),
                {"result": json.dumps(result), "now": now, "id": job_id},
            )
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM key_rotation_events WHERE id=:id"), {"id": event_id}
                    )
                )
                .mappings()
                .one()
            )
        return self._rotation(dict(row))
