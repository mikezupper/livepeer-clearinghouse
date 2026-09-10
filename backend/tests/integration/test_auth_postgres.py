"""Live PostgreSQL authentication lifecycle test."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from clearinghouse.domain.auth import (
    AuthProvider,
    BrowserSession,
    EmailChallenge,
    OAuthTransaction,
)
from clearinghouse.infrastructure.auth_repository import PostgresAuthRepository

DATABASE_URL = os.environ.get("CLEARINGHOUSE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="live PostgreSQL URL not configured")


@pytest.mark.asyncio
async def test_auth_state_is_atomic_and_replay_safe() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = PostgresAuthRepository(sessions)
    suffix = secrets.token_hex(10)
    now = datetime.now(UTC)
    email = f"auth-{suffix}@example.test"
    email_hash = secrets.token_hex(32)
    code_hash = secrets.token_hex(32)
    rate_hash = secrets.token_hex(32)
    state_hash = secrets.token_hex(32)
    challenge = EmailChallenge(
        f"otp_{suffix}",
        email_hash,
        code_hash,
        secrets.token_hex(32),
        now + timedelta(minutes=5),
        3,
    )
    transaction = OAuthTransaction(
        f"oauth_{suffix}",
        AuthProvider.GOOGLE,
        state_hash,
        "pkce-verifier",
        secrets.token_hex(32),
        "https://app.example.test/callback",
        now + timedelta(minutes=5),
    )
    principal_id = ""
    try:
        principal = await repository.resolve_identity(AuthProvider.EMAIL, email, email)
        principal_id = principal.id
        assert principal.tenant_id is None
        assert principal.account_id is None
        assert principal.roles == frozenset()
        assert await repository.resolve_identity(AuthProvider.EMAIL, email, email) == principal

        assert (
            await repository.consume_rate_limit(
                "integration", rate_hash, limit=1, window_seconds=60, now=now
            )
            is None
        )
        assert (
            await repository.consume_rate_limit(
                "integration", rate_hash, limit=1, window_seconds=60, now=now
            )
            is not None
        )

        await repository.replace_email_challenge(challenge)
        assert not await repository.verify_email_challenge(email_hash, "f" * 64, now)
        assert await repository.verify_email_challenge(email_hash, code_hash, now)
        assert not await repository.verify_email_challenge(email_hash, code_hash, now)

        await repository.save_oauth_transaction(transaction)
        assert (
            await repository.consume_oauth_transaction(AuthProvider.GOOGLE, state_hash, now)
            == transaction
        )
        assert (
            await repository.consume_oauth_transaction(AuthProvider.GOOGLE, state_hash, now) is None
        )

        session = BrowserSession(
            f"bws_{suffix}",
            principal,
            secrets.token_hex(32),
            secrets.token_hex(32),
            now + timedelta(hours=1),
            now + timedelta(days=7),
            now,
            secrets.token_hex(32),
            secrets.token_hex(32),
        )
        await repository.create_session(session)
        assert await repository.use_session(session.token_hash, now, 3600) is not None
        replacement_hash = secrets.token_hex(32)
        renewed = await repository.renew_session(
            session.token_hash,
            now=now + timedelta(seconds=1),
            inactivity_seconds=3600,
            replacement_id=f"bws_{suffix}r",
            replacement_token_hash=replacement_hash,
            replacement_csrf_hash=secrets.token_hex(32),
            ttl_seconds=86400,
        )
        assert renewed is not None and renewed.absolute_expires_at == session.absolute_expires_at
        assert await repository.use_session(session.token_hash, now, 3600) is None
        assert await repository.use_session(replacement_hash, now, 3600) is not None
        await repository.revoke_session(renewed.id)
        assert await repository.use_session(replacement_hash, now, 3600) is None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM auth_rate_limits WHERE key_hash = :value"), {"value": rate_hash}
            )
            await connection.execute(
                text("DELETE FROM auth_email_challenges WHERE email_hash = :value"),
                {"value": email_hash},
            )
            await connection.execute(
                text("DELETE FROM auth_oauth_transactions WHERE state_hash = :value"),
                {"value": state_hash},
            )
            if principal_id:
                await connection.execute(
                    text("DELETE FROM principals WHERE id = :value"), {"value": principal_id}
                )
        await engine.dispose()
