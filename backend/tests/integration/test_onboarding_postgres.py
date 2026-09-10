"""Live PostgreSQL onboarding concurrency and takeover tests."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clearinghouse.application.onboarding import OnboardingService
from clearinghouse.domain.accounts import PrincipalContext, PrincipalId, Role
from clearinghouse.domain.onboarding import (
    InvalidInvitation,
    OnboardingConflict,
    OnboardingForbidden,
    OnboardingNotFound,
    OperatorBootstrap,
)
from clearinghouse.infrastructure.onboarding import PostgresOnboardingRepository

DATABASE_URL = os.environ.get("CLEARINGHOUSE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="live PostgreSQL URL not configured")


@pytest.mark.asyncio
async def test_bootstrap_linking_takeover_and_database_invariants() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = OnboardingService(PostgresOnboardingRepository(sessions), "integration-pepper-strong")
    bootstrap_secret = "concurrent-operator-seed-value-123456"  # noqa: S105

    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO principals(id,tenant_id,account_id,status) "
                    "VALUES ('operator_12345678',NULL,NULL,'active')"
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO auth_identities
                      (id,principal_id,provider,provider_subject,email,created_at)
                    VALUES ('identity_operator1','operator_12345678','email',
                            'operator@example.test','operator@example.test',now())
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO auth_browser_sessions
                      (id,principal_id,token_hash,csrf_hash,client_ip_hash,user_agent_hash,
                       created_at,last_seen_at,expires_at,absolute_expires_at)
                    VALUES ('session_operator1','operator_12345678',repeat('a',64),repeat('b',64),
                            repeat('c',64),repeat('d',64),now(),now(),now()+interval '1 hour',
                            now()+interval '1 day')
                    """
                )
            )
        initial = await asyncio.gather(
            service.bootstrap_operator("operator@example.test", bootstrap_secret),
            service.bootstrap_operator(
                "operator@example.test", "competing-operator-seed-value-654321"
            ),
            return_exceptions=True,
        )
        successes = [result for result in initial if isinstance(result, OperatorBootstrap)]
        assert len(successes) == 1 and successes[0].created
        assert any(isinstance(result, OnboardingConflict) for result in initial)
        retries = await asyncio.gather(
            service.bootstrap_operator("operator@example.test", bootstrap_secret),
            service.bootstrap_operator(
                "operator@example.test", "competing-operator-seed-value-654321"
            ),
            return_exceptions=True,
        )
        idempotent = [result for result in retries if isinstance(result, OperatorBootstrap)]
        assert len(idempotent) == 1 and not idempotent[0].created
        async with engine.connect() as connection:
            assert await connection.scalar(
                text(
                    "SELECT revoked_at IS NOT NULL FROM auth_browser_sessions "
                    "WHERE id='session_operator1'"
                )
            )
        operator = PrincipalContext(
            PrincipalId(successes[0].principal_id), frozenset({Role.OPERATOR})
        )

        with pytest.raises(OnboardingConflict):
            await service.bootstrap_operator(
                "other@example.test", "different-seed-value-123456789012"
            )

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO tenants(id,display_name) VALUES ('tenant_12345678','Tenant')
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO accounts(id,tenant_id,display_name,unit,exposure_cap)
                    VALUES ('account_12345678','tenant_12345678','Account','wei',100)
                    """
                )
            )
            for suffix in ("source_12345678", "attacker_12345678"):
                await connection.execute(
                    text(
                        "INSERT INTO principals(id,tenant_id,account_id,status) "
                        "VALUES (:id,NULL,NULL,'active')"
                    ),
                    {"id": suffix},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO auth_identities
                            (id,principal_id,provider,provider_subject,email,created_at)
                        VALUES (:identity,:principal,'github',:subject,NULL,now())
                        """
                    ),
                    {
                        "identity": f"identity_{suffix}",
                        "principal": suffix,
                        "subject": suffix,
                    },
                )
            await connection.execute(
                text(
                    """
                    INSERT INTO principals(id,tenant_id,account_id,status)
                    VALUES ('target_12345678','tenant_12345678','account_12345678','active')
                    """
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO principal_roles(principal_id,role) "
                    "VALUES ('target_12345678','credential_holder')"
                )
            )

        with pytest.raises(OnboardingNotFound, match="source principal not found"):
            await service.issue_invitation(
                "missing_12345678", "target_12345678", "hidden source", operator
            )
        with pytest.raises(OnboardingNotFound, match="principal not found"):
            await service.issue_invitation(
                "source_12345678", "missing_12345678", "hidden target", operator
            )
        missing_operator = PrincipalContext(
            PrincipalId("operator_missing1"), frozenset({Role.OPERATOR})
        )
        with pytest.raises(OnboardingForbidden, match="issuer is not active"):
            await service.issue_invitation(
                "source_12345678", "target_12345678", "missing issuer", missing_operator
            )
        with pytest.raises(OnboardingConflict, match="target principal"):
            await service.issue_invitation(
                "source_12345678", "attacker_12345678", "unscoped target", operator
            )
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE principals SET status='suspended' WHERE id='source_12345678'")
            )
        with pytest.raises(OnboardingNotFound, match="source principal not found"):
            await service.issue_invitation(
                "source_12345678", "target_12345678", "suspended source", operator
            )
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE principals SET status='active' WHERE id='source_12345678'")
            )

        issued = await service.issue_invitation(
            "source_12345678", "target_12345678", "explicit owner link", operator
        )
        attacker = PrincipalContext(PrincipalId("attacker_12345678"), frozenset())
        with pytest.raises(InvalidInvitation):
            await service.redeem_invitation(issued.secret, attacker)
        replacement = await service.issue_invitation(
            "source_12345678", "target_12345678", "replacement owner link", operator
        )
        source = PrincipalContext(PrincipalId("source_12345678"), frozenset())
        with pytest.raises(InvalidInvitation):
            await service.redeem_invitation(issued.secret, source)
        link = await service.redeem_invitation(replacement.secret, source)
        assert link.principal_id == "target_12345678"
        with pytest.raises(InvalidInvitation):
            await service.redeem_invitation(replacement.secret, source)

        privilege_snapshot = await service.issue_invitation(
            "attacker_12345678", "target_12345678", "must preserve scope", operator
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO principal_roles(principal_id,role) "
                    "VALUES ('target_12345678','tenant_admin')"
                )
            )
        with pytest.raises(OnboardingConflict):
            await service.redeem_invitation(privilege_snapshot.secret, attacker)

        future_service = OnboardingService(
            PostgresOnboardingRepository(sessions),
            "integration-pepper-strong",
            clock=lambda: datetime(2030, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(InvalidInvitation):
            await future_service.redeem_invitation(privilege_snapshot.secret, attacker)

        issuer_snapshot = await service.issue_invitation(
            "attacker_12345678", "target_12345678", "issuer must remain active", operator
        )
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE principals SET status='suspended' WHERE id=:operator"),
                {"operator": str(operator.id)},
            )
        with pytest.raises(OnboardingConflict):
            await service.redeem_invitation(issuer_snapshot.secret, attacker)
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE principals SET status='active' WHERE id=:operator"),
                {"operator": str(operator.id)},
            )

        concurrent_invite = await service.issue_invitation(
            "attacker_12345678", "target_12345678", "concurrent link", operator
        )
        concurrent_results = await asyncio.gather(
            service.redeem_invitation(concurrent_invite.secret, attacker),
            service.issue_invitation(
                "attacker_12345678", "target_12345678", "concurrent replacement", operator
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in concurrent_results) == 1
        assert all(not isinstance(result, DBAPIError) for result in concurrent_results), (
            concurrent_results
        )

        async with engine.begin() as connection:
            linked_principal = await connection.scalar(
                text("SELECT principal_id FROM auth_identities WHERE id=:identity"),
                {"identity": "identity_source_12345678"},
            )
            assert linked_principal == "target_12345678"
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text(
                        "UPDATE auth_identities SET principal_id='attacker_12345678' "
                        "WHERE id='identity_source_12345678'"
                    )
                )
        for statement in (
            "UPDATE principals SET status='active' WHERE id='source_12345678'",
            "UPDATE principals SET tenant_id='tenant_12345678' WHERE id='source_12345678'",
            "INSERT INTO principal_roles(principal_id,role) "
            "VALUES ('source_12345678','tenant_admin')",
        ):
            with pytest.raises(DBAPIError):
                async with engine.begin() as connection:
                    await connection.execute(text(statement))
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO auth_identity_invitations
                          (id,source_principal_id,target_principal_id,tenant_id,
                           target_account_id,target_roles,target_status,issued_by,
                           issuer_authority,secret_prefix,secret_hash,reason,expires_at,created_at)
                        VALUES ('invite_invalid1','attacker_12345678','target_12345678',
                          'tenant_12345678','account_12345678','credential_holder,tenant_admin',
                          'active','attacker_12345678','operator','invalid-prefix-12345678',
                          decode(repeat('ab',32),'hex'),'invalid direct write',
                          now()+interval '1 hour',now())
                        """
                    )
                )
    finally:
        await engine.dispose()
