"""Storage conformance exercised against the bundled SQLite adapter."""

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from clearinghouse.application.core_store import StoredChallenge, StoredCredential, StoredSession
from clearinghouse.domain.core import (
    AuthorizationId,
    CapabilityOffer,
    CredentialId,
    ExactPrice,
    PaymentAuthorization,
    PriceObservationId,
    UsageEvent,
    UsageEventId,
    UsageStatus,
    Workload,
    WorkloadId,
    WorkloadStatus,
)
from clearinghouse.infrastructure.sqlite import SqliteStore


@pytest.fixture
async def store(tmp_path):  # type: ignore[no-untyped-def]
    value = SqliteStore(tmp_path / "core.db", busy_timeout_ms=250)
    await value.initialize()
    yield value
    await value.close()


async def test_identity_challenge_and_session_round_trip(store: SqliteStore) -> None:
    now = datetime.now(UTC)
    challenge = StoredChallenge(
        "otp_1", "person@example.test", b"code", now + timedelta(minutes=5), 0
    )
    async with store.transaction() as transaction:
        await transaction.put_challenge(challenge)
        assert await transaction.get_challenge(challenge.id) == challenge
        await transaction.increment_challenge_attempts(challenge.id)
        changed = await transaction.get_challenge(challenge.id)
        assert changed is not None and changed.attempts == 1
        identity = await transaction.resolve_identity(challenge.email, admin_email=challenge.email)
        session = StoredSession("ses_1", b"token", b"csrf", identity, now + timedelta(hours=1))
        await transaction.put_session(session)
        assert await transaction.get_session(b"token") == session

    async with store.transaction() as transaction:
        await transaction.delete_challenge(challenge.id)
        await transaction.delete_session(b"token")
        assert await transaction.get_challenge(challenge.id) is None
        assert await transaction.get_session(b"token") is None


async def test_credential_lifecycle(store: SqliteStore) -> None:
    now = datetime.now(UTC)
    async with store.transaction() as transaction:
        identity = await transaction.resolve_identity("sdk@example.test", admin_email=None)
        credential = StoredCredential(
            CredentialId("cred_1"), identity.account_id, identity.user_id, "CI", b"api", now, None
        )
        await transaction.put_credential(credential)
        assert await transaction.get_credential(b"api") == credential
        assert list(await transaction.list_credentials(identity.account_id)) == [credential]
        assert await transaction.revoke_credential(identity.account_id, credential.id, now)
        assert await transaction.get_credential(b"api") is None
        assert not await transaction.revoke_credential(identity.account_id, credential.id, now)


async def test_offer_workload_authorization_and_usage_round_trip(store: SqliteStore) -> None:
    now = datetime.now(UTC)
    price = ExactPrice(3, 2, "wei", "pixel")
    offer = CapabilityOffer(
        PriceObservationId("price_1"),
        "https://orch.example.test",
        "0x0000000000000000000000000000000000000001",
        "live-video-to-video",
        "model-a",
        (("pipeline", "live"),),
        price,
        now,
        now + timedelta(minutes=5),
    )
    async with store.transaction() as transaction:
        identity = await transaction.resolve_identity("work@example.test", admin_email=None)
        await transaction.replace_offers([offer])
        assert list(await transaction.list_offers(now=now)) == [offer]
        assert await transaction.get_offer(offer.id, now=now) == offer
        workload = Workload(
            WorkloadId("work_1"),
            identity.account_id,
            identity.user_id,
            offer.capability,
            offer.model,
            offer.id,
            price,
            WorkloadStatus.ACTIVE,
            now + timedelta(hours=1),
            now,
            client_reference="customer-job-7",
        )
        await transaction.put_workload(workload, b"work-token")
        assert await transaction.get_workload(workload.id) == workload
        assert await transaction.get_workload_by_token(b"work-token") == workload
        await transaction.bind_workload(
            workload.id, state_id="state-1", manifest_id="manifest-1", payment_session_id="pm-1"
        )
        bound = await transaction.get_workload(workload.id)
        assert bound is not None and bound.runner_session_id == "state-1"
        authorization = PaymentAuthorization(
            AuthorizationId("auth_1"),
            workload.id,
            "signer_1",
            "state-1",
            0,
            offer.orchestrator_address or "",
            price,
            now,
        )
        await transaction.put_authorization(authorization)
        assert await transaction.get_authorization(authorization.id) == authorization
        usage = UsageEvent(
            UsageEventId("usage_1"),
            "wire-1",
            authorization.id,
            identity.account_id,
            identity.user_id,
            workload.id,
            "signer_1",
            "state-1",
            0,
            "manifest-1",
            "pm-1",
            offer.capability,
            10,
            "pixel",
            15,
            "wei",
            1,
            now,
            UsageStatus.MATCHED,
        )
        assert await transaction.put_usage(usage)
        assert not await transaction.put_usage(usage)
        assert list(await transaction.list_usage(identity.account_id)) == [usage]
        assert len(await transaction.list_workloads()) == 1
        assert await transaction.revoke_workload(workload.id, at=now)
        assert not await transaction.revoke_workload(workload.id, at=now)


async def test_transactions_rollback_and_sqlite_safety_pragmas(store: SqliteStore) -> None:
    with pytest.raises(RuntimeError, match="rollback"):
        async with store.transaction() as transaction:
            await transaction.resolve_identity("rollback@example.test", admin_email=None)
            raise RuntimeError("rollback")
    async with aiosqlite.connect(store.path) as connection:
        journal = await (await connection.execute("PRAGMA journal_mode")).fetchone()
        users = await (await connection.execute("SELECT count(*) FROM users")).fetchone()
        assert journal is not None and journal[0] == "wal"
        assert users is not None and users[0] == 0
    assert await store.readiness()
    await store.close()
    assert not await store.readiness()
