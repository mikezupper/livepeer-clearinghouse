"""Live PostgreSQL signer admission, replay, exposure, and invariant tests."""

# ruff: noqa: E501 -- SQL invariants are clearer as complete statements.

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clearinghouse.application.accounts import AccountService
from clearinghouse.application.signer import SignerConflict, SignerService
from clearinghouse.domain.accounts import (
    AccountId,
    ExactRate,
    GrantKind,
    PrincipalContext,
    PrincipalId,
    Role,
    TenantId,
)
from clearinghouse.domain.signer import DenialReason, PaymentState, SignerSession
from clearinghouse.infrastructure.accounts import PostgresAccountsRepository
from clearinghouse.infrastructure.signer import PostgresSignerRepository

DATABASE_URL = os.getenv("CLEARINGHOUSE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="live PostgreSQL not configured")
NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def state(
    state_id: str,
    *,
    sequence: int = 0,
    auth_id: str = "",
    nonce: int = 1,
    when: str = "2026-09-09T12:00:00.000000001Z",
) -> PaymentState:
    return PaymentState(
        state_id,
        f"pm_{state_id}",
        when,
        "0x" + "a" * 40,
        "preflight",
        0,
        nonce,
        "0",
        1,
        1,
        "live",
        sequence,
        auth_id,
    )


async def test_postgres_signer_admission_concurrency_and_invariants() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    accounts = AccountService(PostgresAccountsRepository(engine), b"integration-credential-pepper")
    operator_id = f"principal_operator_{uuid4().hex}"
    operator = PrincipalContext(PrincipalId(operator_id), frozenset({Role.OPERATOR}))
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO principals(id) VALUES (:id)"), {"id": operator_id}
        )
        await connection.execute(
            text("INSERT INTO principal_roles(principal_id,role) VALUES (:id,'operator')"),
            {"id": operator_id},
        )
    suffix = uuid4().hex
    tenant = await accounts.create_tenant(f"Signer {suffix}", operator)
    account = await accounts.create_account(tenant.id, "Signer account", "wei", 200, operator)
    principal = await accounts.create_principal(
        tenant.id, account.id, "Holder", frozenset({Role.CREDENTIAL_HOLDER}), operator
    )
    actor = PrincipalContext(
        PrincipalId(principal.id),
        principal.roles,
        tenant_id=TenantId(tenant.id),
        account_id=AccountId(account.id),
    )
    await accounts.create_grant(
        account.id, GrantKind.CREDIT, 200, "wei", "fund", None, f"fund-{suffix}", operator
    )
    await accounts.create_rate_card(
        "live", None, ExactRate(1, 1, "wei", "seconds"), NOW - timedelta(hours=1), operator
    )
    await accounts.set_capability_policy(account.id, "live", None, True, "allow", operator)
    repository = PostgresSignerRepository(
        async_sessionmaker(engine, expire_on_commit=False),
        global_cap=1000,
        signer_id="signer_integration",
        signer_url="https://signer.example",
        discovery_url="https://signer.example",
    )
    signer = SignerService(
        repository,
        "integration-signer-session-pepper",
        "integration-webhook-secret",
        clock=lambda: NOW,
    )
    await signer.initialize()
    created = await signer.create_session(
        actor, "live", None, "preflight", 40, "wei", 60, f"create-a-{suffix}"
    )
    with pytest.raises(SignerConflict):
        await signer.create_session(
            actor, "live", None, "preflight", 40, "wei", 60, f"create-a-{suffix}"
        )
    with pytest.raises(SignerConflict, match="different request"):
        await signer.create_session(
            actor, "live", None, "preflight", 39, "wei", 60, f"create-a-{suffix}"
        )
    allowed = await signer.authorize(created.token, repository.signer_id, state(f"state_{suffix}"))
    assert allowed.allowed and allowed.reserved_amount == 10
    replay = await signer.authorize(created.token, repository.signer_id, state(f"state_{suffix}"))
    assert replay.reason is DenialReason.REPLAYED_STATE
    fork = await signer.authorize(
        created.token, repository.signer_id, state(f"state_{suffix}", nonce=2)
    )
    assert fork.reason is DenialReason.STATE_FORK
    quarantined = await signer.authorize(
        created.token,
        repository.signer_id,
        state(
            f"state_{suffix}",
            sequence=1,
            auth_id=created.id,
            when="2026-09-09T12:00:01Z",
        ),
    )
    assert quarantined.reason is DenialReason.STATE_FORK

    concurrent = await signer.create_session(
        actor, "live", None, "preflight", 30, "wei", 60, f"authorize-race-{suffix}"
    )
    race_state = state(f"race_{suffix}")
    race_results = await asyncio.gather(
        signer.authorize(concurrent.token, repository.signer_id, race_state),
        signer.authorize(concurrent.token, repository.signer_id, race_state),
    )
    assert sum(result.allowed for result in race_results) == 1
    assert {result.reason for result in race_results if not result.allowed} == {
        DenialReason.REPLAYED_STATE
    }

    second = await signer.create_session(
        actor, "live", None, "preflight", 15, "wei", 60, f"create-b-{suffix}"
    )
    second_state = f"second_{suffix}"
    assert (await signer.authorize(second.token, repository.signer_id, state(second_state))).allowed
    exhausted = await signer.authorize(
        second.token,
        repository.signer_id,
        state(second_state, sequence=1, auth_id=second.id, when="2026-09-09T12:00:10Z"),
    )
    assert exhausted.reason is DenialReason.LEASE_EXHAUSTED
    async with engine.connect() as connection:
        assert await connection.scalar(
            text(
                "SELECT signer_confirmed_at IS NOT NULL FROM authorization_receipts WHERE session_id=:id"
            ),
            {"id": second.id},
        )

    refreshed = await signer.refresh_session(
        actor, concurrent.id, 10, f"refresh-operation-{suffix}"
    )
    assert refreshed.lease.cap == 10
    async with engine.connect() as connection:
        old_lease = (
            await connection.execute(
                text("SELECT available,pending FROM leases WHERE session_id=:id"),
                {"id": concurrent.id},
            )
        ).one()
        assert (old_lease.available, old_lease.pending) == (0, 10)
    with pytest.raises(SignerConflict, match="already created"):
        await signer.refresh_session(actor, concurrent.id, 10, f"refresh-operation-{suffix}")
    with pytest.raises(SignerConflict, match="different request"):
        await signer.refresh_session(actor, concurrent.id, 9, f"refresh-operation-{suffix}")
    with pytest.raises(ValueError, match="not refreshable"):
        await signer.refresh_session(
            actor, "ssn_missing_session", None, f"missing-refresh-{suffix}"
        )
    with pytest.raises(ValueError, match="remaining availability"):
        await signer.refresh_session(actor, refreshed.id, 11, f"large-refresh-{suffix}")
    with pytest.raises(ValueError, match="original authentication source"):
        await signer.refresh_session(
            actor,
            refreshed.id,
            5,
            f"wrong-source-{suffix}",
            "credential_not_original",
        )

    issued = await accounts.issue_credential(account.id, principal.id, "signer", None, actor)
    credential_session = await signer.create_session(
        actor,
        "live",
        None,
        "preflight",
        5,
        "wei",
        60,
        f"credential-session-{suffix}",
        issued.credential.id,
    )
    await accounts.revoke_credential(issued.credential.id, actor)
    assert (
        await signer.authorize(
            credential_session.token, repository.signer_id, state(f"credential_{suffix}")
        )
    ).reason is DenialReason.UNKNOWN_CREDENTIAL

    expired = await repository.create_session(
        principal=actor,
        credential_id=None,
        capability="live",
        model=None,
        app="preflight",
        operation_key=f"expired-session-{suffix}",
        request_hash="a" * 64,
        requested_cap=5,
        unit="wei",
        ttl_seconds=60,
        token_hash=signer.digest("session", f"expired-{suffix}"),
        now=NOW - timedelta(seconds=120),
    )
    await signer.create_session(
        actor, "live", None, "preflight", 1, "wei", 60, f"cleanup-trigger-{suffix}"
    )
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT available FROM leases WHERE id=:id"), {"id": expired.lease.id}
            )
            == 0
        )

    await signer.revoke_session(actor, second.id)
    async with engine.connect() as connection:
        revoked_lease = (
            await connection.execute(
                text("SELECT available,pending FROM leases WHERE session_id=:id"),
                {"id": second.id},
            )
        ).one()
        assert (revoked_lease.available, revoked_lease.pending) == (0, 10)
    values = await signer.list_sessions(actor)
    assert {item.id for item in values} >= {created.id, second.id}
    assert await signer.list_leases(actor)
    tenant_admin = PrincipalContext(
        operator.id,
        frozenset({Role.TENANT_ADMIN}),
        tenant_id=TenantId(tenant.id),
    )
    unscoped = PrincipalContext(PrincipalId(f"principal_unscoped_{suffix}"), frozenset())
    assert await signer.list_sessions(operator)
    assert await signer.list_sessions(tenant_admin)
    assert not await signer.list_sessions(unscoped)
    with pytest.raises(ValueError, match="not found"):
        await signer.revoke_session(actor, "ssn_missing_session")

    # Exercise every fail-closed state boundary against real rows.  Each case
    # uses a fresh one-wei lease so a denial cannot influence the next case.
    async def denial_session(label: str) -> SignerSession:
        return await signer.create_session(
            actor, "live", None, "preflight", 1, "wei", 60, f"denial-{label}-{suffix}"
        )

    unknown = await signer.authorize(
        "not-a-session-token", repository.signer_id, state(f"unknown_{suffix}")
    )
    assert unknown.reason is DenialReason.UNKNOWN_CREDENTIAL

    killed_session = await denial_session("kill")
    await signer.set_kill_switch(True, "integration denial", operator)
    assert (
        await signer.authorize(killed_session.token, repository.signer_id, state(f"kill_{suffix}"))
    ).reason is DenialReason.KILL_SWITCH_ACTIVE
    with pytest.raises(ValueError, match="disabled"):
        await signer.refresh_session(actor, killed_session.id, None, f"killed-refresh-{suffix}")
    await signer.set_kill_switch(False, "integration reset", operator)
    assert not (await signer.get_kill_switch(operator))["enabled"]

    mismatch = await denial_session("auth-id")
    assert (
        await signer.authorize(
            mismatch.token,
            repository.signer_id,
            state(f"auth_id_{suffix}", auth_id="ssn_wrong_identity", sequence=1),
        )
    ).reason is DenialReason.IDENTITY_UNAVAILABLE
    assert (
        await signer.authorize(
            mismatch.token,
            repository.signer_id,
            state(f"gap_{suffix}", auth_id=mismatch.id, sequence=2),
        )
    ).reason is DenialReason.OUT_OF_ORDER_STATE

    malformed_cases = (
        ("app", {"app": "another-app"}, DenialReason.CAPABILITY_DENIED),
        (
            "timestamp",
            {"last_update": "not-a-timestamp"},
            DenialReason.UNSUPPORTED_PAYMENT_SHAPE,
        ),
        (
            "payment-type",
            {"payment_type": "unknown"},
            DenialReason.UNSUPPORTED_PAYMENT_SHAPE,
        ),
        (
            "quantity-unit",
            {"payment_type": "lv2v"},
            DenialReason.UNSUPPORTED_PAYMENT_SHAPE,
        ),
        (
            "rate",
            {"initial_price_per_unit": 2},
            DenialReason.UNSUPPORTED_PAYMENT_SHAPE,
        ),
    )
    for label, changes, expected in malformed_cases:
        target = await denial_session(label)
        invalid = state(f"invalid_{label}_{suffix}")
        invalid = PaymentState(
            **{
                **{field: getattr(invalid, field) for field in invalid.__dataclass_fields__},
                **changes,
            }
        )
        assert (
            await signer.authorize(target.token, repository.signer_id, invalid)
        ).reason is expected

    malformed_continuation = await signer.create_session(
        actor,
        "live",
        None,
        "preflight",
        20,
        "wei",
        60,
        f"malformed-continuation-{suffix}",
    )
    malformed_state_id = f"malformed_continuation_{suffix}"
    assert (
        await signer.authorize(
            malformed_continuation.token,
            repository.signer_id,
            state(malformed_state_id),
        )
    ).allowed
    malformed_next = state(
        malformed_state_id,
        sequence=1,
        auth_id=malformed_continuation.id,
        when="not-a-timestamp",
    )
    assert (
        await signer.authorize(malformed_continuation.token, repository.signer_id, malformed_next)
    ).reason is DenialReason.UNSUPPORTED_PAYMENT_SHAPE
    async with engine.connect() as connection:
        assert await connection.scalar(
            text(
                "SELECT signer_confirmed_at IS NOT NULL FROM authorization_receipts "
                "WHERE session_id=:id AND sequence_number=0"
            ),
            {"id": malformed_continuation.id},
        )

    suspended_continuation = await signer.create_session(
        actor, "live", None, "preflight", 20, "wei", 60, f"suspended-n1-{suffix}"
    )
    suspended_state_id = f"suspended_n1_{suffix}"
    assert (
        await signer.authorize(
            suspended_continuation.token,
            repository.signer_id,
            state(suspended_state_id),
        )
    ).allowed
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE accounts SET status='suspended' WHERE id=:id"), {"id": account.id}
        )
    assert (
        await signer.authorize(
            suspended_continuation.token,
            repository.signer_id,
            state(
                suspended_state_id,
                sequence=1,
                auth_id=suspended_continuation.id,
                when="2026-09-09T12:00:01Z",
            ),
        )
    ).reason is DenialReason.ACCOUNT_SUSPENDED
    async with engine.begin() as connection:
        assert await connection.scalar(
            text(
                "SELECT signer_confirmed_at IS NOT NULL FROM authorization_receipts "
                "WHERE session_id=:id AND sequence_number=0"
            ),
            {"id": suspended_continuation.id},
        )
        await connection.execute(
            text("UPDATE accounts SET status='active' WHERE id=:id"), {"id": account.id}
        )

    status_cases = (
        (
            "UPDATE accounts SET status=:status WHERE id=:id",
            "account",
            DenialReason.ACCOUNT_SUSPENDED,
        ),
        ("UPDATE tenants SET status=:status WHERE id=:id", "tenant", DenialReason.TENANT_SUSPENDED),
        (
            "UPDATE principals SET status=:status WHERE id=:id",
            "principal",
            DenialReason.IDENTITY_UNAVAILABLE,
        ),
    )
    for update_sql, label, expected in status_cases:
        target = await denial_session(label)
        target_id = {
            "account": account.id,
            "tenant": tenant.id,
            "principal": principal.id,
        }[label]
        async with engine.begin() as connection:
            await connection.execute(
                text(update_sql),
                {"id": target_id, "status": "suspended"},
            )
        assert (
            await signer.authorize(
                target.token, repository.signer_id, state(f"suspended_{label}_{suffix}")
            )
        ).reason is expected
        async with engine.begin() as connection:
            await connection.execute(text(update_sql), {"id": target_id, "status": "active"})

    role_session = await denial_session("role")
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM principal_roles WHERE principal_id=:id AND role='credential_holder'"),
            {"id": principal.id},
        )
    assert (
        await signer.authorize(
            role_session.token, repository.signer_id, state(f"role_removed_{suffix}")
        )
    ).reason is DenialReason.IDENTITY_UNAVAILABLE
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO principal_roles(principal_id,role) VALUES (:id,'credential_holder')"),
            {"id": principal.id},
        )

    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE accounts SET exposure_cap=0 WHERE id=:id"), {"id": account.id}
            )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM leases WHERE id=:id"), {"id": created.lease.id}
            )

    other = await accounts.create_account(tenant.id, "Concurrent", "wei", 100, operator)
    other_principal = await accounts.create_principal(
        tenant.id, other.id, "Other holder", frozenset({Role.CREDENTIAL_HOLDER}), operator
    )
    other_actor = PrincipalContext(
        PrincipalId(other_principal.id),
        other_principal.roles,
        tenant_id=TenantId(tenant.id),
        account_id=AccountId(other.id),
    )
    await accounts.create_grant(
        other.id, GrantKind.CREDIT, 100, "wei", "fund", None, f"other-{suffix}", operator
    )
    await accounts.set_capability_policy(other.id, "live", None, True, "allow", operator)
    results = await asyncio.gather(
        signer.create_session(
            other_actor, "live", None, "preflight", 80, "wei", 60, f"concurrent-a-{suffix}"
        ),
        signer.create_session(
            other_actor, "live", None, "preflight", 80, "wei", 60, f"concurrent-b-{suffix}"
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, SignerSession) for result in results) == 1, results
    assert sum(isinstance(result, ValueError) for result in results) == 1

    global_attempts: list[tuple[PrincipalContext, str]] = []
    for label in ("global-a", "global-b"):
        global_account = await accounts.create_account(tenant.id, label, "wei", 700, operator)
        global_principal = await accounts.create_principal(
            tenant.id,
            global_account.id,
            label,
            frozenset({Role.CREDENTIAL_HOLDER}),
            operator,
        )
        global_actor = PrincipalContext(
            PrincipalId(global_principal.id),
            global_principal.roles,
            tenant_id=TenantId(tenant.id),
            account_id=AccountId(global_account.id),
        )
        await accounts.create_grant(
            global_account.id,
            GrantKind.CREDIT,
            700,
            "wei",
            "global funding",
            None,
            f"{label}-{suffix}",
            operator,
        )
        await accounts.set_capability_policy(
            global_account.id, "live", None, True, "allow", operator
        )
        global_attempts.append((global_actor, label))
    global_results = await asyncio.gather(
        *(
            signer.create_session(
                global_actor,
                "live",
                None,
                "preflight",
                600,
                "wei",
                60,
                f"{label}-operation-{suffix}",
            )
            for global_actor, label in global_attempts
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, SignerSession) for result in global_results) == 1
    assert sum(isinstance(result, ValueError) for result in global_results) == 1
    async with engine.connect() as connection:
        global_row = (
            await connection.execute(
                text("SELECT open_exposure,exposure_cap FROM global_exposure WHERE singleton")
            )
        ).one()
        assert 0 <= global_row.open_exposure <= global_row.exposure_cap == 1000
    await engine.dispose()
