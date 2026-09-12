from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from contracts.ports.v2.protocols import DiscoveredPrice, DiscoverySnapshotUnavailable

from clearinghouse.application.discovery import DiscoveryService, sdk_discovery
from clearinghouse.application.pagination import StaleCursor
from clearinghouse.domain.core import CapabilityOffer, ExactPrice, PriceObservationId
from clearinghouse.infrastructure.discovery import HttpNetworkDiscovery
from clearinghouse.infrastructure.sqlite import SqliteStore


async def test_http_discovery_observes_exact_prices_and_sdk_shape(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    payload = [
        {
            "address": "https://orch.example.com",
            "runners": [
                {
                    "url": "https://runner.example.com/live",
                    "app": "live-video-to-video",
                    "model_id": "noop",
                    "runner_id": "runner-7",
                    "mode": "persistent",
                    "gpu": {"name": "L40S"},
                    "price_info": {"price": "0.125", "currency": "wei", "unit": "seconds"},
                },
                {"url": "https://invalid.example.com", "app": "other"},
            ],
        },
        "ignored",
    ]

    class FakeClient:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

        async def get(self, url, params):  # type: ignore[no-untyped-def]
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(200, request=request, json=payload)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = HttpNetworkDiscovery(
        ["https://signer.example.com/discover-orchestrators"], clock=lambda: now
    )
    store = SqliteStore(tmp_path / "discovery.db")
    await store.initialize()
    service = DiscoveryService(store, provider, clock=lambda: now)
    snapshot = await service.refresh("live-video-to-video", "noop")
    offers = snapshot.offers
    assert snapshot.stale is False
    assert len(offers) == 1
    assert offers[0].price.numerator == 1
    assert offers[0].price.denominator == 8
    assert list(await service.offers()) == list(offers)
    assert sdk_discovery(offers) == [
        {
            "address": "https://orch.example.com",
            "runners": [
                {
                    "url": "https://runner.example.com/live",
                    "app": "live-video-to-video",
                    "runner_id": "runner-7",
                    "mode": "persistent",
                    "gpu": {"name": "L40S"},
                    "price_info": {"price": "0.125", "currency": "wei", "unit": "seconds"},
                }
            ],
        }
    ]
    await store.close()


async def test_http_discovery_rejects_non_list_response(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class FakeClient:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

        async def get(self, url, params):  # type: ignore[no-untyped-def]
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(200, request=request, json={})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = HttpNetworkDiscovery(["https://signer.example.com/discover-orchestrators"])
    try:
        await provider.discover(None, None)
    except DiscoverySnapshotUnavailable as error:
        assert str(error) == "discovery source unavailable"
    else:
        raise AssertionError("invalid discovery response was accepted")


async def test_http_discovery_rejects_go_livepeer_capability_only_entries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    signer_payload = [
        {
            "address": "https://orch.example.com:8936",
            "score": 1,
            "capabilities": ["live-video-to-video/noop"],
        }
    ]

    class FakeClient:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

        async def get(self, url, params):  # type: ignore[no-untyped-def]
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(200, request=request, json=signer_payload)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    with pytest.raises(DiscoverySnapshotUnavailable, match="omitted"):
        await HttpNetworkDiscovery(["http://remote-signer:8935/discover-orchestrators"]).discover(
            None, None
        )


async def test_discovery_service_serves_bounded_last_complete_snapshot(tmp_path) -> None:  # type: ignore[no-untyped-def]
    times = iter(
        (
            datetime(2026, 9, 11, 12, tzinfo=UTC),
            datetime(2026, 9, 11, 12, 30, tzinfo=UTC),
            datetime(2026, 9, 11, 13, 1, tzinfo=UTC),
        )
    )
    observed = datetime(2026, 9, 11, 12, tzinfo=UTC)

    class Provider:
        calls = 0

        async def discover(self, capability, model):  # type: ignore[no-untyped-def]
            assert capability is None and model is None
            self.calls += 1
            if self.calls > 1:
                raise DiscoverySnapshotUnavailable("partial")
            return [
                DiscoveredPrice(
                    "https://runner.example.com/live",
                    None,
                    "live",
                    "noop",
                    {"orchestrator_url": "https://orch.example.com"},
                    2,
                    1,
                    "wei",
                    "seconds",
                    observed,
                )
            ]

    store = SqliteStore(tmp_path / "stale.db")
    await store.initialize()
    service = DiscoveryService(
        store,
        Provider(),
        observation_ttl_seconds=3600,
        clock=lambda: next(times),
    )

    fresh = await service.refresh()
    stale = await service.refresh(capability="live", model="noop")
    assert fresh.stale is False
    assert stale.stale is True
    assert stale.offers == fresh.offers
    with pytest.raises(DiscoverySnapshotUnavailable):
        await service.refresh()

    await store.close()


async def test_discovery_retains_unreported_orchestrator_until_its_snapshot_expires(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)

    def offer(source: str, capability: str) -> DiscoveredPrice:
        return DiscoveredPrice(
            f"{source}/apps/{capability}",
            None,
            capability,
            None,
            {"orchestrator_url": source},
            1,
            1,
            "wei",
            "fixed",
            now,
        )

    class Provider:
        calls = 0

        async def discover(self, capability, model):  # type: ignore[no-untyped-def]
            del capability, model
            self.calls += 1
            first = offer("https://orch-a.example", "app-a")
            second = offer("https://orch-b.example", "app-b")
            return [first, second] if self.calls == 1 else [first]

    store = SqliteStore(tmp_path / "source-scoped.db")
    await store.initialize()
    service = DiscoveryService(store, Provider(), observation_ttl_seconds=3600, clock=lambda: now)

    assert len((await service.refresh()).offers) == 2
    partial = await service.refresh()
    assert partial.stale is True
    assert {value.capability for value in partial.offers} == {"app-a", "app-b"}
    await store.close()


async def test_discovery_cursor_fails_explicitly_after_generation_changes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)

    class Provider:
        async def discover(self, capability, model):  # type: ignore[no-untyped-def]
            del capability, model
            return [
                DiscoveredPrice(
                    f"https://runner.example/{index}",
                    None,
                    "live",
                    f"model-{index}",
                    {},
                    index + 1,
                    1,
                    "wei",
                    "fixed",
                    now,
                )
                for index in range(2)
            ]

    store = SqliteStore(tmp_path / "generation.db")
    await store.initialize()
    service = DiscoveryService(store, Provider(), clock=lambda: now)
    first = await service.page(capability=None, model=None, limit=1)
    assert first.page.next_key is not None
    filtered = await service.page(capability="live", model="model-1", limit=1)
    assert [value.model for value in filtered.page.items] == ["model-1"]
    changed = CapabilityOffer(
        PriceObservationId("price_changed"),
        "https://runner.changed",
        None,
        "live",
        None,
        (),
        ExactPrice(1, 1, "wei", "fixed"),
        now,
        now + timedelta(hours=1),
    )
    async with store.transaction() as transaction:
        await transaction.replace_offers((changed,))
    with pytest.raises(StaleCursor):
        await service.page(
            capability=None,
            model=None,
            limit=1,
            after=first.page.next_key,
            snapshot=first.generation,
        )
    await store.close()
