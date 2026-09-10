"""Account administration workflows and their persistence port."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from clearinghouse.domain.accounts import (
    Account,
    AccountId,
    Balance,
    CatalogEntry,
    Credential,
    ExactRate,
    Grant,
    GrantKind,
    IssuedCredential,
    Principal,
    PrincipalContext,
    PrincipalId,
    RateCard,
    Role,
    Status,
    Tenant,
    TenantId,
    grant_delta,
)


class DomainError(Exception):
    """Typed application failure translated only by inbound adapters."""


class Forbidden(DomainError):
    pass


class NotFound(DomainError):
    pass


class Conflict(DomainError):
    pass


class InvalidRequest(DomainError):
    pass


class AccountsRepository(Protocol):
    async def list_tenants(
        self, tenant_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[Tenant]: ...
    async def get_tenant(self, tenant_id: str) -> Tenant | None: ...
    async def create_tenant(self, display_name: str, actor: PrincipalContext) -> Tenant: ...
    async def update_tenant(
        self,
        tenant_id: str,
        display_name: str | None,
        status: Status | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Tenant | None: ...
    async def list_accounts(
        self, tenant_id: str | None, limit: int, cursor: str | None
    ) -> Sequence[Account]: ...
    async def get_account(self, account_id: str) -> Account | None: ...
    async def create_account(
        self,
        tenant_id: str,
        display_name: str,
        unit: str,
        exposure_cap: int,
        actor: PrincipalContext,
    ) -> Account: ...
    async def update_account(
        self,
        account_id: str,
        display_name: str | None,
        exposure_cap: int | None,
        status: Status | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Account | None: ...
    async def list_principals(self, tenant_id: str | None) -> Sequence[Principal]: ...
    async def get_principal(self, principal_id: str) -> Principal | None: ...
    async def create_principal(
        self,
        tenant_id: str | None,
        account_id: str | None,
        display_name: str | None,
        roles: frozenset[Role],
        actor: PrincipalContext,
    ) -> Principal: ...
    async def update_principal(
        self,
        principal_id: str,
        status: Status | None,
        roles: frozenset[Role] | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Principal | None: ...
    async def link_principal(
        self,
        principal_id: str,
        tenant_id: str,
        account_id: str | None,
        roles: frozenset[Role],
        reason: str,
        actor: PrincipalContext,
    ) -> Principal | None: ...
    async def list_credentials(
        self, principal_id: str | None, tenant_id: str | None = None
    ) -> Sequence[Credential]: ...
    async def get_credential(self, credential_id: str) -> Credential | None: ...
    async def create_credential(
        self,
        account_id: str,
        principal_id: str,
        prefix: str,
        secret_hash: bytes,
        label: str,
        expires_at: datetime | None,
        actor: PrincipalContext,
    ) -> Credential: ...
    async def replace_credential(
        self, credential_id: str, prefix: str, secret_hash: bytes, actor: PrincipalContext
    ) -> Credential | None: ...
    async def revoke_credential(
        self, credential_id: str, actor: PrincipalContext
    ) -> Credential | None: ...
    async def credential_hash(self, prefix: str) -> tuple[Credential, bytes] | None: ...
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
    ) -> Grant: ...
    async def list_grants(
        self,
        account_id: str | None,
        tenant_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> Sequence[Grant]: ...
    async def balance(self, account_id: str) -> Balance | None: ...
    async def list_rate_cards(self) -> Sequence[RateCard]: ...
    async def create_rate_card(
        self,
        capability: str,
        model: str | None,
        rate: ExactRate,
        effective_at: datetime,
        actor: PrincipalContext,
    ) -> RateCard: ...
    async def catalog(self, account_id: str) -> Sequence[CatalogEntry]: ...
    async def set_capability_policy(
        self,
        account_id: str,
        capability: str,
        model: str | None,
        allowed: bool,
        reason: str,
        actor: PrincipalContext,
    ) -> None: ...


class AccountService:
    def __init__(self, repository: AccountsRepository, credential_pepper: bytes) -> None:
        if len(credential_pepper) < 16:
            raise ValueError("credential pepper must be at least 16 bytes")
        self.repository = repository
        self._pepper = credential_pepper

    @staticmethod
    def _require_operator(actor: PrincipalContext) -> None:
        if not actor.is_operator:
            raise Forbidden("operator role required")

    @staticmethod
    def _require_tenant(actor: PrincipalContext, tenant_id: str) -> None:
        if not actor.can_access_tenant(tenant_id):
            raise Forbidden("tenant access denied")

    async def list_tenants(
        self,
        actor: PrincipalContext,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Sequence[Tenant]:
        tenant_id = None if actor.is_operator else str(actor.tenant_id or "")
        return await self.repository.list_tenants(tenant_id, limit, cursor)

    async def get_tenant(self, tenant_id: str, actor: PrincipalContext) -> Tenant:
        self._require_tenant(actor, tenant_id)
        tenant = await self.repository.get_tenant(tenant_id)
        if tenant is None:
            raise NotFound("tenant not found")
        return tenant

    async def create_tenant(self, display_name: str, actor: PrincipalContext) -> Tenant:
        self._require_operator(actor)
        return await self.repository.create_tenant(display_name, actor)

    async def update_tenant(
        self,
        tenant_id: str,
        display_name: str | None,
        status: Status | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Tenant:
        self._require_operator(actor)
        if status is not None and not reason:
            raise InvalidRequest("status changes require a reason")
        tenant = await self.repository.update_tenant(tenant_id, display_name, status, reason, actor)
        if tenant is None:
            raise NotFound("tenant not found")
        return tenant

    async def list_accounts(
        self,
        tenant_id: str | None,
        actor: PrincipalContext,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Sequence[Account]:
        if not actor.is_operator:
            tenant_id = str(actor.tenant_id) if actor.tenant_id else ""
        elif tenant_id is not None:
            self._require_tenant(actor, tenant_id)
        return await self.repository.list_accounts(tenant_id, limit, cursor)

    async def get_account(self, account_id: str, actor: PrincipalContext) -> Account:
        account = await self.repository.get_account(account_id)
        if account is None:
            raise NotFound("account not found")
        if not actor.can_access_account(account.tenant_id, account.id):
            raise NotFound("account not found")
        return account

    async def create_account(
        self,
        tenant_id: str,
        display_name: str,
        unit: str,
        exposure_cap: int,
        actor: PrincipalContext,
    ) -> Account:
        self._require_tenant(actor, tenant_id)
        if not (actor.is_operator or Role.TENANT_ADMIN in actor.roles):
            raise Forbidden("tenant administrator role required")
        return await self.repository.create_account(
            tenant_id, display_name, unit, exposure_cap, actor
        )

    async def update_account(
        self,
        account_id: str,
        display_name: str | None,
        exposure_cap: int | None,
        status: Status | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Account:
        account = await self.get_account(account_id, actor)
        if not (actor.is_operator or Role.TENANT_ADMIN in actor.roles):
            raise Forbidden("tenant administrator role required")
        if (status is not None or exposure_cap is not None) and not reason:
            raise InvalidRequest("cap and status changes require a reason")
        updated = await self.repository.update_account(
            account.id, display_name, exposure_cap, status, reason, actor
        )
        if updated is None:
            raise NotFound("account not found")
        return updated

    async def list_principals(
        self, tenant_id: str | None, actor: PrincipalContext
    ) -> Sequence[Principal]:
        if actor.is_operator:
            return await self.repository.list_principals(tenant_id)
        if Role.TENANT_ADMIN in actor.roles:
            return await self.repository.list_principals(str(actor.tenant_id))
        principal = await self.repository.get_principal(str(actor.id))
        return (principal,) if principal is not None else ()

    async def create_principal(
        self,
        tenant_id: str | None,
        account_id: str | None,
        display_name: str | None,
        roles: frozenset[Role],
        actor: PrincipalContext,
    ) -> Principal:
        if tenant_id is None:
            self._require_operator(actor)
        else:
            self._require_tenant(actor, tenant_id)
            if not (actor.is_operator or Role.TENANT_ADMIN in actor.roles):
                raise Forbidden("tenant administrator role required")
        if Role.OPERATOR in roles and not actor.is_operator:
            raise Forbidden("only operators may grant operator role")
        if Role.OPERATOR in roles and (tenant_id is not None or account_id is not None):
            raise InvalidRequest("operators must be unscoped")
        if not actor.is_operator and roles != frozenset({Role.CREDENTIAL_HOLDER}):
            raise Forbidden("tenant administrators may create credential holders only")
        if Role.CREDENTIAL_HOLDER in roles and account_id is None:
            raise InvalidRequest("credential holders require an account")
        return await self.repository.create_principal(
            tenant_id, account_id, display_name, roles, actor
        )

    async def update_principal(
        self,
        principal_id: str,
        status: Status | None,
        roles: frozenset[Role] | None,
        reason: str | None,
        actor: PrincipalContext,
    ) -> Principal:
        if principal_id == "principal_system_metering":
            raise NotFound("principal not found")
        existing = await self.repository.get_principal(principal_id)
        if existing is None:
            raise NotFound("principal not found")
        if not (actor.is_operator or Role.TENANT_ADMIN in actor.roles):
            raise Forbidden("principal administration denied")
        if str(actor.id) == principal_id:
            raise Forbidden("principals may not change their own role or status")
        if existing.tenant_id is None:
            self._require_operator(actor)
        else:
            self._require_tenant(actor, existing.tenant_id)
        if not reason:
            raise InvalidRequest("principal changes require a reason")
        if roles and Role.OPERATOR in roles and not actor.is_operator:
            raise Forbidden("only operators may grant operator role")
        if (
            not actor.is_operator
            and roles is not None
            and roles != frozenset({Role.CREDENTIAL_HOLDER})
        ):
            raise Forbidden("tenant administrators may assign credential holder only")
        updated = await self.repository.update_principal(principal_id, status, roles, reason, actor)
        if updated is None:
            raise NotFound("principal not found")
        return updated

    async def link_principal(
        self,
        principal_id: str,
        tenant_id: str,
        account_id: str | None,
        roles: frozenset[Role],
        reason: str,
        actor: PrincipalContext,
    ) -> Principal:
        self._require_operator(actor)
        if principal_id == "principal_system_metering":
            raise NotFound("principal not found")
        existing = await self.repository.get_principal(principal_id)
        if existing is None:
            raise NotFound("principal not found")
        if existing.tenant_id is not None or existing.account_id is not None:
            raise Conflict("principal is already scoped")
        if Role.OPERATOR in roles:
            raise InvalidRequest("scoped principals cannot be operators")
        if Role.CREDENTIAL_HOLDER in roles and account_id is None:
            raise InvalidRequest("credential holders require an account")
        account = await self.repository.get_account(account_id) if account_id else None
        if account_id and (account is None or account.tenant_id != tenant_id):
            raise InvalidRequest("account does not belong to tenant")
        linked = await self.repository.link_principal(
            principal_id, tenant_id, account_id, roles, reason, actor
        )
        if linked is None:
            raise Conflict("principal could not be linked")
        return linked

    def _new_secret(self) -> tuple[str, str, bytes]:
        secret = f"och_live_{secrets.token_urlsafe(32)}"
        prefix = secret[:17]
        return (
            secret,
            prefix,
            hashlib.blake2b(secret.encode(), key=self._pepper, digest_size=32).digest(),
        )

    async def issue_credential(
        self,
        account_id: str,
        principal_id: str,
        label: str,
        expires_at: datetime | None,
        actor: PrincipalContext,
    ) -> IssuedCredential:
        if principal_id == "principal_system_metering":
            raise InvalidRequest("principal does not belong to account")
        await self.get_account(account_id, actor)
        if not (actor.is_operator or Role.TENANT_ADMIN in actor.roles or actor.id == principal_id):
            raise Forbidden("credential issuance denied")
        if expires_at is not None and (
            expires_at.tzinfo is None or expires_at <= datetime.now(UTC)
        ):
            raise InvalidRequest("credential expiry must be a future absolute timestamp")
        principal = await self.repository.get_principal(principal_id)
        if principal is None or principal.account_id != account_id:
            raise InvalidRequest("principal does not belong to account")
        secret, prefix, digest = self._new_secret()
        credential = await self.repository.create_credential(
            account_id, principal_id, prefix, digest, label, expires_at, actor
        )
        return IssuedCredential(credential, secret)

    async def list_credentials(self, actor: PrincipalContext) -> Sequence[Credential]:
        if actor.is_operator:
            return await self.repository.list_credentials(None)
        if Role.TENANT_ADMIN in actor.roles:
            return await self.repository.list_credentials(None, str(actor.tenant_id))
        return await self.repository.list_credentials(str(actor.id), str(actor.tenant_id))

    async def rotate_credential(
        self, credential_id: str, actor: PrincipalContext
    ) -> IssuedCredential:
        existing = await self.repository.get_credential(credential_id)
        if existing is None:
            raise NotFound("credential not found")
        await self.get_account(existing.account_id, actor)
        if not (
            actor.is_operator
            or Role.TENANT_ADMIN in actor.roles
            or actor.id == existing.principal_id
        ):
            raise Forbidden("credential rotation denied")
        secret, prefix, digest = self._new_secret()
        replacement = await self.repository.replace_credential(credential_id, prefix, digest, actor)
        if replacement is None:
            raise NotFound("credential not found")
        return IssuedCredential(replacement, secret)

    async def revoke_credential(self, credential_id: str, actor: PrincipalContext) -> None:
        existing = await self.repository.get_credential(credential_id)
        if existing is None:
            raise NotFound("credential not found")
        await self.get_account(existing.account_id, actor)
        if not (
            actor.is_operator
            or Role.TENANT_ADMIN in actor.roles
            or actor.id == existing.principal_id
        ):
            raise Forbidden("credential revocation denied")
        await self.repository.revoke_credential(credential_id, actor)

    async def authenticate_credential(self, secret: str) -> Credential | None:
        match = await self.repository.credential_hash(secret[:17])
        if match is None:
            return None
        credential, stored_hash = match
        supplied = hashlib.blake2b(secret.encode(), key=self._pepper, digest_size=32).digest()
        if not hmac.compare_digest(supplied, stored_hash) or credential.status != "active":
            return None
        if credential.expires_at is not None and credential.expires_at <= datetime.now(
            credential.expires_at.tzinfo
        ):
            return None
        account = await self.repository.get_account(credential.account_id)
        principal = await self.repository.get_principal(credential.principal_id)
        if account is None or principal is None:
            return None
        tenant = await self.repository.get_tenant(account.tenant_id)
        if tenant is None or any(
            item.status is not Status.ACTIVE for item in (tenant, account, principal)
        ):
            return None
        return credential

    async def authenticate_credential_context(
        self, secret: str
    ) -> tuple[PrincipalContext, str] | None:
        credential = await self.authenticate_credential(secret)
        if credential is None:
            return None
        account = await self.repository.get_account(credential.account_id)
        principal = await self.repository.get_principal(credential.principal_id)
        if account is None or principal is None:
            return None
        return (
            PrincipalContext(
                id=PrincipalId(principal.id),
                tenant_id=TenantId(account.tenant_id),
                account_id=AccountId(account.id),
                roles=principal.roles,
            ),
            credential.id,
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
        actor: PrincipalContext,
    ) -> Grant:
        self._require_operator(actor)
        account = await self.get_account(account_id, actor)
        if account.unit != unit:
            raise InvalidRequest("grant unit must match account unit")
        try:
            grant_delta(kind, amount)
        except ValueError as error:
            raise InvalidRequest(str(error)) from error
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "account_id": account_id,
                    "kind": kind,
                    "amount": str(amount),
                    "unit": unit,
                    "reason": reason,
                    "external_reference": external_reference,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return await self.repository.create_grant(
            account_id,
            kind,
            amount,
            unit,
            reason,
            external_reference,
            idempotency_key,
            request_hash,
            actor,
        )

    async def list_grants(
        self,
        account_id: str | None,
        actor: PrincipalContext,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Sequence[Grant]:
        tenant_id: str | None = None
        if account_id is not None:
            account = await self.get_account(account_id, actor)
            tenant_id = account.tenant_id
        elif actor.is_operator:
            tenant_id = None
        elif Role.TENANT_ADMIN in actor.roles:
            tenant_id = str(actor.tenant_id)
        elif actor.account_id is not None:
            account_id = str(actor.account_id)
            tenant_id = str(actor.tenant_id)
        else:
            return ()
        return await self.repository.list_grants(account_id, tenant_id, limit, cursor)

    async def balance(self, account_id: str, actor: PrincipalContext) -> Balance:
        await self.get_account(account_id, actor)
        balance = await self.repository.balance(account_id)
        if balance is None:
            raise NotFound("account not found")
        return balance

    async def list_rate_cards(self, actor: PrincipalContext) -> Sequence[RateCard]:
        self._require_operator(actor)
        return await self.repository.list_rate_cards()

    async def create_rate_card(
        self,
        capability: str,
        model: str | None,
        rate: ExactRate,
        effective_at: datetime,
        actor: PrincipalContext,
    ) -> RateCard:
        self._require_operator(actor)
        if effective_at.tzinfo is None:
            raise InvalidRequest("rate effective time must include a timezone")
        return await self.repository.create_rate_card(capability, model, rate, effective_at, actor)

    async def catalog(self, account_id: str, actor: PrincipalContext) -> Sequence[CatalogEntry]:
        account = await self.get_account(account_id, actor)
        tenant = await self.get_tenant(account.tenant_id, actor)
        entries = await self.repository.catalog(account_id)
        if account.status is Status.ACTIVE and tenant.status is Status.ACTIVE:
            return entries
        return tuple(
            CatalogEntry(entry.capability, entry.model, entry.rate, False) for entry in entries
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
        await self.get_account(account_id, actor)
        if not (actor.is_operator or Role.TENANT_ADMIN in actor.roles):
            raise Forbidden("tenant administrator role required")
        await self.repository.set_capability_policy(
            account_id, capability, model, allowed, reason, actor
        )
