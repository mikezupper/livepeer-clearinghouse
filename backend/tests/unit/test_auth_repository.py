"""PostgreSQL authentication repository transaction tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self, cast

import pytest
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
from clearinghouse.infrastructure.auth_repository import PostgresAuthRepository

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


class FakeResult:
    def __init__(
        self,
        *,
        scalar: object | None = None,
        rows: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.scalar = scalar
        self.rows = rows

    def scalar_one_or_none(self) -> object | None:
        return self.scalar

    def mappings(self) -> Self:
        return self

    def one_or_none(self) -> Mapping[str, Any] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> Sequence[RowMapping]:
        return cast(Sequence[RowMapping], self.rows)


class FakeSession:
    def __init__(self, results: Sequence[FakeResult] = ()) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, Mapping[str, object] | None]] = []

    async def execute(
        self, statement: object, parameters: Mapping[str, object] | None = None
    ) -> FakeResult:
        self.calls.append((str(statement), parameters))
        return self.results.pop(0) if self.results else FakeResult()

    async def scalar(
        self, statement: object, parameters: Mapping[str, object] | None = None
    ) -> object | None:
        result = await self.execute(statement, parameters)
        return result.scalar


class SessionContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback


class FakeFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def begin(self) -> SessionContext:
        return SessionContext(self.session)


def repository(*results: FakeResult) -> tuple[PostgresAuthRepository, FakeSession]:
    session = FakeSession(results)
    factory = cast(async_sessionmaker[AsyncSession], FakeFactory(session))
    return PostgresAuthRepository.from_session_factory(factory), session


def principal_rows() -> list[Mapping[str, Any]]:
    common: dict[str, Any] = {
        "id": "bws_abcdefgh",
        "principal_id": "prn_abcdefgh",
        "tenant_id": "ten_abcdefgh",
        "account_id": "acc_abcdefgh",
        "status": "active",
        "token_hash": "a" * 64,
        "csrf_hash": "b" * 64,
        "client_ip_hash": "c" * 64,
        "user_agent_hash": "d" * 64,
        "last_seen_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
        "absolute_expires_at": NOW + timedelta(days=7),
        "revoked_at": None,
    }
    return [{**common, "role": "credential_holder"}, {**common, "role": "tenant_admin"}]


def browser_session() -> BrowserSession:
    return BrowserSession(
        "bws_abcdefgh",
        Principal(
            "prn_abcdefgh",
            "ten_abcdefgh",
            "acc_abcdefgh",
            frozenset({Role.CREDENTIAL_HOLDER}),
        ),
        "a" * 64,
        "b" * 64,
        NOW + timedelta(hours=1),
        NOW + timedelta(days=7),
        NOW,
        "c" * 64,
        "d" * 64,
    )


@pytest.mark.asyncio
async def test_rate_limit_insert_increment_and_limit() -> None:
    adapter, session = repository(FakeResult(), FakeResult(), FakeResult())
    assert (
        await adapter.consume_rate_limit("scope", "a" * 64, limit=2, window_seconds=60, now=NOW)
        is None
    )
    assert "INSERT INTO auth_rate_limits" in session.calls[-1][0]
    adapter, session = repository(FakeResult(), FakeResult(scalar=1), FakeResult())
    assert (
        await adapter.consume_rate_limit("scope", "a" * 64, limit=2, window_seconds=60, now=NOW)
        is None
    )
    assert "request_count = request_count + 1" in session.calls[-1][0]
    adapter, _ = repository(FakeResult(), FakeResult(scalar=2))
    assert (
        await adapter.consume_rate_limit("scope", "a" * 64, limit=2, window_seconds=60, now=NOW)
        == 60
    )


@pytest.mark.asyncio
async def test_email_challenge_replacement_and_verification_paths() -> None:
    challenge = EmailChallenge(
        "otp_abcdefgh", "a" * 64, "b" * 64, "c" * 64, NOW + timedelta(minutes=5), 3
    )
    adapter, session = repository()
    await adapter.replace_email_challenge(challenge)
    assert len(session.calls) == 2

    adapter, session = repository(
        FakeResult(
            rows=[
                {
                    "id": challenge.id,
                    "code_hash": challenge.code_hash,
                    "expires_at": challenge.expires_at,
                    "attempt_count": 0,
                    "max_attempts": 3,
                }
            ]
        )
    )
    assert await adapter.verify_email_challenge(challenge.email_hash, challenge.code_hash, NOW)
    assert session.calls[-1][1] is not None and session.calls[-1][1]["valid"] is True

    for rows in (
        [],
        [
            {
                "id": challenge.id,
                "code_hash": challenge.code_hash,
                "expires_at": NOW,
                "attempt_count": 0,
                "max_attempts": 3,
            }
        ],
        [
            {
                "id": challenge.id,
                "code_hash": challenge.code_hash,
                "expires_at": challenge.expires_at,
                "attempt_count": 3,
                "max_attempts": 3,
            }
        ],
    ):
        adapter, _ = repository(FakeResult(rows=rows))
        assert not await adapter.verify_email_challenge("a" * 64, "wrong", NOW)
    adapter, _ = repository(
        FakeResult(
            rows=[
                {
                    "id": challenge.id,
                    "code_hash": challenge.code_hash,
                    "expires_at": challenge.expires_at,
                    "attempt_count": 0,
                    "max_attempts": 3,
                }
            ]
        )
    )
    assert not await adapter.verify_email_challenge("a" * 64, "wrong", NOW)


@pytest.mark.asyncio
async def test_identity_resolution_existing_new_and_missing() -> None:
    rows = [{**row, "id": "prn_abcdefgh"} for row in principal_rows()]
    adapter, _ = repository(FakeResult(), FakeResult(scalar="prn_abcdefgh"), FakeResult(rows=rows))
    existing = await adapter.resolve_identity(AuthProvider.EMAIL, "user@example.com", None)
    assert existing.roles == frozenset({Role.CREDENTIAL_HOLDER, Role.TENANT_ADMIN})

    adapter, session = repository(
        FakeResult(), FakeResult(), FakeResult(), FakeResult(), FakeResult(rows=rows)
    )
    created = await adapter.resolve_identity(AuthProvider.GITHUB, "123", "verified@example.com")
    assert created.id == "prn_abcdefgh"
    statements = "\n".join(call[0] for call in session.calls)
    assert "INSERT INTO principals" in statements
    assert "INSERT INTO auth_identities" in statements
    assert "INSERT INTO principal_roles" not in statements

    adapter, _ = repository(FakeResult(), FakeResult(scalar="missing"), FakeResult())
    with pytest.raises(RuntimeError):
        await adapter.resolve_identity(AuthProvider.EMAIL, "missing@example.com", None)


@pytest.mark.asyncio
async def test_oauth_transactions_are_one_time_and_expiring() -> None:
    value = OAuthTransaction(
        "oauth_abcdefgh",
        AuthProvider.GOOGLE,
        "a" * 64,
        "verifier",
        "b" * 64,
        "https://app.example/callback",
        NOW + timedelta(minutes=5),
    )
    adapter, session = repository()
    await adapter.save_oauth_transaction(value)
    assert "INSERT INTO auth_oauth_transactions" in session.calls[0][0]
    row = {
        "id": value.id,
        "provider": value.provider.value,
        "state_hash": value.state_hash,
        "pkce_verifier": value.pkce_verifier,
        "nonce_hash": value.nonce_hash,
        "redirect_uri": value.redirect_uri,
        "expires_at": value.expires_at,
    }
    adapter, _ = repository(FakeResult(rows=[row]), FakeResult())
    assert await adapter.consume_oauth_transaction(AuthProvider.GOOGLE, "a" * 64, NOW) == value
    adapter, _ = repository(FakeResult())
    assert await adapter.consume_oauth_transaction(AuthProvider.GOOGLE, "a" * 64, NOW) is None
    adapter, _ = repository(FakeResult(rows=[{**row, "expires_at": NOW}]))
    assert await adapter.consume_oauth_transaction(AuthProvider.GOOGLE, "a" * 64, NOW) is None


@pytest.mark.asyncio
async def test_session_create_use_renew_and_revoke() -> None:
    value = browser_session()
    adapter, session = repository()
    await adapter.create_session(value)
    assert "INSERT INTO auth_browser_sessions" in session.calls[0][0]

    adapter, _ = repository(FakeResult(rows=principal_rows()), FakeResult())
    used = await adapter.use_session(value.token_hash, NOW, 3600)
    assert used is not None and used.last_seen_at == NOW
    adapter, _ = repository(FakeResult())
    assert await adapter.use_session(value.token_hash, NOW, 3600) is None

    adapter, session = repository(FakeResult(rows=principal_rows()), FakeResult(), FakeResult())
    renewed = await adapter.renew_session(
        value.token_hash,
        now=NOW,
        inactivity_seconds=3600,
        replacement_id="bws_replaced1",
        replacement_token_hash="e" * 64,
        replacement_csrf_hash="f" * 64,
        ttl_seconds=86400,
    )
    assert renewed is not None
    assert renewed.id == "bws_replaced1"
    assert renewed.roles if hasattr(renewed, "roles") else renewed.principal.roles
    assert "UPDATE auth_browser_sessions SET revoked_at" in session.calls[1][0]
    adapter, _ = repository(FakeResult())
    assert (
        await adapter.renew_session(
            value.token_hash,
            now=NOW,
            inactivity_seconds=3600,
            replacement_id="bws_replaced1",
            replacement_token_hash="e" * 64,
            replacement_csrf_hash="f" * 64,
            ttl_seconds=86400,
        )
        is None
    )
    adapter, session = repository()
    await adapter.revoke_session(value.id)
    assert "COALESCE" in session.calls[0][0]
