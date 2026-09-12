import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import aiosqlite
import httpx
import pytest

from clearinghouse.application.core_store import StoredCredential
from clearinghouse.application.pagination import KeysetPage
from clearinghouse.domain.core import (
    CapabilityOffer,
    CredentialId,
    ExactPrice,
    PriceObservationId,
    UsageEvent,
    UsageEventId,
    UsageStatus,
    Workload,
    WorkloadId,
    WorkloadStatus,
)
from clearinghouse.infrastructure.simple_config import CoreSettings
from clearinghouse.infrastructure.sqlite import SqliteStore
from clearinghouse.simple_main import create_app


async def test_large_collections_are_complete_deterministic_and_bounded(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = SqliteStore(tmp_path / "large-pages.db")
    await store.initialize()
    now = datetime.now(UTC)
    price = ExactPrice(2, 1, "wei", "pixel")
    offers = tuple(
        CapabilityOffer(
            PriceObservationId(f"price_{index:04d}"),
            f"https://runner-{index}.example.test",
            None,
            f"capability-{index:04d}",
            None,
            (),
            price,
            now,
            now + timedelta(hours=1),
        )
        for index in range(1005)
    )
    offer = offers[0]
    async with store.transaction() as transaction:
        identity = await transaction.resolve_identity("paged@example.test", admin_email=None)
        for index in range(1004):
            await transaction.resolve_identity(f"user-{index:04d}@example.test", admin_email=None)
        await transaction.replace_offers(offers)
        for index in range(1005):
            created = now + timedelta(microseconds=index)
            workload = Workload(
                WorkloadId(f"work_{index:04d}"),
                identity.account_id,
                identity.user_id,
                "live",
                None,
                offer.id,
                price,
                WorkloadStatus.ACTIVE,
                now + timedelta(hours=1),
                created,
            )
            await transaction.put_credential(
                StoredCredential(
                    CredentialId(f"cred_{index:04d}"),
                    identity.account_id,
                    identity.user_id,
                    f"Credential {index}",
                    f"digest-{index}".encode(),
                    created,
                    None,
                )
            )
            await transaction.put_workload(workload, f"token-{index}".encode())
            await transaction.put_usage(
                UsageEvent(
                    UsageEventId(f"usage_{index:04d}"),
                    f"transport-{index}",
                    None,
                    identity.account_id,
                    identity.user_id,
                    workload.id,
                    "signer",
                    f"state-{index}",
                    index,
                    f"manifest-{index}",
                    f"payment-{index}",
                    "live",
                    1,
                    "pixel",
                    2,
                    "wei",
                    1,
                    created,
                    UsageStatus.MATCHED,
                )
            )

    async def collect(kind: str) -> tuple[list[str], list[int]]:
        after: tuple[str, ...] = ()
        identifiers: list[str] = []
        sizes: list[int] = []
        while True:
            page: KeysetPage[StoredCredential] | KeysetPage[Workload] | KeysetPage[UsageEvent]
            async with store.transaction() as transaction:
                if kind == "credentials":
                    page = await transaction.list_credentials(
                        identity.account_id, limit=50, after=after
                    )
                elif kind == "workloads":
                    page = await transaction.list_workloads(
                        identity.account_id, limit=50, after=after
                    )
                else:
                    page = await transaction.list_usage(identity.account_id, limit=50, after=after)
            identifiers.extend(str(item.id) for item in page.items)
            sizes.append(len(page.items))
            if page.next_key is None:
                return identifiers, sizes
            after = page.next_key

    for kind in ("credentials", "workloads", "usage"):
        identifiers, sizes = await collect(kind)
        assert len(identifiers) == 1005
        assert len(set(identifiers)) == 1005
        assert sizes == [50] * 20 + [5]

    identity_ids: list[str] = []
    offer_ids: list[str] = []
    for kind in ("identities", "offers"):
        after: tuple[str, ...] = ()
        collection_sizes: list[int] = []
        while True:
            async with store.transaction() as transaction:
                if kind == "identities":
                    identity_page = await transaction.list_identities(limit=50, after=after)
                    identity_ids.extend(str(value.user_id) for value in identity_page.items)
                    size = len(identity_page.items)
                    continuation = identity_page.next_key
                else:
                    offer_page = await transaction.list_offers(now=now, limit=50, after=after)
                    offer_ids.extend(str(value.id) for value in offer_page.items)
                    size = len(offer_page.items)
                    continuation = offer_page.next_key
            collection_sizes.append(size)
            if continuation is None:
                break
            after = continuation
        assert collection_sizes == [50] * 20 + [5]
    assert len(set(identity_ids)) == len(set(offer_ids)) == 1005

    async with store.transaction() as transaction:
        summary = await transaction.account_summary(identity.account_id, now=now)
        assert await transaction.usage_aggregates(()) == ()
    assert (summary.offers, summary.credentials, summary.workloads, summary.usage_events) == (
        1005,
        1005,
        1005,
        1005,
    )
    assert summary.computed_fee == 2010
    async with aiosqlite.connect(store.path) as connection:
        plans = (
            (
                "SELECT * FROM api_credentials WHERE account_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 51",
                (identity.account_id,),
                "ix_credentials_account_page",
            ),
            (
                "SELECT * FROM workloads WHERE account_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 51",
                (identity.account_id,),
                "ix_workloads_account_created",
            ),
            (
                "SELECT * FROM usage_events WHERE account_id = ? "
                "ORDER BY occurred_at DESC, id DESC LIMIT 51",
                (identity.account_id,),
                "ix_usage_account_occurred",
            ),
        )
        for sql, values, index_name in plans:
            rows = await (await connection.execute(f"EXPLAIN QUERY PLAN {sql}", values)).fetchall()
            assert index_name in " ".join(str(value) for row in rows for value in row)

    settings = CoreSettings(_env_file=None, database_path=store.path)
    token = "och_live_large-fixture"  # noqa: S105 -- deterministic non-secret test value
    digest = hmac.new(
        settings.auth_pepper.get_secret_value().encode(),
        f"credential\0{token}".encode(),
        hashlib.sha256,
    ).digest()
    async with store.transaction() as transaction:
        await transaction.put_credential(
            StoredCredential(
                CredentialId("cred_http"),
                identity.account_id,
                identity.user_id,
                "HTTP fixture",
                digest,
                now + timedelta(seconds=1),
                None,
            )
        )

    class Sender:
        async def send_code(self, email: str, code: str, expires_in_minutes: int) -> None:
            del email, code, expires_in_minutes

    class Provider:
        async def discover(self, capability, model):  # type: ignore[no-untyped-def]
            raise AssertionError("collection qualification must not refresh discovery")

    app = create_app(settings, store, Sender(), Provider(), consume_kafka=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8080"
    ) as client:
        headers = {"Authorization": f"Bearer {token}"}
        for path in ("/v1/credentials", "/v1/workloads", "/v1/usage", "/v1/costs"):
            response = await client.get(path, headers=headers)
            assert response.status_code == 200
            assert len(response.json()["items"]) == 50
            assert response.json()["next_cursor"]
            assert len(response.content) < 100_000
    await store.close()


async def test_keyset_pages_remain_stable_during_insert_and_delete(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = SqliteStore(tmp_path / "changing-pages.db")
    await store.initialize()
    now = datetime.now(UTC)
    async with store.transaction() as transaction:
        identity = await transaction.resolve_identity("changes@example.test", admin_email=None)
        for index in range(1, 5):
            await transaction.put_credential(
                StoredCredential(
                    CredentialId(f"cred_{index}"),
                    identity.account_id,
                    identity.user_id,
                    f"Credential {index}",
                    f"digest-{index}".encode(),
                    now,
                    None,
                )
            )
        first = await transaction.list_credentials(identity.account_id, limit=2)
    assert [str(value.id) for value in first.items] == ["cred_4", "cred_3"]
    assert first.next_key is not None
    async with store.transaction() as transaction:
        await transaction.put_credential(
            StoredCredential(
                CredentialId("cred_5"),
                identity.account_id,
                identity.user_id,
                "Inserted ahead",
                b"digest-5",
                now,
                None,
            )
        )
    async with aiosqlite.connect(store.path) as connection:
        await connection.execute("DELETE FROM api_credentials WHERE id = ?", ("cred_2",))
        await connection.commit()
    async with store.transaction() as transaction:
        second = await transaction.list_credentials(
            identity.account_id, limit=2, after=first.next_key
        )
    assert [str(value.id) for value in second.items] == ["cred_1"]
    assert not (
        {str(value.id) for value in first.items} & {str(value.id) for value in second.items}
    )
    await store.close()


async def test_storage_rejects_cursor_keys_from_another_ordering(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = SqliteStore(tmp_path / "invalid-keys.db")
    await store.initialize()
    now = datetime.now(UTC)
    async with store.transaction() as transaction:
        identity = await transaction.resolve_identity("keys@example.test", admin_email=None)
        calls = (
            lambda: transaction.list_identities(after=("wrong",)),
            lambda: transaction.list_credentials(identity.account_id, after=("wrong",)),
            lambda: transaction.list_offers(now=now, after=("wrong",)),
            lambda: transaction.list_workloads(identity.account_id, after=("wrong",)),
            lambda: transaction.list_usage(identity.account_id, after=("wrong",)),
        )
        for call in calls:
            with pytest.raises(ValueError, match="cursor key"):
                await call()
    await store.close()
