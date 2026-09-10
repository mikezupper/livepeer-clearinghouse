"""Live PostgreSQL isolation, pagination, and audit immutability tests."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clearinghouse.application.operations import OperationsService
from clearinghouse.domain.accounts import PrincipalContext, PrincipalId, Role, TenantId
from clearinghouse.infrastructure.metering import encode_cursor
from clearinghouse.infrastructure.operations import (
    PostgresOperationsRepository,
    reference_adapter_manifest,
)

DATABASE_URL = os.getenv("CLEARINGHOUSE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="live PostgreSQL not configured")


async def test_postgres_audit_reads_are_scoped_paginated_and_immutable() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = PostgresOperationsRepository(
        sessions,
        [
            reference_adapter_manifest(
                [
                    {
                        "name": "identity",
                        "contract_version": "1.0",
                        "capabilities": ["email_otp"],
                    }
                ]
            )
        ],
    )
    service = OperationsService(repository)
    suffix = uuid4().hex
    tenant_a = f"tenant_ops_{suffix}"
    tenant_b = f"tenant_other_{suffix}"
    operator_id = f"principal_operator_{suffix}"
    admin_id = f"principal_admin_{suffix}"
    other_id = f"principal_other_{suffix}"
    now = datetime.now(UTC)
    event_a_new = f"audit_new_{suffix}"
    event_a_old = f"audit_old_{suffix}"
    event_b = f"audit_other_{suffix}"
    event_global = f"audit_global_{suffix}"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO tenants(id,display_name) VALUES "
                "(:tenant_a,'Operations tenant'),(:tenant_b,'Other tenant')"
            ),
            {"tenant_a": tenant_a, "tenant_b": tenant_b},
        )
        await connection.execute(
            text(
                "INSERT INTO principals(id,tenant_id) VALUES "
                "(:operator,NULL),(:admin,:tenant_a),(:other,:tenant_b)"
            ),
            {
                "operator": operator_id,
                "admin": admin_id,
                "other": other_id,
                "tenant_a": tenant_a,
                "tenant_b": tenant_b,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO principal_roles(principal_id,role) VALUES "
                "(:operator,'operator'),(:admin,'tenant_admin'),(:other,'tenant_admin')"
            ),
            {"operator": operator_id, "admin": admin_id, "other": other_id},
        )
        await connection.execute(
            text(
                "INSERT INTO audit_events"
                "(id,tenant_id,actor_id,action,target_id,reason,request_id,occurred_at) VALUES "
                "(:old,:tenant_a,:admin,'account.created','account_old','approved','request_old',:old_at),"
                "(:new,:tenant_a,:admin,'account.updated','account_new','approved','request_new',:new_at),"
                "(:other_event,:tenant_b,:other,'account.updated','account_other','approved','request_other',:new_at),"
                "(:global,NULL,:operator,'kill_switch.changed','global','incident','request_global',:new_at)"
            ),
            {
                "old": event_a_old,
                "new": event_a_new,
                "other_event": event_b,
                "global": event_global,
                "tenant_a": tenant_a,
                "tenant_b": tenant_b,
                "admin": admin_id,
                "other": other_id,
                "operator": operator_id,
                "old_at": now - timedelta(seconds=1),
                "new_at": now,
            },
        )

    operator = PrincipalContext(PrincipalId(operator_id), frozenset({Role.OPERATOR}))
    admin = PrincipalContext(
        PrincipalId(admin_id), frozenset({Role.TENANT_ADMIN}), TenantId(tenant_a)
    )
    operator_rows = await service.list_audit_events(operator, 200, None)
    returned_ids = {row.id for row in operator_rows}
    assert {event_a_new, event_a_old, event_b, event_global} <= returned_ids
    admin_rows = await service.list_audit_events(admin, 200, None)
    admin_ids = [row.id for row in admin_rows]
    assert event_a_new in admin_ids and event_a_old in admin_ids
    assert event_b not in admin_ids and event_global not in admin_ids
    page = await service.list_audit_events(admin, 10, encode_cursor(now, event_a_new))
    assert event_a_new not in {row.id for row in page}
    assert event_a_old in {row.id for row in page}
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE audit_events SET reason='tampered' WHERE id=:id"),
                {"id": event_a_new},
            )
    await engine.dispose()


async def test_operability_control_plane_is_idempotent_bounded_and_append_only() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    operator_id = f"principal_ops_control_{suffix}"
    group = f"ops-group-{suffix}"
    topic = f"ops-topic-{suffix}"
    signer = f"ops-signer-{suffix}"
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO principals(id) VALUES (:id)"), {"id": operator_id}
        )
        await connection.execute(
            text("INSERT INTO principal_roles(principal_id,role) VALUES (:id,'operator')"),
            {"id": operator_id},
        )
        await connection.execute(
            text("""INSERT INTO metering_worker_heartbeats(
              consumer_group,topic,signer_id,started_at,last_seen_at)
              VALUES (:group,:topic,:signer,:now,:now)"""),
            {"group": group, "topic": topic, "signer": signer, "now": now},
        )
        await connection.execute(
            text("""INSERT INTO auth_email_challenges(id,email_hash,code_hash,
              request_ip_hash,expires_at,attempt_count,max_attempts,created_at,key_id)
              VALUES (:id,:hash,:hash,:hash,:expires,0,3,:created,'legacy')"""),
            {
                "id": f"challenge_{suffix}",
                "hash": "a" * 64,
                "expires": now - timedelta(days=2),
                "created": now - timedelta(days=3),
            },
        )
        await connection.execute(
            text("""INSERT INTO auth_email_challenges(id,email_hash,code_hash,
              request_ip_hash,expires_at,attempt_count,max_attempts,created_at,key_id)
              VALUES (:id,:hash,:hash,:hash,:expires,0,3,:created,'active-key')"""),
            {
                "id": f"challenge_active_{suffix}",
                "hash": "b" * 64,
                "expires": now + timedelta(days=2),
                "created": now,
            },
        )
    repository = PostgresOperationsRepository(
        sessions,
        (),
        heartbeat_stale_seconds=60,
        consumer_group=group,
        topic=topic,
        signer_id=signer,
    )
    service = OperationsService(repository)
    operator = PrincipalContext(PrincipalId(operator_id), frozenset({Role.OPERATOR}))
    status = await service.status(operator)
    assert status.status == "ready" and status.metering_ready
    wrong_binding = PostgresOperationsRepository(sessions, (), consumer_group="wrong")
    assert (await wrong_binding.status("20260910_0008")).status == "degraded"

    check = await service.reconcile_projections(
        operator, "check", "scheduled projection verification", f"projection-{suffix}", now
    )
    replay = await service.reconcile_projections(
        operator, "check", "scheduled projection verification", f"projection-{suffix}", now
    )
    assert replay.id == check.id

    drift_tenant = f"tenant_projection_{suffix}"
    drift_account = f"account_projection_{suffix}"
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenants(id,display_name) VALUES (:id,'Projection tenant')"),
            {"id": drift_tenant},
        )
        await connection.execute(
            text("""INSERT INTO accounts(id,tenant_id,display_name,unit,exposure_cap)
              VALUES (:id,:tenant,'Projection account','wei',10)"""),
            {"id": drift_account, "tenant": drift_tenant},
        )
        await connection.execute(
            text("""INSERT INTO account_exposures(account_id,tenant_id,open_lease_exposure,unit)
              VALUES (:id,:tenant,0,'wei')"""),
            {"id": drift_account, "tenant": drift_tenant},
        )
    async with engine.begin() as connection:
        await connection.execute(text("ALTER TABLE account_exposures DISABLE TRIGGER USER"))
        await connection.execute(
            text("UPDATE account_exposures SET open_lease_exposure=1 WHERE account_id=:id"),
            {"id": drift_account},
        )
        await connection.execute(text("ALTER TABLE account_exposures ENABLE TRIGGER USER"))
    repaired = await service.reconcile_projections(
        operator,
        "repair",
        "repair verified projection cache only",
        f"projection-repair-{suffix}",
        now + timedelta(seconds=1),
    )
    assert repaired.status.value == "repaired" and repaired.account_drift_count >= 1
    async with engine.connect() as connection:
        assert (
            await connection.execute(
                text("SELECT open_lease_exposure FROM account_exposures WHERE account_id=:id"),
                {"id": drift_account},
            )
        ).scalar_one() == 0

    invalid_tx = f"ledger_tx_ops_invalid_{suffix}"
    invalid_posting = f"posting_ops_invalid_{suffix}"
    async with engine.begin() as connection:
        await connection.execute(text("ALTER TABLE ledger_transactions DISABLE TRIGGER USER"))
        await connection.execute(text("ALTER TABLE ledger_postings DISABLE TRIGGER USER"))
        await connection.execute(
            text("""INSERT INTO ledger_transactions(id,tenant_id,kind,source_id,actor_id)
              VALUES (:id,:tenant,'test','operability-adversarial',:actor)"""),
            {"id": invalid_tx, "tenant": drift_tenant, "actor": operator_id},
        )
        await connection.execute(
            text("""INSERT INTO ledger_postings(id,transaction_id,tenant_id,account_code,
              amount,unit) VALUES (:id,:tx,:tenant,:account,1,'wei')"""),
            {
                "id": invalid_posting,
                "tx": invalid_tx,
                "tenant": drift_tenant,
                "account": "payer:" + drift_account,
            },
        )
        await connection.execute(text("ALTER TABLE ledger_postings ENABLE TRIGGER USER"))
        await connection.execute(text("ALTER TABLE ledger_transactions ENABLE TRIGGER USER"))
    escalated = await service.reconcile_projections(
        operator,
        "repair",
        "fail closed on authoritative ledger drift",
        f"projection-escalate-{suffix}",
        now + timedelta(seconds=2),
    )
    assert escalated.status.value == "escalated"
    async with engine.connect() as connection:
        assert (
            await connection.execute(
                text("SELECT kill_switch FROM global_exposure WHERE singleton")
            )
        ).scalar_one()
    async with engine.begin() as connection:
        await connection.execute(text("ALTER TABLE ledger_transactions DISABLE TRIGGER USER"))
        await connection.execute(text("ALTER TABLE ledger_postings DISABLE TRIGGER USER"))
        await connection.execute(
            text("DELETE FROM ledger_postings WHERE transaction_id=:id"), {"id": invalid_tx}
        )
        await connection.execute(
            text("DELETE FROM ledger_transactions WHERE id=:id"), {"id": invalid_tx}
        )
        await connection.execute(text("ALTER TABLE ledger_postings ENABLE TRIGGER USER"))
        await connection.execute(text("ALTER TABLE ledger_transactions ENABLE TRIGGER USER"))
        await connection.execute(
            text("""UPDATE global_exposure SET kill_switch=false,
              reason='integration cleanup',changed_at=:now,actor_id=:actor WHERE singleton"""),
            {"now": now + timedelta(seconds=3), "actor": operator_id},
        )

    artifact = f"backup_{suffix}"
    created = await service.record_backup_event(
        operator,
        "created",
        artifact,
        "logical",
        "b" * 64,
        "c" * 64,
        "backup-key-1",
        "lsn:0/1",
        now + timedelta(days=30),
        "scheduled encrypted backup",
        f"backup-created-{suffix}",
        now,
    )
    repeated = await service.record_backup_event(
        operator,
        "created",
        artifact,
        "logical",
        "b" * 64,
        "c" * 64,
        "backup-key-1",
        "lsn:0/1",
        now + timedelta(days=30),
        "scheduled encrypted backup",
        f"backup-created-{suffix}",
        now,
    )
    assert repeated.id == created.id
    verified = await service.record_backup_event(
        operator,
        "verified",
        artifact,
        "logical",
        "b" * 64,
        "c" * 64,
        "backup-key-1",
        "lsn:0/1",
        now + timedelta(days=30),
        "isolated restore verified",
        f"backup-verified-{suffix}",
        now + timedelta(seconds=1),
    )
    assert verified.sequence == 2
    with pytest.raises(ValueError, match="idempotency"):
        await service.record_backup_event(
            operator,
            "verified",
            artifact,
            "logical",
            "b" * 64,
            "d" * 64,
            "backup-key-1",
            "lsn:0/1",
            now + timedelta(days=30),
            "changed request",
            f"backup-verified-{suffix}",
            now + timedelta(seconds=1),
        )

    rotation = f"rotation_{suffix}"
    started = await service.record_key_rotation(
        operator,
        rotation,
        "backup_encryption",
        "started",
        "backup-key-2",
        "backup-key-1",
        "begin bounded overlap",
        f"rotation-start-{suffix}",
        now,
    )
    activated = await service.record_key_rotation(
        operator,
        rotation,
        "backup_encryption",
        "activated",
        "backup-key-2",
        "backup-key-1",
        "new backup verified",
        f"rotation-active-{suffix}",
        now + timedelta(seconds=1),
    )
    assert started.sequence == 1 and activated.sequence == 2
    repeated_activation = await service.record_key_rotation(
        operator,
        rotation,
        "backup_encryption",
        "activated",
        "backup-key-2",
        "backup-key-1",
        "new backup verified",
        f"rotation-active-{suffix}",
        now + timedelta(seconds=1),
    )
    assert repeated_activation.id == activated.id
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO key_rotation_events(id,rotation_id,sequence,purpose,
                  action,key_id,initiator_id,reason,occurred_at)
                  VALUES (:id,:rotation,1,'backup_encryption','activated','bad-key',
                    :actor,'invalid direct activation',:now)"""),
                {
                    "id": f"rotation_event_invalid_{suffix}",
                    "rotation": f"rotation_invalid_{suffix}",
                    "actor": operator_id,
                    "now": now,
                },
            )

    dry_run = await service.run_retention(
        operator,
        "auth_ephemeral",
        "dry_run",
        now - timedelta(days=1),
        1,
        "preview expired challenges",
        f"retention-dry-{suffix}",
        now,
    )
    assert dry_run.scanned_count == 1 and dry_run.deleted_count == 0
    repeated_dry_run = await service.run_retention(
        operator,
        "auth_ephemeral",
        "dry_run",
        now - timedelta(days=1),
        1,
        "preview expired challenges",
        f"retention-dry-{suffix}",
        now,
    )
    assert repeated_dry_run.id == dry_run.id
    hold_id = f"hold_{suffix}"
    placed_hold = await service.place_legal_hold(
        operator,
        hold_id,
        "auth_ephemeral",
        "global",
        "global",
        "incident preservation",
        now + timedelta(days=30),
        now,
    )
    repeated_hold = await service.place_legal_hold(
        operator,
        hold_id,
        "auth_ephemeral",
        "global",
        "global",
        "incident preservation",
        now + timedelta(days=30),
        now,
    )
    assert repeated_hold == placed_hold
    held = await service.run_retention(
        operator,
        "auth_ephemeral",
        "apply",
        now - timedelta(days=1),
        1000,
        "hold-aware cleanup",
        f"retention-held-{suffix}",
        now,
    )
    assert held.deleted_count == 0 and held.held_count >= 1
    released = await service.release_legal_hold(
        operator, hold_id, "preservation complete", now + timedelta(seconds=2)
    )
    assert released.released_at is not None
    repeated_release = await service.release_legal_hold(
        operator, hold_id, "preservation complete", now + timedelta(seconds=2)
    )
    assert repeated_release == released
    applied = await service.run_retention(
        operator,
        "auth_ephemeral",
        "apply",
        now - timedelta(days=1),
        1000,
        "delete expired challenge",
        f"retention-apply-{suffix}",
        now + timedelta(seconds=3),
    )
    assert applied.deleted_count >= 1
    async with engine.connect() as connection:
        remaining = (
            await connection.execute(
                text("SELECT count(*) FROM auth_email_challenges WHERE id=:id"),
                {"id": f"challenge_{suffix}"},
            )
        ).scalar_one()
        purge_count = (
            await connection.execute(
                text("SELECT count(*) FROM secret_purge_events WHERE job_id=:job"),
                {"job": applied.job_id},
            )
        ).scalar_one()
        active_remaining = (
            await connection.execute(
                text("SELECT count(*) FROM auth_email_challenges WHERE id=:id"),
                {"id": f"challenge_active_{suffix}"},
            )
        ).scalar_one()
    assert remaining == 0 and purge_count == applied.deleted_count
    assert active_remaining == 1
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE backup_events SET reason='tampered' WHERE id=:id"),
                {"id": created.id},
            )
    await engine.dispose()
