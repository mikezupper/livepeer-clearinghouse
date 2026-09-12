from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from contracts.ports.v2.protocols import DiscoverySnapshotUnavailable

from clearinghouse.infrastructure.discovery import (
    CompositeNetworkDiscovery,
    ConfiguredLv2vDiscovery,
    HttpNetworkDiscovery,
)


def test_discovery_decoder_ignores_malformed_and_filters_values() -> None:
    provider = HttpNetworkDiscovery(
        ["https://discovery.example"], clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)
    )
    payload: list[object] = [
        None,
        {"address": "", "runners": []},
        {"address": "https://orch.example", "runners": "invalid"},
        {
            "address": "https://orch.example",
            "runners": [
                None,
                {"url": "", "app": "live", "price_info": {}},
                {"url": "https://runner.example", "app": "live", "price_info": "invalid"},
                {
                    "url": "https://runner.example",
                    "app": "live",
                    "price_info": {"price": True, "currency": "wei", "unit": "pixel"},
                },
                {
                    "url": "https://runner.example",
                    "app": "live",
                    "price_info": {"price": "nan", "currency": "wei", "unit": "pixel"},
                },
                {
                    "url": "https://runner.example",
                    "app": "live",
                    "price_info": {"price": "-1", "currency": "wei", "unit": "pixel"},
                },
                {
                    "url": "https://runner.example",
                    "app": "live",
                    "model": "other",
                    "price_info": {"price": 1, "currency": "", "unit": "pixel"},
                },
                {
                    "url": "https://runner.example",
                    "app": "live",
                    "model": "wanted",
                    "orchestrator_address": "0x1",
                    "price_info": {"price": 1.5, "currency": "WEI", "unit": "PIXEL"},
                },
            ],
        },
    ]
    assert provider._decode(payload, capability="other", model=None) == []
    assert provider._decode(payload, capability="live", model="other") == []
    offers = provider._decode(payload, capability="live", model="wanted")
    assert len(offers) == 1
    assert (offers[0].numerator, offers[0].denominator) == (3, 2)
    assert offers[0].orchestrator_address == "0x1"


def test_discovery_requires_urls_and_ratio_rejects_invalid_types() -> None:
    with pytest.raises(ValueError, match="at least one"):
        HttpNetworkDiscovery([])
    assert HttpNetworkDiscovery._text(1) == ""
    assert HttpNetworkDiscovery._ratio(object()) is None


@pytest.mark.parametrize(
    ("payload", "incomplete"),
    (
        ([], True),
        ([{"address": "https://orch.example", "capabilities": ["live"]}], True),
        (
            [
                {
                    "address": "https://orch.example",
                    "capabilities": ["live"],
                    "runners": [],
                }
            ],
            True,
        ),
        (
            [
                {
                    "address": "https://orch.example",
                    "runners": [
                        {
                            "url": "https://runner.example/live",
                            "app": "live",
                            "price_info": {"price": 1, "currency": "wei", "unit": "seconds"},
                        }
                    ],
                }
            ],
            False,
        ),
    ),
)
def test_incomplete_snapshot_detection(payload: list[object], incomplete: bool) -> None:
    assert HttpNetworkDiscovery._incomplete(payload) is incomplete


async def test_configured_lv2v_discovery_requires_complete_exact_prices(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "lv2v.json"
    path.write_text(
        json.dumps(
            {
                "schema": "livepeer.clearinghouse.lv2v-offers.v1",
                "offers": [
                    {
                        "orchestrator_url": "https://orch.example:8936",
                        "orchestrator_address": "0xABC",
                        "capability": "live-video-to-video/model",
                        "model": "model",
                        "constraints": {"source": "GetOrchestrator"},
                        "price": {
                            "numerator": "7",
                            "denominator": "3",
                            "currency": "wei",
                            "quantity_unit": "720p-pixel-seconds",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    provider = ConfiguredLv2vDiscovery(path, clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    assert await provider.discover("other", None) == []
    offers = await provider.discover("live-video-to-video/model", "model")
    assert len(offers) == 1
    assert (offers[0].numerator, offers[0].denominator) == (7, 3)
    assert offers[0].orchestrator_address == "0xabc"
    assert offers[0].quantity_unit == "720p-pixel-seconds"

    path.write_text('{"schema":"livepeer.clearinghouse.lv2v-offers.v1","offers":[{}]}')
    with pytest.raises(DiscoverySnapshotUnavailable, match="incomplete"):
        await provider.discover(None, None)


async def test_composite_discovery_is_atomic() -> None:
    expected = HttpNetworkDiscovery._runner(
        {
            "url": "https://runner.example",
            "app": "app",
            "price_info": {"price": 1, "currency": "wei", "unit": "fixed"},
        },
        orchestrator_url="https://orch.example",
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert expected is not None

    class Provider:
        async def discover(self, capability, model):  # type: ignore[no-untyped-def]
            del capability, model
            return [expected]

    values = await CompositeNetworkDiscovery([Provider(), Provider()]).discover(None, None)
    assert values == [expected, expected]
    with pytest.raises(ValueError, match="at least one"):
        CompositeNetworkDiscovery([])
