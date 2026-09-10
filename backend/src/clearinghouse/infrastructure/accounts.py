"""Reference account repositories for PostgreSQL and isolated tests."""

# ruff: noqa: E501, S608 -- keeping SQL statements visually atomic aids DB review.

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from clearinghouse.application.accounts import Conflict
from clearinghouse.domain.accounts import (
    Account,
    Balance,
    CatalogEntry,
    Credential,
    ExactRate,
    Grant,
    GrantKind,
    Principal,
    PrincipalContext,
    RateCard,
    Role,
    Status,
    Tenant,
    grant_delta,
    utc_now,
)
from clearinghouse.infrastructure.metering import _decode_cursor


def opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class MemoryAccountsRepository:
    """Concurrency-safe repository used by unit and HTTP adapter tests."""

    def __init__(self) -> None:
        self.tenants: dict[str, Tenant] = {}
        self.accounts: dict[str, Account] = {}
        self.principals: dict[str, Principal] = {}
        self.credentials: dict[str, tuple[Credential, bytes]] = {}
        self.grants: dict[str, Grant] = {}
        self.rate_cards: dict[str, RateCard] = {}
        self.policies: dict[tuple[str, str, str | None], bool] = {}
        self.posted: dict[str, int] = {}
        self.open_exposure: dict[str, int] = {}
        self.idempotency: dict[tuple[str, str, str], tuple[str, str]] = {}
        self.audit: list[dict[str, str]] = []
        self.ledger_postings: list[tuple[str, str, int, str]] = []
        self._lock = asyncio.Lock()

    def _audit(self, actor: PrincipalContext, action: str, target: str, reason: str) -> None:
        self.audit.append(
            {"actor_id": str(actor.id), "action": action, "target_id": target, "reason": reason}
        )

    async def list_tenants(
        self,
        tenant_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Sequence[Tenant]:
        values = [
            item for item in self.tenants.values() if tenant_id is None or item.id == tenant_id
        ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        if cursor is not None:
            marker = _decode_cursor(cursor)
            values = [item for item in values if (item.created_at, item.id) < marker]
        return tuple(values[: limit + 1])

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self.tenants.get(tenant_id)

    async def create_tenant(self, display_name: str, actor: PrincipalContext) -> Tenant:
        tenant = Tenant(opaque_id("tenant"), display_name, Status.ACTIVE, utc_now())
        self.tenants[tenant.id] = tenant
        self._audit(actor, "tenant.created", tenant.id, "created")
        return tenant

    async def update_tenant(
        self,
        tenant_id: str,
        display_name: str | None,
        status: Status | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Tenant | None:
        current = self.tenants.get(tenant_id)
        if current is None:
            return None
        updated = replace(
            current,
            display_name=display_name or current.display_name,
            status=status or current.status,
        )
        self.tenants[tenant_id] = updated
        self._audit(actor, "tenant.updated", tenant_id, reason or "metadata updated")
        return updated

    async def list_accounts(
        self,
        tenant_id: str | None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Sequence[Account]:
        values = [
            item
            for item in self.accounts.values()
            if tenant_id is None or item.tenant_id == tenant_id
        ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        if cursor is not None:
            marker = _decode_cursor(cursor)
            values = [item for item in values if (item.created_at, item.id) < marker]
        return tuple(values[: limit + 1])

    async def get_account(self, account_id: str) -> Account | None:
        return self.accounts.get(account_id)

    async def create_account(
        self,
        tenant_id: str,
        display_name: str,
        unit: str,
        exposure_cap: int,
        actor: PrincipalContext,
    ) -> Account:
        if tenant_id not in self.tenants:
            raise Conflict("tenant does not exist")
        account = Account(
            opaque_id("acct"), tenant_id, display_name, unit, exposure_cap, Status.ACTIVE, utc_now()
        )
        self.accounts[account.id] = account
        self.posted[account.id] = 0
        self._audit(actor, "account.created", account.id, "created")
        return account

    async def update_account(
        self,
        account_id: str,
        display_name: str | None,
        exposure_cap: int | None,
        status: Status | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Account | None:
        current = self.accounts.get(account_id)
        if current is None:
            return None
        updated = replace(
            current,
            display_name=display_name or current.display_name,
            exposure_cap=current.exposure_cap if exposure_cap is None else exposure_cap,
            status=status or current.status,
        )
        self.accounts[account_id] = updated
        self._audit(actor, "account.updated", account_id, reason or "metadata updated")
        return updated

    async def list_principals(self, tenant_id: str | None) -> Sequence[Principal]:
        return tuple(
            p for p in self.principals.values() if tenant_id is None or p.tenant_id == tenant_id
        )

    async def get_principal(self, principal_id: str) -> Principal | None:
        return self.principals.get(principal_id)

    async def create_principal(
        self,
        tenant_id: str | None,
        account_id: str | None,
        display_name: str | None,
        roles: frozenset[Role],
        actor: PrincipalContext,
    ) -> Principal:
        if tenant_id is not None and tenant_id not in self.tenants:
            raise Conflict("tenant does not exist")
        if account_id is not None:
            account = self.accounts.get(account_id)
            if account is None:
                raise Conflict("account does not exist")
            if account.tenant_id != tenant_id:
                raise Conflict("account belongs to another tenant")
        principal = Principal(
            opaque_id("principal"),
            tenant_id,
            account_id,
            display_name,
            Status.ACTIVE,
            roles,
            utc_now(),
        )
        self.principals[principal.id] = principal
        self._audit(actor, "principal.created", principal.id, "created")
        return principal

    async def update_principal(
        self,
        principal_id: str,
        status: Status | None,
        roles: frozenset[Role] | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Principal | None:
        current = self.principals.get(principal_id)
        if current is None:
            return None
        updated = replace(
            current,
            status=status or current.status,
            roles=current.roles if roles is None else roles,
        )
        self.principals[principal_id] = updated
        self._audit(actor, "principal.updated", principal_id, reason or "updated")
        return updated

    async def link_principal(
        self,
        principal_id: str,
        tenant_id: str,
        account_id: str | None,
        roles: frozenset[Role],
        reason: str,
        actor: PrincipalContext,
    ) -> Principal | None:
        current = self.principals.get(principal_id)
        if current is None or current.tenant_id is not None:
            return None
        linked = replace(current, tenant_id=tenant_id, account_id=account_id, roles=roles)
        self.principals[principal_id] = linked
        self._audit(actor, "principal.scoped", principal_id, reason)
        return linked

    async def list_credentials(
        self, principal_id: str | None, tenant_id: str | None = None
    ) -> Sequence[Credential]:
        values = (entry[0] for entry in self.credentials.values())
        return tuple(
            c
            for c in values
            if (principal_id is None or c.principal_id == principal_id)
            and (tenant_id is None or self.accounts[c.account_id].tenant_id == tenant_id)
        )

    async def get_credential(self, credential_id: str) -> Credential | None:
        entry = self.credentials.get(credential_id)
        return entry[0] if entry else None

    async def create_credential(
        self,
        account_id: str,
        principal_id: str,
        prefix: str,
        secret_hash: bytes,
        label: str,
        expires_at: datetime | None,
        actor: PrincipalContext,
    ) -> Credential:
        credential = Credential(
            opaque_id("cred"),
            account_id,
            principal_id,
            prefix,
            label,
            "active",
            utc_now(),
            expires_at,
        )
        self.credentials[credential.id] = (credential, secret_hash)
        self._audit(actor, "credential.issued", credential.id, "issued")
        return credential

    async def replace_credential(
        self, credential_id: str, prefix: str, secret_hash: bytes, actor: PrincipalContext
    ) -> Credential | None:
        stored = self.credentials.get(credential_id)
        if stored is None:
            return None
        old = replace(stored[0], status="revoked")
        self.credentials[credential_id] = (old, stored[1])
        replacement = Credential(
            opaque_id("cred"),
            old.account_id,
            old.principal_id,
            prefix,
            old.label,
            "active",
            utc_now(),
            old.expires_at,
        )
        self.credentials[replacement.id] = (replacement, secret_hash)
        self._audit(actor, "credential.rotated", credential_id, replacement.id)
        return replacement

    async def revoke_credential(
        self, credential_id: str, actor: PrincipalContext
    ) -> Credential | None:
        stored = self.credentials.get(credential_id)
        if stored is None:
            return None
        revoked = replace(stored[0], status="revoked")
        self.credentials[credential_id] = (revoked, stored[1])
        self._audit(actor, "credential.revoked", credential_id, "revoked")
        return revoked

    async def credential_hash(self, prefix: str) -> tuple[Credential, bytes] | None:
        return next(
            (entry for entry in self.credentials.values() if entry[0].prefix == prefix), None
        )

    async def create_grant(
        self,
        account_id: str,
        kind: GrantKind,
        amount: int,
        unit: str,
        reason: str,
        external_reference: str | None,
        idempotency_key: str,
        request_hash: str,
        actor: PrincipalContext,
    ) -> Grant:
        scope = (str(actor.id), "grant.create", idempotency_key)
        async with self._lock:
            previous = self.idempotency.get(scope)
            if previous:
                if previous[0] != request_hash:
                    raise Conflict("idempotency key reused with different request")
                return self.grants[previous[1]]
            grant = Grant(
                opaque_id("grant"),
                account_id,
                kind,
                amount,
                unit,
                reason,
                str(actor.id),
                utc_now(),
                external_reference,
            )
            delta = grant_delta(kind, amount)
            transaction_id = opaque_id("ledger_tx")
            postings = (
                (transaction_id, f"payer:{account_id}", delta, unit),
                (transaction_id, "system:grants", -delta, unit),
            )
            if sum(posting[2] for posting in postings) != 0:
                raise AssertionError("ledger transaction is not balanced")
            self.grants[grant.id] = grant
            self.ledger_postings.extend(postings)
            self.posted[account_id] = self.posted.get(account_id, 0) + delta
            self.idempotency[scope] = (request_hash, grant.id)
            self._audit(actor, "grant.created", grant.id, reason)
            return grant

    async def list_grants(
        self,
        account_id: str | None,
        tenant_id: str | None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Sequence[Grant]:
        values = [
            grant
            for grant in self.grants.values()
            if (account_id is None or grant.account_id == account_id)
            and (tenant_id is None or self.accounts[grant.account_id].tenant_id == tenant_id)
        ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        if cursor is not None:
            marker = _decode_cursor(cursor)
            values = [item for item in values if (item.created_at, item.id) < marker]
        return tuple(values[: limit + 1])

    async def balance(self, account_id: str) -> Balance | None:
        account = self.accounts.get(account_id)
        if account is None:
            return None
        posted = self.posted.get(account_id, 0)
        exposure = self.open_exposure.get(account_id, 0)
        return Balance(account_id, posted, exposure, posted - exposure, account.unit)

    async def list_rate_cards(self) -> Sequence[RateCard]:
        return tuple(
            sorted(
                self.rate_cards.values(),
                key=lambda card: (card.capability, card.model or "", card.version),
            )
        )

    async def create_rate_card(
        self,
        capability: str,
        model: str | None,
        rate: ExactRate,
        effective_at: datetime,
        actor: PrincipalContext,
    ) -> RateCard:
        versions = [
            r.version
            for r in self.rate_cards.values()
            if r.capability == capability and r.model == model
        ]
        card = RateCard(
            opaque_id("rate"),
            max(versions, default=0) + 1,
            capability,
            model,
            rate,
            effective_at,
            utc_now(),
        )
        self.rate_cards[card.id] = card
        self._audit(actor, "rate_card.created", card.id, "published")
        return card

    async def catalog(self, account_id: str) -> Sequence[CatalogEntry]:
        now = utc_now()
        latest: dict[tuple[str, str | None], RateCard] = {}
        for card in self.rate_cards.values():
            key = (card.capability, card.model)
            if card.effective_at <= now and (
                key not in latest or card.version > latest[key].version
            ):
                latest[key] = card
        return tuple(
            CatalogEntry(card.capability, card.model, card.rate, True)
            for card in latest.values()
            if self.policies.get((account_id, card.capability, card.model), False)
        )

    async def set_capability_policy(
        self,
        account_id: str,
        capability: str,
        model: str | None,
        allowed: bool,
        reason: str,
        actor: PrincipalContext,
    ) -> None:
        self.policies[(account_id, capability, model)] = allowed
        self._audit(actor, "account.capability_policy", account_id, reason)


class PostgresAccountsRepository:
    """PostgreSQL repository using explicit SQL and atomic financial writes."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @staticmethod
    def _tenant(row: Any) -> Tenant:
        return Tenant(row.id, row.display_name, Status(row.status), row.created_at)

    @staticmethod
    def _account(row: Any) -> Account:
        return Account(
            row.id,
            row.tenant_id,
            row.display_name,
            row.unit,
            int(row.exposure_cap),
            Status(row.status),
            row.created_at,
        )

    @staticmethod
    def _credential(row: Any) -> Credential:
        return Credential(
            row.id,
            row.account_id,
            row.principal_id,
            row.prefix,
            row.label,
            row.status,
            row.created_at,
            row.expires_at,
        )

    async def _audit(
        self,
        connection: Any,
        actor: PrincipalContext,
        action: str,
        target_id: str,
        reason: str,
        tenant_id: str | None = None,
    ) -> None:
        await connection.execute(
            text(
                "INSERT INTO audit_events (id, tenant_id, actor_id, action, target_id, reason, request_id) VALUES (:id,:tenant,:actor,:action,:target,:reason,:request)"
            ),
            {
                "id": opaque_id("audit"),
                "tenant": tenant_id if tenant_id is not None else actor.tenant_id,
                "actor": str(actor.id),
                "action": action,
                "target": target_id,
                "reason": reason,
                "request": opaque_id("request"),
            },
        )

    async def list_tenants(
        self,
        tenant_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Sequence[Tenant]:
        where = " WHERE id=:tenant" if tenant_id is not None else ""
        params: dict[str, object] = {"tenant": tenant_id, "limit": limit + 1}
        if cursor is not None:
            cursor_time, cursor_id = _decode_cursor(cursor)
            where += " AND" if where else " WHERE"
            where += " (created_at,id)<(:cursor_time,:cursor_id)"
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
        async with self.engine.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT id,display_name,status,created_at FROM tenants"
                        + where
                        + " ORDER BY created_at DESC,id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).all()
        return tuple(self._tenant(r) for r in rows)

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        async with self.engine.connect() as c:
            row = (
                await c.execute(
                    text("SELECT id,display_name,status,created_at FROM tenants WHERE id=:id"),
                    {"id": tenant_id},
                )
            ).first()
        return self._tenant(row) if row else None

    async def create_tenant(self, display_name: str, actor: PrincipalContext) -> Tenant:
        tenant_id = opaque_id("tenant")
        async with self.engine.begin() as c:
            row = (
                await c.execute(
                    text(
                        "INSERT INTO tenants(id,display_name) VALUES (:id,:name) RETURNING id,display_name,status,created_at"
                    ),
                    {"id": tenant_id, "name": display_name},
                )
            ).one()
            await self._audit(c, actor, "tenant.created", tenant_id, "created", tenant_id)
        return self._tenant(row)

    async def update_tenant(
        self,
        tenant_id: str,
        display_name: str | None,
        status: Status | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Tenant | None:
        async with self.engine.begin() as c:
            row = (
                await c.execute(
                    text(
                        "UPDATE tenants SET display_name=COALESCE(:name,display_name), status=COALESCE(:status,status) WHERE id=:id RETURNING id,display_name,status,created_at"
                    ),
                    {
                        "id": tenant_id,
                        "name": display_name,
                        "status": status.value if status else None,
                    },
                )
            ).first()
            if row:
                await self._audit(
                    c, actor, "tenant.updated", tenant_id, reason or "metadata updated", tenant_id
                )
        return self._tenant(row) if row else None

    async def list_accounts(
        self,
        tenant_id: str | None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Sequence[Account]:
        where = " WHERE tenant_id=:tenant" if tenant_id is not None else ""
        params: dict[str, object] = {"tenant": tenant_id, "limit": limit + 1}
        if cursor is not None:
            cursor_time, cursor_id = _decode_cursor(cursor)
            where += " AND" if where else " WHERE"
            where += " (created_at,id)<(:cursor_time,:cursor_id)"
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
        query = (
            "SELECT id,tenant_id,display_name,unit,exposure_cap,status,created_at FROM accounts"
            + where
            + " ORDER BY created_at DESC,id DESC LIMIT :limit"
        )
        async with self.engine.connect() as c:
            rows = (await c.execute(text(query), params)).all()
        return tuple(self._account(r) for r in rows)

    async def get_account(self, account_id: str) -> Account | None:
        async with self.engine.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT id,tenant_id,display_name,unit,exposure_cap,status,created_at FROM accounts WHERE id=:id"
                    ),
                    {"id": account_id},
                )
            ).first()
        return self._account(row) if row else None

    async def create_account(
        self,
        tenant_id: str,
        display_name: str,
        unit: str,
        exposure_cap: int,
        actor: PrincipalContext,
    ) -> Account:
        account_id = opaque_id("acct")
        async with self.engine.begin() as c:
            row = (
                await c.execute(
                    text(
                        "INSERT INTO accounts(id,tenant_id,display_name,unit,exposure_cap) VALUES (:id,:tenant,:name,:unit,:cap) RETURNING id,tenant_id,display_name,unit,exposure_cap,status,created_at"
                    ),
                    {
                        "id": account_id,
                        "tenant": tenant_id,
                        "name": display_name,
                        "unit": unit,
                        "cap": exposure_cap,
                    },
                )
            ).one()
            await c.execute(
                text(
                    "INSERT INTO account_exposures(account_id,tenant_id,unit) VALUES (:account,:tenant,:unit)"
                ),
                {"account": account_id, "tenant": tenant_id, "unit": unit},
            )
            await self._audit(c, actor, "account.created", account_id, "created", tenant_id)
        return self._account(row)

    async def update_account(
        self,
        account_id: str,
        display_name: str | None,
        exposure_cap: int | None,
        status: Status | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Account | None:
        async with self.engine.begin() as c:
            await c.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended('clearinghouse:exposure',0))")
            )
            exposure = (
                await c.execute(
                    text(
                        "SELECT open_lease_exposure FROM account_exposures "
                        "WHERE account_id=:id FOR UPDATE"
                    ),
                    {"id": account_id},
                )
            ).first()
            await c.execute(
                text("SELECT id FROM accounts WHERE id=:id FOR UPDATE"), {"id": account_id}
            )
            if (
                exposure_cap is not None
                and exposure is not None
                and exposure_cap < int(exposure.open_lease_exposure)
            ):
                raise Conflict("account exposure cap is below open lease exposure")
            row = (
                await c.execute(
                    text(
                        "UPDATE accounts SET display_name=COALESCE(:name,display_name), exposure_cap=COALESCE(:cap,exposure_cap), status=COALESCE(:status,status) WHERE id=:id RETURNING id,tenant_id,display_name,unit,exposure_cap,status,created_at"
                    ),
                    {
                        "id": account_id,
                        "name": display_name,
                        "cap": exposure_cap,
                        "status": status.value if status else None,
                    },
                )
            ).first()
            if row:
                await self._audit(
                    c,
                    actor,
                    "account.updated",
                    account_id,
                    reason or "metadata updated",
                    row.tenant_id,
                )
        return self._account(row) if row else None

    async def list_principals(self, tenant_id: str | None) -> Sequence[Principal]:
        query = (
            "SELECT p.id,p.tenant_id,p.account_id,p.display_name,p.status,p.created_at,COALESCE(array_agg(pr.role) FILTER (WHERE pr.role IS NOT NULL),'{}') roles FROM principals p LEFT JOIN principal_roles pr ON pr.principal_id=p.id"
            + (" WHERE p.tenant_id=:tenant" if tenant_id is not None else "")
            + " GROUP BY p.id ORDER BY p.id"
        )
        async with self.engine.connect() as c:
            rows = (await c.execute(text(query), {"tenant": tenant_id})).all()
        return tuple(
            Principal(
                r.id,
                r.tenant_id,
                r.account_id,
                r.display_name,
                Status(r.status),
                frozenset(Role(x) for x in r.roles),
                r.created_at,
            )
            for r in rows
        )

    async def get_principal(self, principal_id: str) -> Principal | None:
        query = "SELECT p.id,p.tenant_id,p.account_id,p.display_name,p.status,p.created_at,COALESCE(array_agg(pr.role) FILTER (WHERE pr.role IS NOT NULL),'{}') roles FROM principals p LEFT JOIN principal_roles pr ON pr.principal_id=p.id WHERE p.id=:id GROUP BY p.id"
        async with self.engine.connect() as c:
            r = (await c.execute(text(query), {"id": principal_id})).first()
        return (
            Principal(
                r.id,
                r.tenant_id,
                r.account_id,
                r.display_name,
                Status(r.status),
                frozenset(Role(x) for x in r.roles),
                r.created_at,
            )
            if r
            else None
        )

    async def create_principal(
        self,
        tenant_id: str | None,
        account_id: str | None,
        display_name: str | None,
        roles: frozenset[Role],
        actor: PrincipalContext,
    ) -> Principal:
        principal_id = opaque_id("principal")
        async with self.engine.begin() as c:
            row = (
                await c.execute(
                    text(
                        "INSERT INTO principals(id,tenant_id,account_id,display_name) VALUES (:id,:tenant,:account,:name) RETURNING created_at"
                    ),
                    {
                        "id": principal_id,
                        "tenant": tenant_id,
                        "account": account_id,
                        "name": display_name,
                    },
                )
            ).one()
            for role in roles:
                await c.execute(
                    text("INSERT INTO principal_roles(principal_id,role) VALUES (:id,:role)"),
                    {"id": principal_id, "role": role.value},
                )
            await self._audit(c, actor, "principal.created", principal_id, "created", tenant_id)
        return Principal(
            principal_id, tenant_id, account_id, display_name, Status.ACTIVE, roles, row.created_at
        )

    async def update_principal(
        self,
        principal_id: str,
        status: Status | None,
        roles: frozenset[Role] | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Principal | None:
        async with self.engine.begin() as c:
            changed = (
                await c.execute(
                    text(
                        "UPDATE principals SET status=COALESCE(:status,status) WHERE id=:id RETURNING id"
                    ),
                    {"id": principal_id, "status": status.value if status else None},
                )
            ).first()
            if not changed:
                return None
            if roles is not None:
                await c.execute(
                    text("DELETE FROM principal_roles WHERE principal_id=:id"), {"id": principal_id}
                )
                for role in roles:
                    await c.execute(
                        text("INSERT INTO principal_roles(principal_id,role) VALUES (:id,:role)"),
                        {"id": principal_id, "role": role.value},
                    )
            await self._audit(c, actor, "principal.updated", principal_id, reason or "updated")
        return await self.get_principal(principal_id)

    async def link_principal(
        self,
        principal_id: str,
        tenant_id: str,
        account_id: str | None,
        roles: frozenset[Role],
        reason: str,
        actor: PrincipalContext,
    ) -> Principal | None:
        async with self.engine.begin() as c:
            changed = (
                await c.execute(
                    text(
                        "UPDATE principals SET tenant_id=:tenant,account_id=:account WHERE id=:id AND tenant_id IS NULL AND account_id IS NULL RETURNING id"
                    ),
                    {"id": principal_id, "tenant": tenant_id, "account": account_id},
                )
            ).first()
            if not changed:
                return None
            await c.execute(
                text("DELETE FROM principal_roles WHERE principal_id=:id"),
                {"id": principal_id},
            )
            for role in roles:
                await c.execute(
                    text("INSERT INTO principal_roles(principal_id,role) VALUES (:id,:role)"),
                    {"id": principal_id, "role": role.value},
                )
            await self._audit(c, actor, "principal.scoped", principal_id, reason)
        return await self.get_principal(principal_id)

    async def list_credentials(
        self, principal_id: str | None, tenant_id: str | None = None
    ) -> Sequence[Credential]:
        predicates = []
        if principal_id:
            predicates.append("principal_id=:principal")
        if tenant_id:
            predicates.append("tenant_id=:tenant")
        query = (
            "SELECT id,account_id,principal_id,prefix,label,status,created_at,expires_at FROM credentials"
            + ((" WHERE " + " AND ".join(predicates)) if predicates else "")
            + " ORDER BY created_at"
        )
        async with self.engine.connect() as c:
            rows = (
                await c.execute(text(query), {"principal": principal_id, "tenant": tenant_id})
            ).all()
        return tuple(self._credential(r) for r in rows)

    async def get_credential(self, credential_id: str) -> Credential | None:
        async with self.engine.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT id,account_id,principal_id,prefix,label,status,created_at,expires_at FROM credentials WHERE id=:id"
                    ),
                    {"id": credential_id},
                )
            ).first()
        return self._credential(row) if row else None

    async def create_credential(
        self,
        account_id: str,
        principal_id: str,
        prefix: str,
        secret_hash: bytes,
        label: str,
        expires_at: datetime | None,
        actor: PrincipalContext,
    ) -> Credential:
        credential_id = opaque_id("cred")
        async with self.engine.begin() as c:
            row = (
                await c.execute(
                    text(
                        "INSERT INTO credentials(id,tenant_id,account_id,principal_id,prefix,secret_hash,label,expires_at) SELECT :id,a.tenant_id,:account,:principal,:prefix,:hash,:label,:expires FROM accounts a WHERE a.id=:account RETURNING id,account_id,principal_id,prefix,label,status,created_at,expires_at"
                    ),
                    {
                        "id": credential_id,
                        "account": account_id,
                        "principal": principal_id,
                        "prefix": prefix,
                        "hash": secret_hash,
                        "label": label,
                        "expires": expires_at,
                    },
                )
            ).one()
            await self._audit(c, actor, "credential.issued", credential_id, "issued")
        return self._credential(row)

    async def replace_credential(
        self, credential_id: str, prefix: str, secret_hash: bytes, actor: PrincipalContext
    ) -> Credential | None:
        replacement_id = opaque_id("cred")
        async with self.engine.begin() as c:
            row = (
                await c.execute(
                    text(
                        "WITH old AS (UPDATE credentials SET status='revoked',revoked_at=now() WHERE id=:old AND status='active' RETURNING *) INSERT INTO credentials(id,tenant_id,account_id,principal_id,prefix,secret_hash,label,expires_at,rotated_from_id) SELECT :new,tenant_id,account_id,principal_id,:prefix,:hash,label,expires_at,id FROM old RETURNING id,account_id,principal_id,prefix,label,status,created_at,expires_at"
                    ),
                    {
                        "old": credential_id,
                        "new": replacement_id,
                        "prefix": prefix,
                        "hash": secret_hash,
                    },
                )
            ).first()
            if row:
                await self._audit(c, actor, "credential.rotated", credential_id, replacement_id)
        return self._credential(row) if row else None

    async def revoke_credential(
        self, credential_id: str, actor: PrincipalContext
    ) -> Credential | None:
        async with self.engine.begin() as c:
            row = (
                await c.execute(
                    text(
                        "UPDATE credentials SET status='revoked',revoked_at=now() WHERE id=:id RETURNING id,account_id,principal_id,prefix,label,status,created_at,expires_at"
                    ),
                    {"id": credential_id},
                )
            ).first()
            if row:
                await self._audit(c, actor, "credential.revoked", credential_id, "revoked")
        return self._credential(row) if row else None

    async def credential_hash(self, prefix: str) -> tuple[Credential, bytes] | None:
        async with self.engine.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT id,account_id,principal_id,prefix,label,status,created_at,expires_at,secret_hash FROM credentials WHERE prefix=:prefix"
                    ),
                    {"prefix": prefix},
                )
            ).first()
        return (self._credential(row), bytes(row.secret_hash)) if row else None

    async def create_grant(
        self,
        account_id: str,
        kind: GrantKind,
        amount: int,
        unit: str,
        reason: str,
        external_reference: str | None,
        idempotency_key: str,
        request_hash: str,
        actor: PrincipalContext,
    ) -> Grant:
        scope = f"{actor.id}:grant.create"
        async with self.engine.begin() as c:
            await c.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended('clearinghouse:exposure',0))")
            )
            exposure = (
                await c.execute(
                    text(
                        "SELECT open_lease_exposure FROM account_exposures "
                        "WHERE account_id=:account FOR UPDATE"
                    ),
                    {"account": account_id},
                )
            ).one_or_none()
            account = (
                await c.execute(
                    text("SELECT tenant_id,unit FROM accounts WHERE id=:account FOR UPDATE"),
                    {"account": account_id},
                )
            ).one_or_none()
            await c.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"),
                {"lock": f"{scope}:{idempotency_key}"},
            )
            previous = (
                await c.execute(
                    text(
                        "SELECT request_hash,response_id FROM idempotency_keys WHERE actor_scope=:scope AND operation='grant.create' AND idempotency_key=:key"
                    ),
                    {"scope": str(actor.id), "key": idempotency_key},
                )
            ).first()
            if previous:
                if previous.request_hash != request_hash:
                    raise Conflict("idempotency key reused with different request")
                row = (
                    await c.execute(
                        text("SELECT * FROM grants WHERE id=:id"), {"id": previous.response_id}
                    )
                ).one()
                return Grant(
                    row.id,
                    row.account_id,
                    GrantKind(row.kind),
                    int(row.amount),
                    row.unit,
                    row.reason,
                    row.actor_id,
                    row.created_at,
                    row.external_reference,
                )
            if account is None or exposure is None:
                raise Conflict("account not found")
            current_posted = await c.scalar(
                text(
                    "SELECT COALESCE(sum(amount),0) FROM ledger_postings "
                    "WHERE tenant_id=:tenant AND account_code='payer:'||:account AND unit=:unit"
                ),
                {"tenant": account.tenant_id, "account": account_id, "unit": unit},
            )
            delta = grant_delta(kind, amount)
            if unit != account.unit or int(current_posted or 0) + delta < int(
                exposure.open_lease_exposure
            ):
                raise Conflict("grant would underfund open lease exposure")
            grant_id, transaction_id = opaque_id("grant"), opaque_id("ledger_tx")
            row = (
                await c.execute(
                    text(
                        "INSERT INTO grants(id,tenant_id,account_id,kind,amount,unit,reason,external_reference,actor_id,idempotency_key) SELECT :id,a.tenant_id,:account,:kind,:amount,:unit,:reason,:external,:actor,:key FROM accounts a WHERE a.id=:account RETURNING *"
                    ),
                    {
                        "id": grant_id,
                        "account": account_id,
                        "kind": kind.value,
                        "amount": amount,
                        "unit": unit,
                        "reason": reason,
                        "external": external_reference,
                        "actor": str(actor.id),
                        "key": idempotency_key,
                    },
                )
            ).one()
            await c.execute(
                text(
                    "INSERT INTO ledger_transactions(id,tenant_id,kind,source_id,actor_id) VALUES (:id,:tenant,'grant',:source,:actor)"
                ),
                {
                    "id": transaction_id,
                    "tenant": row.tenant_id,
                    "source": grant_id,
                    "actor": str(actor.id),
                },
            )
            await c.execute(
                text(
                    "INSERT INTO ledger_postings(id,transaction_id,tenant_id,account_code,amount,unit) VALUES (:a,:tx,:tenant,:payer,:delta,:unit),(:b,:tx,:tenant,'system:grants',:inverse,:unit)"
                ),
                {
                    "a": opaque_id("post"),
                    "b": opaque_id("post"),
                    "tx": transaction_id,
                    "tenant": row.tenant_id,
                    "payer": f"payer:{account_id}",
                    "delta": delta,
                    "inverse": -delta,
                    "unit": unit,
                },
            )
            await c.execute(
                text(
                    "INSERT INTO idempotency_keys(actor_scope,operation,idempotency_key,request_hash,response_id) VALUES (:scope,'grant.create',:key,:hash,:response)"
                ),
                {
                    "scope": str(actor.id),
                    "key": idempotency_key,
                    "hash": request_hash,
                    "response": grant_id,
                },
            )
            await self._audit(c, actor, "grant.created", grant_id, reason, row.tenant_id)
        return Grant(
            row.id,
            row.account_id,
            GrantKind(row.kind),
            int(row.amount),
            row.unit,
            row.reason,
            row.actor_id,
            row.created_at,
            row.external_reference,
        )

    async def list_grants(
        self,
        account_id: str | None,
        tenant_id: str | None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Sequence[Grant]:
        where = ""
        if account_id is not None and tenant_id is not None:
            where = " WHERE account_id=:account AND tenant_id=:tenant"
        elif account_id is not None:
            where = " WHERE account_id=:account"
        elif tenant_id is not None:
            where = " WHERE tenant_id=:tenant"
        params: dict[str, object] = {"account": account_id, "tenant": tenant_id, "limit": limit + 1}
        if cursor is not None:
            cursor_time, cursor_id = _decode_cursor(cursor)
            where += " AND" if where else " WHERE"
            where += " (created_at,id)<(:cursor_time,:cursor_id)"
            params.update(cursor_time=cursor_time, cursor_id=cursor_id)
        async with self.engine.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT * FROM grants"
                        + where
                        + " ORDER BY created_at DESC,id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).all()
        return tuple(
            Grant(
                row.id,
                row.account_id,
                GrantKind(row.kind),
                int(row.amount),
                row.unit,
                row.reason,
                row.actor_id,
                row.created_at,
                row.external_reference,
            )
            for row in rows
        )

    async def balance(self, account_id: str) -> Balance | None:
        async with self.engine.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT a.id account_id,a.unit,COALESCE(sum(p.amount),0) posted,e.open_lease_exposure FROM accounts a JOIN account_exposures e ON e.account_id=a.id LEFT JOIN ledger_postings p ON p.tenant_id=a.tenant_id AND p.account_code='payer:'||a.id WHERE a.id=:id GROUP BY a.id,a.unit,e.open_lease_exposure"
                    ),
                    {"id": account_id},
                )
            ).first()
        return (
            Balance(
                row.account_id,
                int(row.posted),
                int(row.open_lease_exposure),
                int(row.posted) - int(row.open_lease_exposure),
                row.unit,
            )
            if row
            else None
        )

    async def list_rate_cards(self) -> Sequence[RateCard]:
        async with self.engine.connect() as c:
            rows = (
                await c.execute(text("SELECT * FROM rate_cards ORDER BY capability,model,version"))
            ).all()
        return tuple(
            RateCard(
                r.id,
                int(r.version),
                r.capability,
                r.model,
                ExactRate(int(r.numerator), int(r.denominator), r.charge_unit, r.quantity_unit),
                r.effective_at,
                r.created_at,
            )
            for r in rows
        )

    async def create_rate_card(
        self,
        capability: str,
        model: str | None,
        rate: ExactRate,
        effective_at: datetime,
        actor: PrincipalContext,
    ) -> RateCard:
        card_id = opaque_id("rate")
        async with self.engine.begin() as c:
            await c.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended('clearinghouse:exposure',0))")
            )
            await c.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock,0))"),
                {"lock": f"rate:{capability}:{model or ''}"},
            )
            row = (
                await c.execute(
                    text(
                        "INSERT INTO rate_cards(id,version,capability,model,numerator,denominator,charge_unit,quantity_unit,effective_at) VALUES (:id,(SELECT COALESCE(max(version),0)+1 FROM rate_cards WHERE capability=:capability AND model IS NOT DISTINCT FROM :model),:capability,:model,:numerator,:denominator,:charge_unit,:quantity_unit,:effective) RETURNING *"
                    ),
                    {
                        "id": card_id,
                        "capability": capability,
                        "model": model,
                        "numerator": rate.numerator,
                        "denominator": rate.denominator,
                        "charge_unit": rate.charge_unit,
                        "quantity_unit": rate.quantity_unit,
                        "effective": effective_at,
                    },
                )
            ).one()
            await self._audit(c, actor, "rate_card.created", card_id, "published")
        return RateCard(
            row.id,
            int(row.version),
            row.capability,
            row.model,
            rate,
            row.effective_at,
            row.created_at,
        )

    async def catalog(self, account_id: str) -> Sequence[CatalogEntry]:
        async with self.engine.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT DISTINCT ON (r.capability,r.model) r.*,p.allowed FROM rate_cards r JOIN account_capability_policies p ON p.account_id=:account AND p.capability=r.capability AND p.model=COALESCE(r.model,'') WHERE r.effective_at<=now() AND p.allowed ORDER BY r.capability,r.model,r.version DESC"
                    ),
                    {"account": account_id},
                )
            ).all()
        return tuple(
            CatalogEntry(
                r.capability,
                r.model,
                ExactRate(int(r.numerator), int(r.denominator), r.charge_unit, r.quantity_unit),
                bool(r.allowed),
            )
            for r in rows
        )

    async def set_capability_policy(
        self,
        account_id: str,
        capability: str,
        model: str | None,
        allowed: bool,
        reason: str,
        actor: PrincipalContext,
    ) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended('clearinghouse:exposure',0))")
            )
            account = (
                await c.execute(
                    text("SELECT id FROM accounts WHERE id=:id FOR UPDATE"), {"id": account_id}
                )
            ).one_or_none()
            if account is None:
                raise Conflict("account does not exist")
            await c.execute(
                text(
                    "INSERT INTO account_capability_policies(account_id,capability,model,allowed) VALUES (:account,:capability,:model,:allowed) ON CONFLICT (account_id,capability,model) DO UPDATE SET allowed=excluded.allowed"
                ),
                {
                    "account": account_id,
                    "capability": capability,
                    "model": model or "",
                    "allowed": allowed,
                },
            )
            await self._audit(c, actor, "account.capability_policy", account_id, reason)
