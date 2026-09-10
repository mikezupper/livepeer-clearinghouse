"""Account service invariants, authorization, secrets, and ledger tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from clearinghouse.application.accounts import (
    AccountService,
    Conflict,
    Forbidden,
    InvalidRequest,
    NotFound,
)
from clearinghouse.domain.accounts import (
    Account,
    AccountId,
    ExactRate,
    Grant,
    GrantKind,
    Principal,
    PrincipalContext,
    PrincipalId,
    Role,
    Status,
    Tenant,
    TenantId,
    grant_delta,
)
from clearinghouse.infrastructure.accounts import MemoryAccountsRepository


@pytest.fixture
def repository() -> MemoryAccountsRepository:
    return MemoryAccountsRepository()


@pytest.fixture
def service(repository: MemoryAccountsRepository) -> AccountService:
    return AccountService(repository, b"a-test-pepper-with-enough-bytes")


@pytest.fixture
def operator() -> PrincipalContext:
    return PrincipalContext(PrincipalId("principal_operator000"), frozenset({Role.OPERATOR}))


async def setup_account(
    service: AccountService, operator: PrincipalContext
) -> tuple[Tenant, Account, Principal, PrincipalContext]:
    tenant = await service.create_tenant("Acme", operator)
    account = await service.create_account(tenant.id, "Primary", "wei", 10_000, operator)
    principal = await service.create_principal(
        TenantId(tenant.id),
        AccountId(account.id),
        "Builder",
        frozenset({Role.CREDENTIAL_HOLDER}),
        operator,
    )
    holder = PrincipalContext(
        PrincipalId(principal.id),
        frozenset({Role.CREDENTIAL_HOLDER}),
        TenantId(tenant.id),
        AccountId(account.id),
    )
    return tenant, account, principal, holder


async def test_tenant_scope_and_admin_policy(
    service: AccountService, operator: PrincipalContext
) -> None:
    tenant, account, principal, holder = await setup_account(service, operator)
    assert [item.id for item in await service.list_tenants(holder)] == [tenant.id]
    assert [item.id for item in await service.list_principals(None, holder)] == [principal.id]
    with pytest.raises(Forbidden):
        await service.create_tenant("Denied", holder)
    with pytest.raises(Forbidden):
        await service.update_principal(
            principal.id, None, frozenset({Role.TENANT_ADMIN}), "escalate", holder
        )
    with pytest.raises(NotFound):
        await service.get_account("acct_foreign000", holder)
    assert account.tenant_id == tenant.id


async def test_explicit_principal_link_is_one_way_and_operator_only(
    service: AccountService,
    repository: MemoryAccountsRepository,
    operator: PrincipalContext,
) -> None:
    tenant, account, _, holder = await setup_account(service, operator)
    pending = await repository.create_principal(None, None, None, frozenset(), operator)
    linked = await service.link_principal(
        pending.id,
        tenant.id,
        account.id,
        frozenset({Role.CREDENTIAL_HOLDER}),
        "approved onboarding",
        operator,
    )
    assert linked.account_id == account.id
    with pytest.raises(Conflict):
        await service.link_principal(
            pending.id, tenant.id, account.id, linked.roles, "again", operator
        )
    with pytest.raises(Forbidden):
        await service.link_principal(
            pending.id, tenant.id, account.id, linked.roles, "denied", holder
        )


async def test_credential_is_revealed_once_hashed_rotated_and_fail_closed(
    service: AccountService,
    repository: MemoryAccountsRepository,
    operator: PrincipalContext,
) -> None:
    tenant, account, principal, holder = await setup_account(service, operator)
    issued = await service.issue_credential(account.id, principal.id, "CI", None, holder)
    assert issued.secret.startswith("och_live_")
    assert issued.secret.encode() not in repository.credentials[issued.credential.id][1]
    assert await service.authenticate_credential(issued.secret) == issued.credential
    assert await service.authenticate_credential(issued.secret + "x") is None
    rotated = await service.rotate_credential(issued.credential.id, holder)
    assert await service.authenticate_credential(issued.secret) is None
    assert await service.authenticate_credential(rotated.secret) == rotated.credential
    await service.update_tenant(tenant.id, None, Status.SUSPENDED, "incident", operator)
    assert await service.authenticate_credential(rotated.secret) is None
    with pytest.raises(InvalidRequest):
        await service.issue_credential(
            account.id,
            principal.id,
            "expired",
            datetime.now(UTC) - timedelta(seconds=1),
            holder,
        )


async def test_grants_are_atomic_balanced_and_idempotent(
    service: AccountService,
    repository: MemoryAccountsRepository,
    operator: PrincipalContext,
) -> None:
    _, account, _, _ = await setup_account(service, operator)

    async def grant() -> Grant:
        return await service.create_grant(
            account.id,
            GrantKind.CREDIT,
            125,
            "wei",
            "prepaid",
            None,
            "same-idempotency-key",
            operator,
        )

    first, second = await asyncio.gather(grant(), grant())
    assert first == second
    postings = repository.ledger_postings
    assert len(postings) == 2
    assert sum(value for _, _, value, unit in postings if unit == "wei") == 0
    assert (await service.balance(account.id, operator)).posted == 125
    assert [item.id for item in await service.list_grants(account.id, operator)] == [first.id]
    with pytest.raises(Conflict):
        await service.create_grant(
            account.id,
            GrantKind.CREDIT,
            126,
            "wei",
            "changed",
            None,
            "same-idempotency-key",
            operator,
        )
    with pytest.raises(InvalidRequest):
        await service.create_grant(
            account.id, GrantKind.DEBIT, -1, "wei", "bad", None, "different-key-000", operator
        )


async def test_catalog_is_immutable_versioned_exact_and_default_deny(
    service: AccountService,
    operator: PrincipalContext,
) -> None:
    tenant, account, _, holder = await setup_account(service, operator)
    rate = ExactRate(7, 3, "wei", "seconds")
    first = await service.create_rate_card(
        "live.video", None, rate, datetime.now(UTC) - timedelta(seconds=1), operator
    )
    second = await service.create_rate_card(
        "live.video", None, ExactRate(8, 3, "wei", "seconds"), datetime.now(UTC), operator
    )
    assert (first.version, second.version) == (1, 2)
    assert await service.catalog(account.id, holder) == ()
    await service.set_capability_policy(account.id, "live.video", None, True, "approved", operator)
    catalog = await service.catalog(account.id, holder)
    assert len(catalog) == 1 and catalog[0].rate.numerator == 8
    await service.update_account(account.id, None, None, Status.SUSPENDED, "pause", operator)
    assert not (await service.catalog(account.id, holder))[0].available
    await service.update_tenant(tenant.id, None, Status.ACTIVE, "resume", operator)


async def test_fail_closed_policy_branches(
    service: AccountService,
    repository: MemoryAccountsRepository,
    operator: PrincipalContext,
) -> None:
    tenant, account, principal, holder = await setup_account(service, operator)
    other_tenant = await service.create_tenant("Other", operator)
    other_account = await service.create_account(other_tenant.id, "Other", "wei", 50, operator)
    other_principal = await service.create_principal(
        other_tenant.id,
        other_account.id,
        "Other holder",
        frozenset({Role.CREDENTIAL_HOLDER}),
        operator,
    )
    other_holder = PrincipalContext(
        PrincipalId(other_principal.id),
        frozenset({Role.CREDENTIAL_HOLDER}),
        TenantId(other_tenant.id),
        AccountId(other_account.id),
    )
    admin_principal = await service.create_principal(
        tenant.id, None, "Admin", frozenset({Role.TENANT_ADMIN}), operator
    )
    admin = PrincipalContext(
        PrincipalId(admin_principal.id),
        frozenset({Role.TENANT_ADMIN}),
        TenantId(tenant.id),
    )
    peer_principal = await service.create_principal(
        tenant.id,
        account.id,
        "Peer",
        frozenset({Role.CREDENTIAL_HOLDER}),
        operator,
    )
    peer = PrincipalContext(
        PrincipalId(peer_principal.id),
        frozenset({Role.CREDENTIAL_HOLDER}),
        TenantId(tenant.id),
        AccountId(account.id),
    )
    with pytest.raises(NotFound):
        await service.get_tenant("tenant_missing", operator)
    with pytest.raises(Forbidden):
        await service.get_tenant(other_tenant.id, holder)
    with pytest.raises(InvalidRequest):
        await service.update_tenant(tenant.id, None, Status.SUSPENDED, None, operator)
    with pytest.raises(NotFound):
        await service.update_tenant("tenant_missing", "x", None, None, operator)
    assert (
        await service.list_accounts(None, PrincipalContext(PrincipalId("roleless"), frozenset()))
        == ()
    )
    with pytest.raises(NotFound):
        await service.get_account(account.id, other_holder)
    with pytest.raises(Forbidden):
        await service.create_account(tenant.id, "No", "wei", 1, holder)
    with pytest.raises(Forbidden):
        await service.update_account(account.id, "No", None, None, None, holder)
    with pytest.raises(InvalidRequest):
        await service.update_account(account.id, None, 1, None, None, operator)
    with pytest.raises(Forbidden):
        await service.create_principal(None, None, None, frozenset(), holder)
    with pytest.raises(Forbidden):
        await service.create_principal(
            tenant.id,
            account.id,
            None,
            frozenset({Role.CREDENTIAL_HOLDER}),
            holder,
        )
    with pytest.raises(InvalidRequest):
        await service.create_principal(
            tenant.id, account.id, None, frozenset({Role.OPERATOR}), operator
        )
    with pytest.raises(Forbidden):
        await service.create_principal(
            tenant.id, account.id, None, frozenset({Role.TENANT_ADMIN}), admin
        )
    with pytest.raises(Forbidden):
        await service.create_principal(
            tenant.id, account.id, None, frozenset({Role.OPERATOR}), admin
        )
    with pytest.raises(InvalidRequest):
        await service.create_principal(
            tenant.id, None, None, frozenset({Role.CREDENTIAL_HOLDER}), operator
        )
    with pytest.raises(NotFound):
        await service.update_principal("principal_missing", None, None, "x", operator)
    with pytest.raises(Forbidden):
        await service.update_principal(principal.id, None, None, "x", holder)
    with pytest.raises(Forbidden):
        await service.update_principal(admin_principal.id, None, None, "x", admin)
    with pytest.raises(InvalidRequest):
        await service.update_principal(principal.id, Status.SUSPENDED, None, None, operator)
    pending = await repository.create_principal(None, None, None, frozenset(), operator)
    with pytest.raises(Forbidden):
        await service.update_principal(pending.id, None, None, "x", admin)
    with pytest.raises(Forbidden):
        await service.update_principal(principal.id, None, frozenset({Role.OPERATOR}), "x", admin)
    with pytest.raises(Forbidden):
        await service.update_principal(principal.id, None, frozenset(), "x", admin)
    with pytest.raises(NotFound):
        await service.link_principal(
            "principal_missing", tenant.id, account.id, frozenset(), "x", operator
        )
    with pytest.raises(InvalidRequest):
        await service.link_principal(
            pending.id, tenant.id, None, frozenset({Role.CREDENTIAL_HOLDER}), "x", operator
        )
    with pytest.raises(InvalidRequest):
        await service.link_principal(
            pending.id, tenant.id, account.id, frozenset({Role.OPERATOR}), "x", operator
        )
    with pytest.raises(InvalidRequest):
        await service.link_principal(
            pending.id,
            tenant.id,
            other_account.id,
            frozenset({Role.CREDENTIAL_HOLDER}),
            "x",
            operator,
        )
    issued = await service.issue_credential(account.id, principal.id, "one", None, holder)
    assert len(await service.list_principals(None, admin)) >= 1
    assert len(await service.list_credentials(admin)) == 1
    assert len(await service.list_credentials(holder)) == 1
    with pytest.raises(Forbidden):
        await service.issue_credential(account.id, principal.id, "no", None, peer)
    with pytest.raises(InvalidRequest):
        await service.issue_credential(account.id, "principal_missing", "no", None, operator)
    with pytest.raises(NotFound):
        await service.rotate_credential("cred_missing", operator)
    with pytest.raises(NotFound):
        await service.revoke_credential("cred_missing", operator)
    with pytest.raises(Forbidden):
        await service.rotate_credential(issued.credential.id, peer)
    with pytest.raises(Forbidden):
        await service.revoke_credential(issued.credential.id, peer)
    assert await service.authenticate_credential("och_live_missing") is None
    stored = repository.credentials[issued.credential.id]
    repository.credentials[issued.credential.id] = (
        replace(stored[0], expires_at=datetime.now(UTC) - timedelta(seconds=1)),
        stored[1],
    )
    assert await service.authenticate_credential(issued.secret) is None
    with pytest.raises(Forbidden):
        await service.create_grant(
            account.id, GrantKind.CREDIT, 1, "wei", "no", None, "holder-key-000000", holder
        )
    with pytest.raises(InvalidRequest):
        await service.create_grant(
            account.id, GrantKind.CREDIT, 1, "usd", "no", None, "wrong-unit-00000", operator
        )
    assert await service.list_grants(None, admin) == ()
    assert await service.list_grants(None, holder) == ()
    assert await service.list_grants(None, operator) == ()
    assert await service.list_grants(None, PrincipalContext(PrincipalId("none"), frozenset())) == ()
    with pytest.raises(Forbidden):
        await service.list_rate_cards(holder)
    with pytest.raises(InvalidRequest):
        await service.create_rate_card(
            "x", None, ExactRate(1, 1, "wei", "fixed"), datetime(2026, 1, 1), operator
        )
    with pytest.raises(Forbidden):
        await service.set_capability_policy(account.id, "x", None, True, "no", holder)


def test_exact_rate_and_pepper_validation() -> None:
    with pytest.raises(ValueError):
        ExactRate(1, 0, "wei", "seconds")
    with pytest.raises(ValueError):
        AccountService(MemoryAccountsRepository(), b"short")
    assert grant_delta(GrantKind.DEBIT, 2) == -2
    assert grant_delta(GrantKind.ADJUSTMENT, -3) == -3
    with pytest.raises(ValueError):
        grant_delta(GrantKind.CREDIT, 0)
    with pytest.raises(ValueError):
        grant_delta(GrantKind.ADJUSTMENT, 0)


async def test_memory_repository_defensive_and_filter_branches(
    repository: MemoryAccountsRepository, operator: PrincipalContext
) -> None:
    with pytest.raises(Conflict):
        await repository.create_account("tenant_missing", "x", "wei", 1, operator)
    assert await repository.update_account("acct_missing", None, None, None, None, operator) is None
    with pytest.raises(Conflict):
        await repository.create_principal("tenant_missing", None, None, frozenset(), operator)
    tenant = await repository.create_tenant("One", operator)
    with pytest.raises(Conflict):
        await repository.create_principal(tenant.id, "acct_missing", None, frozenset(), operator)
    account = await repository.create_account(tenant.id, "One", "wei", 1, operator)
    other = await repository.create_tenant("Two", operator)
    with pytest.raises(Conflict):
        await repository.create_principal(other.id, account.id, None, frozenset(), operator)
    assert (
        await repository.update_principal("principal_missing", None, None, None, operator) is None
    )
    assert (
        await repository.link_principal(
            "principal_missing", tenant.id, account.id, frozenset(), "x", operator
        )
        is None
    )
    assert (
        await repository.replace_credential("cred_missing", "prefix", b"x" * 32, operator) is None
    )
    assert await repository.revoke_credential("cred_missing", operator) is None
    assert await repository.balance("acct_missing") is None
    await repository.create_rate_card(
        "future",
        None,
        ExactRate(1, 1, "wei", "fixed"),
        datetime.now(UTC) + timedelta(days=1),
        operator,
    )
    assert await repository.catalog(account.id) == ()
