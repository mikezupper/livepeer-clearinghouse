"""PostgreSQL adapter for explicit identity onboarding."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clearinghouse.domain.accounts import PrincipalContext, Role
from clearinghouse.domain.auth import AuthProvider
from clearinghouse.domain.onboarding import (
    IdentityInvitation,
    IdentityLink,
    InvalidInvitation,
    OnboardingConflict,
    OnboardingForbidden,
    OnboardingNotFound,
    OperatorBootstrap,
)


def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


@asynccontextmanager
async def _translate_concurrency() -> AsyncIterator[None]:
    try:
        yield
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) in {"40001", "40P01"}:
            raise OnboardingConflict("concurrent onboarding state changed; retry") from error
        raise


class PostgresOnboardingRepository:
    """Serialize invitation redemption and first-operator creation in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @staticmethod
    async def _lock_identity_source(session: AsyncSession, source_principal_id: str) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:source, 20260909))"),
            {"source": source_principal_id},
        )

    @staticmethod
    async def _principal(session: AsyncSession, principal_id: str) -> Mapping[str, Any] | None:
        result = await session.execute(
            text(
                """
                SELECT p.id, p.tenant_id, p.account_id, p.status
                FROM principals p
                WHERE p.id = :id
                FOR UPDATE OF p
                """
            ),
            {"id": principal_id},
        )
        principal = result.mappings().one_or_none()
        if principal is None:
            return None
        roles = (
            (
                await session.execute(
                    text("SELECT role FROM principal_roles WHERE principal_id=:id ORDER BY role"),
                    {"id": principal_id},
                )
            )
            .scalars()
            .all()
        )
        return {**principal, "roles": roles}

    @staticmethod
    async def _audit(
        session: AsyncSession,
        *,
        tenant_id: str | None,
        actor_id: str,
        action: str,
        target_id: str,
        reason: str,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO audit_events
                    (id, tenant_id, actor_id, action, target_id, reason, request_id)
                VALUES (:id, :tenant, :actor, :action, :target, :reason, :request)
                """
            ),
            {
                "id": _opaque_id("audit"),
                "tenant": tenant_id,
                "actor": actor_id,
                "action": action,
                "target": target_id,
                "reason": reason,
                "request": _opaque_id("request"),
            },
        )

    async def create_identity_invitation(
        self,
        *,
        invitation_id: str,
        source_principal_id: str,
        principal_id: str,
        secret_prefix: str,
        secret_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
        reason: str,
        actor: PrincipalContext,
    ) -> IdentityInvitation:
        async with _translate_concurrency(), self._sessions.begin() as session:
            await self._lock_identity_source(session, source_principal_id)
            source = await self._principal(session, source_principal_id)
            if source is None:
                raise OnboardingNotFound("source principal not found")
            target = await self._principal(session, principal_id)
            if target is None:
                raise OnboardingNotFound("principal not found")
            issuer = await self._principal(session, str(actor.id))
            if issuer is None or issuer["status"] != "active":
                raise OnboardingForbidden("invitation issuer is not active")
            issuer_roles = frozenset(Role(role) for role in issuer["roles"])
            target_roles = frozenset(Role(role) for role in target["roles"])
            if target["tenant_id"] is None or target["status"] != "active" or not target_roles:
                raise OnboardingConflict("target principal is not active and scoped")
            issuer_is_operator = issuer_roles == frozenset({Role.OPERATOR})
            if not issuer_is_operator and (
                Role.TENANT_ADMIN not in issuer_roles
                or issuer["tenant_id"] != target["tenant_id"]
                or target_roles != frozenset({Role.CREDENTIAL_HOLDER})
            ):
                raise OnboardingNotFound("principal not found")
            source_identity_count = await session.scalar(
                text("SELECT count(*) FROM auth_identities WHERE principal_id=:principal"),
                {"principal": source_principal_id},
            )
            if (
                source["tenant_id"] is not None
                or source["account_id"] is not None
                or source["roles"]
                or source["status"] != "active"
                or source_identity_count != 1
                or source_principal_id == principal_id
            ):
                raise OnboardingNotFound("source principal not found")
            issuer_authority = "operator" if issuer_is_operator else "tenant_admin"
            await session.execute(
                text(
                    """
                    UPDATE auth_identity_invitations SET superseded_at=:created
                    WHERE source_principal_id=:source
                      AND consumed_at IS NULL AND superseded_at IS NULL
                    """
                ),
                {"created": created_at, "source": source_principal_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO auth_identity_invitations
                        (id, source_principal_id, target_principal_id, tenant_id,
                         target_account_id, target_roles, target_status,
                         issued_by, issuer_authority,
                         secret_prefix, secret_hash, reason, expires_at, created_at)
                    VALUES (:id, :source, :target, :tenant, :account, :roles, :status,
                            :actor, :authority, :prefix, :hash, :reason, :expires, :created)
                    """
                ),
                {
                    "id": invitation_id,
                    "source": source_principal_id,
                    "target": principal_id,
                    "tenant": target["tenant_id"],
                    "account": target["account_id"],
                    "roles": ",".join(sorted(role.value for role in target_roles)),
                    "status": target["status"],
                    "actor": str(actor.id),
                    "authority": issuer_authority,
                    "prefix": secret_prefix,
                    "hash": secret_hash,
                    "reason": reason,
                    "expires": expires_at,
                    "created": created_at,
                },
            )
            await self._audit(
                session,
                tenant_id=target["tenant_id"],
                actor_id=str(actor.id),
                action="auth_identity.invited",
                target_id=principal_id,
                reason=reason,
            )
            return IdentityInvitation(
                invitation_id,
                source_principal_id,
                principal_id,
                target["tenant_id"],
                expires_at,
                created_at,
            )

    async def redeem_identity_invitation(
        self,
        *,
        secret_prefix: str,
        secret_hash: bytes,
        now: datetime,
        actor: PrincipalContext,
    ) -> IdentityLink:
        async with _translate_concurrency(), self._sessions.begin() as session:
            candidate_result = await session.execute(
                text(
                    """
                    SELECT * FROM auth_identity_invitations
                    WHERE secret_prefix = :prefix
                    """
                ),
                {"prefix": secret_prefix},
            )
            candidate = candidate_result.mappings().one_or_none()
            if (
                candidate is None
                or candidate["source_principal_id"] != str(actor.id)
                or not hmac.compare_digest(bytes(candidate["secret_hash"]), secret_hash)
            ):
                raise InvalidInvitation("invalid or expired identity invitation")
            await self._lock_identity_source(session, candidate["source_principal_id"])
            result = await session.execute(
                text(
                    """
                    SELECT * FROM auth_identity_invitations
                    WHERE id = :id
                    FOR UPDATE
                    """
                ),
                {"id": candidate["id"]},
            )
            invitation = result.mappings().one_or_none()
            if (
                invitation is None
                or invitation["consumed_at"] is not None
                or invitation["superseded_at"] is not None
                or invitation["expires_at"] <= now
                or not hmac.compare_digest(bytes(invitation["secret_hash"]), secret_hash)
            ):
                raise InvalidInvitation("invalid or expired identity invitation")

            source = await self._principal(session, str(actor.id))
            target = await self._principal(session, invitation["target_principal_id"])
            issuer = await self._principal(session, invitation["issued_by"])
            if source is None or target is None or issuer is None:
                raise InvalidInvitation("invalid or expired identity invitation")
            if (
                source["tenant_id"] is not None
                or source["account_id"] is not None
                or source["roles"]
                or source["status"] != "active"
            ):
                raise OnboardingForbidden("identity principal is already scoped")
            target_roles = ",".join(sorted(str(role) for role in target["roles"]))
            if (
                target["tenant_id"] != invitation["tenant_id"]
                or target["account_id"] != invitation["target_account_id"]
                or target_roles != invitation["target_roles"]
                or target["status"] != invitation["target_status"]
                or target["status"] != "active"
            ):
                raise OnboardingConflict("invitation target is no longer usable")
            issuer_roles = frozenset(Role(role) for role in issuer["roles"])
            issuer_valid = issuer["status"] == "active"
            if invitation["issuer_authority"] == "operator":
                issuer_valid = issuer_valid and issuer_roles == frozenset({Role.OPERATOR})
            else:
                issuer_valid = (
                    issuer_valid
                    and Role.TENANT_ADMIN in issuer_roles
                    and issuer["tenant_id"] == invitation["tenant_id"]
                    and frozenset(Role(role) for role in target["roles"])
                    == frozenset({Role.CREDENTIAL_HOLDER})
                )
            if not issuer_valid:
                raise OnboardingConflict("invitation issuer is no longer authorized")
            identities = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT id, provider FROM auth_identities
                        WHERE principal_id = :principal
                        ORDER BY id FOR UPDATE
                        """
                        ),
                        {"principal": str(actor.id)},
                    )
                )
                .mappings()
                .all()
            )
            if len(identities) != 1:
                raise OnboardingConflict("identity ownership is ambiguous")
            identity = identities[0]
            existing_link = await session.scalar(
                text("SELECT target_principal_id FROM auth_identity_links WHERE identity_id=:id"),
                {"id": identity["id"]},
            )
            if existing_link is not None:
                raise OnboardingConflict("identity is already linked")

            link_id = _opaque_id("link")
            await session.execute(
                text("UPDATE auth_identities SET principal_id=:target WHERE id=:identity"),
                {"target": target["id"], "identity": identity["id"]},
            )
            await session.execute(
                text("UPDATE principals SET status='suspended' WHERE id=:source"),
                {"source": source["id"]},
            )
            await session.execute(
                text(
                    "UPDATE auth_browser_sessions SET revoked_at=COALESCE(revoked_at,:now) "
                    "WHERE principal_id=:source"
                ),
                {"now": now, "source": source["id"]},
            )
            await session.execute(
                text(
                    """
                    UPDATE auth_identity_invitations
                    SET consumed_at=:now, identity_id=:identity
                    WHERE id=:id
                    """
                ),
                {"now": now, "identity": identity["id"], "id": invitation["id"]},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO auth_identity_links
                        (id, identity_id, source_principal_id, target_principal_id,
                         tenant_id, actor_id, invitation_id, reason, linked_at)
                    VALUES (:id, :identity, :source, :target, :tenant, :actor,
                            :invitation, :reason, :linked)
                    """
                ),
                {
                    "id": link_id,
                    "identity": identity["id"],
                    "source": source["id"],
                    "target": target["id"],
                    "tenant": target["tenant_id"],
                    "actor": source["id"],
                    "invitation": invitation["id"],
                    "reason": invitation["reason"],
                    "linked": now,
                },
            )
            await self._audit(
                session,
                tenant_id=target["tenant_id"],
                actor_id=source["id"],
                action="auth_identity.linked",
                target_id=target["id"],
                reason=invitation["reason"],
            )
            return IdentityLink(
                link_id,
                identity["id"],
                AuthProvider(identity["provider"]),
                target["id"],
                target["tenant_id"],
                now,
            )

    async def bootstrap_operator(
        self, *, normalized_email: str, configuration_hash: str
    ) -> OperatorBootstrap:
        async with _translate_concurrency(), self._sessions.begin() as session:
            await session.execute(text("LOCK TABLE principal_roles IN SHARE ROW EXCLUSIVE MODE"))
            await session.execute(text("LOCK TABLE auth_identities IN SHARE ROW EXCLUSIVE MODE"))
            record = (
                (await session.execute(text("SELECT * FROM operator_bootstrap WHERE singleton")))
                .mappings()
                .one_or_none()
            )
            if record is not None:
                if not hmac.compare_digest(record["configuration_hash"], configuration_hash):
                    raise OnboardingConflict("operator bootstrap configuration conflicts")
                valid = await session.scalar(
                    text(
                        """
                        SELECT EXISTS(SELECT 1 FROM principals p
                          WHERE p.id=:principal AND p.status='active'
                            AND p.tenant_id IS NULL AND p.account_id IS NULL)
                          AND (SELECT count(*) FROM principal_roles
                               WHERE principal_id=:principal AND role='operator')=1
                          AND (SELECT count(*) FROM principal_roles
                               WHERE principal_id=:principal)=1
                          AND (SELECT count(*) FROM auth_identities
                               WHERE principal_id=:principal AND provider='email'
                                 AND provider_subject=:email)=1
                          AND (SELECT count(*) FROM auth_identities
                               WHERE principal_id=:principal)=1
                        """
                    ),
                    {"principal": record["principal_id"], "email": normalized_email},
                )
                if valid is not True:
                    raise OnboardingConflict("operator bootstrap state is inconsistent")
                return OperatorBootstrap(record["principal_id"], False)

            operator_count = await session.scalar(
                text("SELECT count(*) FROM principal_roles WHERE role='operator'")
            )
            if operator_count != 0:
                raise OnboardingConflict("operator bootstrap requires zero existing operators")
            identity = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT id, principal_id FROM auth_identities
                        WHERE provider='email' AND provider_subject=:email FOR UPDATE
                        """
                        ),
                        {"email": normalized_email},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if identity is None:
                principal_id = _opaque_id("prn")
                await session.execute(
                    text(
                        "INSERT INTO principals(id,tenant_id,account_id,display_name,status) "
                        "VALUES (:id,NULL,NULL,NULL,'active')"
                    ),
                    {"id": principal_id},
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO auth_identities
                            (id,principal_id,provider,provider_subject,email,created_at)
                        VALUES (:id,:principal,'email',:email,:email,now())
                        """
                    ),
                    {"id": _opaque_id("aid"), "principal": principal_id, "email": normalized_email},
                )
            else:
                principal_id = identity["principal_id"]
                principal = await self._principal(session, principal_id)
                identity_count = await session.scalar(
                    text("SELECT count(*) FROM auth_identities WHERE principal_id=:principal"),
                    {"principal": principal_id},
                )
                if (
                    principal is None
                    or principal["tenant_id"] is not None
                    or principal["account_id"] is not None
                    or principal["roles"]
                    or principal["status"] != "active"
                    or identity_count != 1
                ):
                    raise OnboardingConflict("configured email identity is already assigned")
            await session.execute(
                text("INSERT INTO principal_roles(principal_id,role) VALUES (:id,'operator')"),
                {"id": principal_id},
            )
            await session.execute(
                text(
                    "UPDATE auth_browser_sessions SET revoked_at=COALESCE(revoked_at,now()) "
                    "WHERE principal_id=:principal"
                ),
                {"principal": principal_id},
            )
            await session.execute(
                text(
                    "INSERT INTO operator_bootstrap(singleton,principal_id,configuration_hash) "
                    "VALUES (true,:principal,:hash)"
                ),
                {"principal": principal_id, "hash": configuration_hash},
            )
            await self._audit(
                session,
                tenant_id=None,
                actor_id=principal_id,
                action="operator.bootstrapped",
                target_id=principal_id,
                reason="configured zero-operator bootstrap",
            )
            return OperatorBootstrap(principal_id, True)
