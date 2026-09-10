"""PostgreSQL authentication repository."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clearinghouse.domain.auth import (
    AuthProvider,
    BrowserSession,
    EmailChallenge,
    OAuthTransaction,
    Principal,
    Role,
)


class PostgresAuthRepository:
    """Keep authentication decisions atomic in PostgreSQL transactions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @classmethod
    def from_session_factory(
        cls, sessions: async_sessionmaker[AsyncSession]
    ) -> PostgresAuthRepository:
        """Build the adapter from the composition root's shared factory."""
        return cls(sessions)

    async def consume_rate_limit(
        self,
        scope: str,
        key_hash: str,
        *,
        limit: int,
        window_seconds: int,
        now: datetime,
    ) -> int | None:
        window_epoch = int(now.timestamp()) // window_seconds * window_seconds
        lock_key = f"{scope}:{key_hash}:{window_epoch}"
        async with self._sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock_key}
            )
            result = await session.execute(
                text(
                    """
                    SELECT request_count FROM auth_rate_limits
                    WHERE scope = :scope AND key_hash = :key_hash
                      AND window_epoch = :window_epoch
                    FOR UPDATE
                    """
                ),
                {"scope": scope, "key_hash": key_hash, "window_epoch": window_epoch},
            )
            count = result.scalar_one_or_none()
            if count is None:
                await session.execute(
                    text(
                        """
                        INSERT INTO auth_rate_limits
                            (scope, key_hash, window_epoch, request_count, created_at)
                        VALUES (:scope, :key_hash, :window_epoch, 1, :now)
                        """
                    ),
                    {
                        "scope": scope,
                        "key_hash": key_hash,
                        "window_epoch": window_epoch,
                        "now": now,
                    },
                )
                return None
            if count >= limit:
                return max(1, window_epoch + window_seconds - int(now.timestamp()))
            await session.execute(
                text(
                    """
                    UPDATE auth_rate_limits SET request_count = request_count + 1
                    WHERE scope = :scope AND key_hash = :key_hash
                      AND window_epoch = :window_epoch
                    """
                ),
                {"scope": scope, "key_hash": key_hash, "window_epoch": window_epoch},
            )
        return None

    async def replace_email_challenge(self, challenge: EmailChallenge) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                text(
                    """
                    UPDATE auth_email_challenges SET consumed_at = now()
                    WHERE email_hash = :email_hash AND consumed_at IS NULL
                    """
                ),
                {"email_hash": challenge.email_hash},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO auth_email_challenges
                        (id, email_hash, code_hash, request_ip_hash, expires_at,
                         attempt_count, max_attempts, created_at)
                    VALUES (:id, :email_hash, :code_hash, :request_ip_hash, :expires_at,
                            0, :max_attempts, now())
                    """
                ),
                {
                    "id": challenge.id,
                    "email_hash": challenge.email_hash,
                    "code_hash": challenge.code_hash,
                    "request_ip_hash": challenge.request_ip_hash,
                    "expires_at": challenge.expires_at,
                    "max_attempts": challenge.max_attempts,
                },
            )

    async def verify_email_challenge(self, email_hash: str, code_hash: str, now: datetime) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, code_hash, expires_at, attempt_count, max_attempts
                    FROM auth_email_challenges
                    WHERE email_hash = :email_hash AND consumed_at IS NULL
                    ORDER BY created_at DESC LIMIT 1 FOR UPDATE
                    """
                ),
                {"email_hash": email_hash},
            )
            row = result.mappings().one_or_none()
            if (
                row is None
                or row["expires_at"] <= now
                or row["attempt_count"] >= row["max_attempts"]
            ):
                return False
            valid = hmac.compare_digest(row["code_hash"], code_hash)
            await session.execute(
                text(
                    """
                    UPDATE auth_email_challenges
                    SET attempt_count = attempt_count + 1,
                        consumed_at = CASE WHEN :valid THEN :now ELSE consumed_at END
                    WHERE id = :id
                    """
                ),
                {"valid": valid, "now": now, "id": row["id"]},
            )
            return valid

    async def resolve_identity(
        self,
        provider: AuthProvider,
        provider_subject: str,
        email: str | None,
    ) -> Principal:
        lock_key = f"identity:{provider.value}:{provider_subject}"
        async with self._sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock_key}
            )
            existing_id = await session.scalar(
                text(
                    """
                    SELECT principal_id FROM auth_identities
                    WHERE provider = :provider AND provider_subject = :subject
                    """
                ),
                {"provider": provider.value, "subject": provider_subject},
            )
            principal_id = str(existing_id) if existing_id else f"prn_{secrets.token_urlsafe(18)}"
            if existing_id is None:
                await session.execute(
                    text(
                        """
                        INSERT INTO principals
                            (id, tenant_id, account_id, display_name, status, created_at)
                        VALUES (:id, NULL, NULL, NULL, 'active', now())
                        """
                    ),
                    {"id": principal_id},
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO auth_identities
                            (id, principal_id, provider, provider_subject, email, created_at)
                        VALUES (:id, :principal_id, :provider, :subject, :email, now())
                        """
                    ),
                    {
                        "id": f"aid_{secrets.token_urlsafe(18)}",
                        "principal_id": principal_id,
                        "provider": provider.value,
                        "subject": provider_subject,
                        "email": email,
                    },
                )
            return await self._load_principal(session, principal_id)

    @staticmethod
    async def _load_principal(session: AsyncSession, principal_id: str) -> Principal:
        result = await session.execute(
            text(
                """
                SELECT p.id, p.tenant_id, p.account_id, p.status, pr.role
                FROM principals p
                LEFT JOIN principal_roles pr ON pr.principal_id = p.id
                WHERE p.id = :id
                """
            ),
            {"id": principal_id},
        )
        rows = result.mappings().all()
        if not rows:
            raise RuntimeError("authentication identity references a missing principal")
        first = rows[0]
        roles = frozenset(Role(row["role"]) for row in rows if row["role"] is not None)
        return Principal(
            id=first["id"],
            tenant_id=first["tenant_id"],
            account_id=first["account_id"],
            roles=roles,
            active=first["status"] == "active",
        )

    async def save_oauth_transaction(self, transaction: OAuthTransaction) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO auth_oauth_transactions
                        (id, provider, state_hash, pkce_verifier, nonce_hash,
                         redirect_uri, expires_at, created_at)
                    VALUES (:id, :provider, :state_hash, :pkce_verifier, :nonce_hash,
                            :redirect_uri, :expires_at, now())
                    """
                ),
                {
                    "id": transaction.id,
                    "provider": transaction.provider.value,
                    "state_hash": transaction.state_hash,
                    "pkce_verifier": transaction.pkce_verifier,
                    "nonce_hash": transaction.nonce_hash,
                    "redirect_uri": transaction.redirect_uri,
                    "expires_at": transaction.expires_at,
                },
            )

    async def consume_oauth_transaction(
        self, provider: AuthProvider, state_hash: str, now: datetime
    ) -> OAuthTransaction | None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, provider, state_hash, pkce_verifier, nonce_hash,
                           redirect_uri, expires_at
                    FROM auth_oauth_transactions
                    WHERE provider = :provider AND state_hash = :state_hash
                      AND consumed_at IS NULL
                    FOR UPDATE
                    """
                ),
                {"provider": provider.value, "state_hash": state_hash},
            )
            row = result.mappings().one_or_none()
            if row is None or row["expires_at"] <= now:
                return None
            await session.execute(
                text("UPDATE auth_oauth_transactions SET consumed_at = :now WHERE id = :id"),
                {"now": now, "id": row["id"]},
            )
            return OAuthTransaction(
                id=row["id"],
                provider=AuthProvider(row["provider"]),
                state_hash=row["state_hash"],
                pkce_verifier=row["pkce_verifier"],
                nonce_hash=row["nonce_hash"],
                redirect_uri=row["redirect_uri"],
                expires_at=row["expires_at"],
            )

    async def create_session(self, session_value: BrowserSession) -> None:
        async with self._sessions.begin() as session:
            await self._insert_session(session, session_value)

    @staticmethod
    async def _insert_session(session: AsyncSession, session_value: BrowserSession) -> None:
        await session.execute(
            text(
                """
                INSERT INTO auth_browser_sessions
                    (id, principal_id, token_hash, csrf_hash, client_ip_hash,
                     user_agent_hash, created_at, last_seen_at, expires_at,
                     absolute_expires_at, revoked_at)
                VALUES (:id, :principal_id, :token_hash, :csrf_hash, :client_ip_hash,
                        :user_agent_hash, :created_at, :last_seen_at, :expires_at,
                        :absolute_expires_at, NULL)
                """
            ),
            {
                "id": session_value.id,
                "principal_id": session_value.principal.id,
                "token_hash": session_value.token_hash,
                "csrf_hash": session_value.csrf_hash,
                "client_ip_hash": session_value.client_ip_hash,
                "user_agent_hash": session_value.user_agent_hash,
                "created_at": session_value.last_seen_at,
                "last_seen_at": session_value.last_seen_at,
                "expires_at": session_value.expires_at,
                "absolute_expires_at": session_value.absolute_expires_at,
            },
        )

    async def use_session(
        self, token_hash: str, now: datetime, inactivity_seconds: int
    ) -> BrowserSession | None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                text(
                    """
                    SELECT s.id, s.principal_id, s.token_hash, s.csrf_hash,
                           s.client_ip_hash, s.user_agent_hash, s.last_seen_at,
                           s.expires_at, s.absolute_expires_at, s.revoked_at,
                           p.tenant_id, p.account_id, p.status, pr.role
                    FROM auth_browser_sessions s
                    JOIN principals p ON p.id = s.principal_id
                    LEFT JOIN principal_roles pr ON pr.principal_id = p.id
                    WHERE s.token_hash = :token_hash
                      AND s.revoked_at IS NULL
                      AND s.expires_at > :now
                      AND s.absolute_expires_at > :now
                      AND s.last_seen_at >= :inactive_after
                      AND p.status = 'active'
                    FOR UPDATE OF s, p
                    """
                ),
                {
                    "token_hash": token_hash,
                    "now": now,
                    "inactive_after": now - timedelta(seconds=inactivity_seconds),
                },
            )
            rows = result.mappings().all()
            if not rows:
                return None
            await session.execute(
                text("UPDATE auth_browser_sessions SET last_seen_at = :now WHERE id = :id"),
                {"now": now, "id": rows[0]["id"]},
            )
        return self._session_from_rows(rows, last_seen_at=now)

    @staticmethod
    def _session_from_rows(
        rows: Sequence[RowMapping], *, last_seen_at: datetime | None = None
    ) -> BrowserSession:
        first = rows[0]
        principal = Principal(
            id=first["principal_id"],
            tenant_id=first["tenant_id"],
            account_id=first["account_id"],
            roles=frozenset(Role(row["role"]) for row in rows if row["role"] is not None),
            active=first["status"] == "active",
        )
        return BrowserSession(
            id=first["id"],
            principal=principal,
            token_hash=first["token_hash"],
            csrf_hash=first["csrf_hash"],
            expires_at=first["expires_at"],
            absolute_expires_at=first["absolute_expires_at"],
            last_seen_at=last_seen_at or first["last_seen_at"],
            client_ip_hash=first["client_ip_hash"],
            user_agent_hash=first["user_agent_hash"],
            revoked=first["revoked_at"] is not None,
        )

    async def renew_session(
        self,
        token_hash: str,
        *,
        now: datetime,
        inactivity_seconds: int,
        replacement_id: str,
        replacement_token_hash: str,
        replacement_csrf_hash: str,
        ttl_seconds: int,
    ) -> BrowserSession | None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                text(
                    """
                    SELECT s.id, s.principal_id, s.token_hash, s.csrf_hash,
                           s.client_ip_hash, s.user_agent_hash, s.last_seen_at,
                           s.expires_at, s.absolute_expires_at, s.revoked_at,
                           p.tenant_id, p.account_id, p.status, pr.role
                    FROM auth_browser_sessions s
                    JOIN principals p ON p.id = s.principal_id
                    LEFT JOIN principal_roles pr ON pr.principal_id = p.id
                    WHERE s.token_hash = :token_hash
                      AND s.revoked_at IS NULL
                      AND s.expires_at > :now
                      AND s.absolute_expires_at > :now
                      AND s.last_seen_at >= :inactive_after
                      AND p.status = 'active'
                    FOR UPDATE OF s, p
                    """
                ),
                {
                    "token_hash": token_hash,
                    "now": now,
                    "inactive_after": now - timedelta(seconds=inactivity_seconds),
                },
            )
            rows = result.mappings().all()
            if not rows:
                return None
            current = self._session_from_rows(rows)
            await session.execute(
                text("UPDATE auth_browser_sessions SET revoked_at = :now WHERE id = :id"),
                {"now": now, "id": current.id},
            )
            replacement = BrowserSession(
                id=replacement_id,
                principal=current.principal,
                token_hash=replacement_token_hash,
                csrf_hash=replacement_csrf_hash,
                expires_at=min(now + timedelta(seconds=ttl_seconds), current.absolute_expires_at),
                absolute_expires_at=current.absolute_expires_at,
                last_seen_at=now,
                client_ip_hash=current.client_ip_hash,
                user_agent_hash=current.user_agent_hash,
            )
            await self._insert_session(session, replacement)
            return replacement

    async def revoke_session(self, session_id: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                text(
                    """
                    UPDATE auth_browser_sessions SET revoked_at = COALESCE(revoked_at, now())
                    WHERE id = :id
                    """
                ),
                {"id": session_id},
            )
