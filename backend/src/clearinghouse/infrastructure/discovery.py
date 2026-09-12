"""HTTP adapter for go-livepeer and live-runner discovery responses."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from contracts.ports.v2.protocols import DiscoveredPrice, DiscoverySnapshotUnavailable


class CompositeNetworkDiscovery:
    """Combine explicitly configured providers as one atomic priced snapshot."""

    def __init__(self, providers: Sequence[object]) -> None:
        if not providers:
            raise ValueError("at least one discovery provider is required")
        self.providers = tuple(providers)

    async def discover(
        self, capability: str | None, model: str | None
    ) -> Sequence[DiscoveredPrice]:
        values: list[DiscoveredPrice] = []
        for provider in self.providers:
            discover = getattr(provider, "discover", None)
            if discover is None:
                raise DiscoverySnapshotUnavailable("configured discovery provider is invalid")
            values.extend(await discover(capability, model))
        return values


class ConfiguredLv2vDiscovery:
    """Load operator-reviewed, exact LV2V prices from a deployment artifact."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self.clock = clock or (lambda: datetime.now(UTC))

    async def discover(
        self, capability: str | None, model: str | None
    ) -> Sequence[DiscoveredPrice]:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DiscoverySnapshotUnavailable("configured LV2V offers are unavailable") from error
        if not isinstance(document, dict) or document.get("schema") != (
            "livepeer.clearinghouse.lv2v-offers.v1"
        ):
            raise DiscoverySnapshotUnavailable("configured LV2V offer schema is invalid")
        raw_offers = document.get("offers")
        if not isinstance(raw_offers, list):
            raise DiscoverySnapshotUnavailable("configured LV2V offers must be a list")
        observed_at = self.clock()
        values: list[DiscoveredPrice] = []
        for raw in raw_offers:
            decoded = self._offer(raw, observed_at)
            if decoded is None:
                raise DiscoverySnapshotUnavailable("configured LV2V offer is incomplete")
            if capability and decoded.capability != capability:
                continue
            if model and decoded.model != model:
                continue
            values.append(decoded)
        return values

    @staticmethod
    def _offer(raw: object, observed_at: datetime) -> DiscoveredPrice | None:
        if not isinstance(raw, Mapping):
            return None
        price = raw.get("price")
        if not isinstance(price, Mapping):
            return None
        orchestrator_url = HttpNetworkDiscovery._text(raw.get("orchestrator_url"))
        address = HttpNetworkDiscovery._text(raw.get("orchestrator_address"))
        capability = HttpNetworkDiscovery._text(raw.get("capability"))
        model = HttpNetworkDiscovery._text(raw.get("model")) or None
        currency = HttpNetworkDiscovery._text(price.get("currency")).lower()
        unit = HttpNetworkDiscovery._text(price.get("quantity_unit")).lower()
        try:
            numerator = int(str(price["numerator"]))
            denominator = int(str(price["denominator"]))
        except KeyError, TypeError, ValueError:
            return None
        if (
            not orchestrator_url
            or not address
            or not capability
            or currency != "wei"
            or unit != "720p-pixel-seconds"
            or numerator < 0
            or denominator <= 0
        ):
            return None
        constraints = raw.get("constraints", {})
        if not isinstance(constraints, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in constraints.items()
        ):
            return None
        return DiscoveredPrice(
            orchestrator_url,
            address.lower(),
            capability,
            model,
            {"orchestrator_url": orchestrator_url, **dict(constraints)},
            numerator,
            denominator,
            currency,
            unit,
            observed_at,
        )


