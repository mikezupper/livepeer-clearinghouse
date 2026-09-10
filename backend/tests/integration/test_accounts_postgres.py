"""PostgreSQL account repository and commit-time invariant tests."""

# ruff: noqa: E501 -- SQL statements stay intact for review against the migration.

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from clearinghouse.application.accounts import AccountService, Conflict
from clearinghouse.domain.accounts import (
    ExactRate,
    GrantKind,
    PrincipalContext,
    PrincipalId,
    Role,
    TenantId,
)
from clearinghouse.infrastructure.accounts import PostgresAccountsRepository
from clearinghouse.infrastructure.metering import encode_cursor

DATABASE_URL = os.getenv("CLEARINGHOUSE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="live PostgreSQL not configured")


async def test_postgres_repository_and_commit_constraints() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    repository = PostgresAccountsRepository(engine)
    service = AccountService(repository, b"integration-test-pepper-is-long")
    operator = PrincipalContext(
        PrincipalId("principal_integration_operator"), frozenset({Role.OPERATOR})
    )
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO principals(id) VALUES (:id) ON CONFLICT DO NOTHING"),
            {"id": str(operator.id)},
        )
        await connection.execute(
            text(
                "INSERT INTO principal_roles(principal_id,role) VALUES (:id,'operator') ON CONFLICT DO NOTHING"
            ),
            {"id": str(operator.id)},
        )
    tenant = await service.create_tenant("Integration", operator)
    account = await service.create_account(tenant.id, "Primary", "wei", 1000, operator)
    key = f"integration-{uuid4().hex}"
    first = await service.create_grant(
        account.id,
        GrantKind.CREDIT,
        100,
        "wei",
        "fund",
        None,
        key,
        operator,
    )
    repeated = await service.create_grant(
        account.id,
        GrantKind.CREDIT,
        100,
        "wei",
        "fund",
        None,
        key,
        operator,
    )
    assert repeated.id == first.id
    assert (await service.balance(account.id, operator)).posted == 100
    cards = await asyncio.gather(
        service.create_rate_card(
            "live.video", None, ExactRate(1, 1, "wei", "seconds"), datetime.now(UTC), operator
        ),
        service.create_rate_card(
            "live.video", None, ExactRate(2, 1, "wei", "seconds"), datetime.now(UTC), operator
        ),
    )
    versions = sorted(card.version for card in cards)
    assert versions[1] - versions[0] == 1
    with pytest.raises(Conflict):
        await service.create_grant(
            account.id,
            GrantKind.CREDIT,
            101,
            "wei",
            "changed",
            None,
            key,
            operator,
        )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE grants SET reason='tampered' WHERE id=:id"),
                {"id": first.id},
            )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO ledger_transactions(id,tenant_id,kind,source_id,actor_id) VALUES ('ledger_tx_empty',:tenant,'test','empty',:actor)"
                ),
                {"tenant": tenant.id, "actor": str(operator.id)},
            )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            tx = "ledger_tx_unbalanced"
            await connection.execute(
                text(
                    "INSERT INTO ledger_transactions(id,tenant_id,kind,source_id,actor_id) VALUES (:tx,:tenant,'test','unbalanced',:actor)"
                ),
                {"tx": tx, "tenant": tenant.id, "actor": str(operator.id)},
            )
            await connection.execute(
                text(
                    "INSERT INTO ledger_postings(id,transaction_id,tenant_id,account_code,amount,unit) VALUES ('post_bad_a',:tx,:tenant,'a',2,'wei'),('post_bad_b',:tx,:tenant,'b',-1,'wei')"
                ),
                {"tx": tx, "tenant": tenant.id},
            )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO principals(id) VALUES ('principal_bad_scope')")
            )
            await connection.execute(
                text(
                    "INSERT INTO principal_roles(principal_id,role) VALUES ('principal_bad_scope','tenant_admin')"
                )
            )
    other_tenant = await service.create_tenant("Other", operator)
    other_account = await service.create_account(other_tenant.id, "Other", "wei", 10, operator)
    await service.create_grant(
        other_account.id,
        GrantKind.CREDIT,
        1,
        "wei",
        "pagination fixture",
        None,
        f"integration-other-{uuid4().hex}",
        operator,
    )
    tenants_page = await service.list_tenants(operator, 1)
    assert len(tenants_page) == 2
    tenant_cursor = encode_cursor(tenants_page[0].created_at, tenants_page[0].id)
    following_tenants = await service.list_tenants(operator, 1, tenant_cursor)
    assert following_tenants and following_tenants[0].id != tenants_page[0].id
    accounts_page = await service.list_accounts(None, operator, 1)
    assert len(accounts_page) == 2
    account_cursor = encode_cursor(accounts_page[0].created_at, accounts_page[0].id)
    following_accounts = await service.list_accounts(None, operator, 1, account_cursor)
    assert following_accounts and following_accounts[0].id != accounts_page[0].id
    grants_page = await service.list_grants(None, operator, 1)
    assert len(grants_page) == 2
    grant_cursor = encode_cursor(grants_page[0].created_at, grants_page[0].id)
    following_grants = await service.list_grants(None, operator, 1, grant_cursor)
    assert following_grants and following_grants[0].id != grants_page[0].id
    with pytest.raises(ValueError, match="invalid cursor"):
        await service.list_accounts(None, operator, 1, "bad")

    tenant_admin = PrincipalContext(
        PrincipalId("principal_integration_tenant_admin"),
        frozenset({Role.TENANT_ADMIN}),
        TenantId(other_tenant.id),
    )
    assert [item.id for item in await service.list_tenants(tenant_admin)] == [other_tenant.id]
    assert [item.id for item in await service.list_accounts(tenant.id, tenant_admin)] == [
        other_account.id
    ]
    assert [item.account_id for item in await service.list_grants(None, tenant_admin)] == [
        other_account.id
    ]
    scoped = await service.create_principal(
        tenant.id, account.id, "Scoped", frozenset({Role.CREDENTIAL_HOLDER}), operator
    )
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO credentials(id,tenant_id,account_id,principal_id,prefix,secret_hash,label) VALUES ('cred_bad_scope',:tenant,:account,:principal,'och_bad_scope',:hash,'bad')"
                ),
                {
                    "tenant": other_tenant.id,
                    "account": other_account.id,
                    "principal": scoped.id,
                    "hash": b"0" * 32,
                },
            )
    await engine.dispose()