class HttpNetworkDiscovery:
    def __init__(
        self,
        urls: Sequence[str],
        *,
        timeout_seconds: float = 10,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not urls:
            raise ValueError("at least one discovery URL is required")
        self.urls = tuple(urls)
        self.timeout_seconds = timeout_seconds
        self.clock = clock or (lambda: datetime.now(UTC))

    async def discover(
        self, capability: str | None, model: str | None
    ) -> Sequence[DiscoveredPrice]:
        values: list[DiscoveredPrice] = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                for url in self.urls:
                    params = {key: value for key, value in (("app", capability),) if value}
                    payload = await self._get_list(client, url, params=params)
                    if self._incomplete(payload):
                        raise DiscoverySnapshotUnavailable(
                            "discovery source omitted complete priced runner records"
                        )
                    values.extend(self._decode(payload, capability=capability, model=model))
        except DiscoverySnapshotUnavailable:
            raise
        except (httpx.HTTPError, ValueError) as error:
            raise DiscoverySnapshotUnavailable("discovery source unavailable") from error
        if not values and capability is None and model is None:
            raise DiscoverySnapshotUnavailable("discovery source returned no priced runners")
        return values

    @staticmethod
    async def _get_list(
        client: httpx.AsyncClient, url: str, *, params: Mapping[str, str]
    ) -> list[object]:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("discovery response must be a list")
        return payload

    @staticmethod
    def _incomplete(payload: Sequence[object]) -> bool:
        """Reject partial signer snapshots instead of bypassing signer filtering."""
        saw_priced_runners = False
        for entry in payload:
            if not isinstance(entry, Mapping):
                continue
            capabilities = entry.get("capabilities")
            runners = entry.get("runners")
            if isinstance(capabilities, list) and capabilities and not runners:
                return True
            if isinstance(runners, list) and runners:
                saw_priced_runners = True
        return not saw_priced_runners

    def _decode(
        self, payload: Sequence[object], *, capability: str | None, model: str | None
    ) -> list[DiscoveredPrice]:
        observed_at = self.clock()
        values: list[DiscoveredPrice] = []
        for entry in payload:
            if not isinstance(entry, Mapping):
                continue
            orchestrator_url = self._text(entry.get("address"))
            runners = entry.get("runners")
            if not orchestrator_url or not isinstance(runners, list):
                continue
            for runner in runners:
                decoded = self._runner(
                    runner,
                    orchestrator_url=orchestrator_url,
                    observed_at=observed_at,
                )
                if decoded is None:
                    continue
                if capability and decoded.capability != capability:
                    continue
                if model and decoded.model != model:
                    continue
                values.append(decoded)
        return values

    @classmethod
    def _runner(
        cls, runner: object, *, orchestrator_url: str, observed_at: datetime
    ) -> DiscoveredPrice | None:
        if not isinstance(runner, Mapping):
            return None
        url = cls._text(runner.get("url"))
        capability = cls._text(runner.get("app"))
        price_info = runner.get("price_info")
        if not url or not capability or not isinstance(price_info, Mapping):
            return None
        price = cls._ratio(price_info.get("price"))
        currency = cls._text(price_info.get("currency")).lower()
        unit = cls._text(price_info.get("unit")).lower()
        if price is None or not currency or not unit:
            return None
        constraints = {
            "orchestrator_url": orchestrator_url,
            "runner_id": cls._text(runner.get("runner_id")),
            "mode": cls._text(runner.get("mode")),
        }
        gpu = runner.get("gpu")
        if isinstance(gpu, Mapping) and (gpu_name := cls._text(gpu.get("name"))):
            constraints["gpu"] = gpu_name
        return DiscoveredPrice(
            url,
            cls._text(entry) if (entry := runner.get("orchestrator_address")) else None,
            capability,
            cls._text(runner.get("model")) or cls._text(runner.get("model_id")) or None,
            constraints,
            price[0],
            price[1],
            currency,
            unit,
            observed_at,
        )

    @staticmethod
    def _text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _ratio(value: object) -> tuple[int, int] | None:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        try:
            decimal = Decimal(str(value))
        except InvalidOperation:
            return None
        if not decimal.is_finite() or decimal < 0:
            return None
        return decimal.as_integer_ratio()
