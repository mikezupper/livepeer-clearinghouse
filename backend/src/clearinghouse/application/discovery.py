"""Network discovery and immutable advertised-price observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from contracts.ports.v2.protocols import (
    DiscoveredPrice,
    DiscoverySnapshotUnavailable,
    NetworkDiscoveryProvider,
)

from clearinghouse.application.core_store import CoreStore
from clearinghouse.application.pagination import KeysetPage, StaleCursor
from clearinghouse.domain.core import CapabilityOffer, ExactPrice, PriceObservationId


@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    offers: tuple[CapabilityOffer, ...]
    stale: bool


@dataclass(frozen=True, slots=True)
class DiscoveryPage:
    page: KeysetPage[CapabilityOffer]
    stale: bool
    generation: str


class DiscoveryService:
    def __init__(
        self,
        store: CoreStore,
        provider: NetworkDiscoveryProvider,
        *,
        observation_ttl_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.observation_ttl_seconds = observation_ttl_seconds
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _observation_id(value: DiscoveredPrice) -> PriceObservationId:
        canonical = json.dumps(
            {
                "orchestrator_url": value.orchestrator_url,
                "orchestrator_address": value.orchestrator_address,
                "capability": value.capability,
                "model": value.model,
                "constraints": sorted(value.constraints.items()),
                "numerator": value.numerator,
                "denominator": value.denominator,
                "currency": value.currency,
                "quantity_unit": value.quantity_unit,
                "observed_at": value.observed_at.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return PriceObservationId(f"price_{hashlib.sha256(canonical.encode()).hexdigest()[:24]}")

    async def refresh(
        self, capability: str | None = None, model: str | None = None
    ) -> DiscoverySnapshot:
        now = self.clock()
        try:
            discovered = await self.provider.discover(None, None)
        except DiscoverySnapshotUnavailable:
            async with self.store.transaction() as transaction:
                cached = await self._all_offers(transaction, now=now)
            if not cached:
                raise
            return DiscoverySnapshot(self._filter(cached, capability, model), stale=True)
        offers = tuple(
            CapabilityOffer(
                self._observation_id(value),
                value.orchestrator_url,
                value.orchestrator_address,
                value.capability,
                value.model,
                tuple(sorted(value.constraints.items())),
                ExactPrice(
                    value.numerator,
                    value.denominator,
                    value.currency,
                    value.quantity_unit,
                ),
                value.observed_at,
                value.observed_at + timedelta(seconds=self.observation_ttl_seconds),
            )
            for value in discovered
        )
        async with self.store.transaction() as transaction:
            await transaction.replace_offers(offers)
            current = await self._all_offers(transaction, now=now)
        incoming_sources = {self._source(offer) for offer in offers}
        retained_sources = {self._source(offer) for offer in current} - incoming_sources
        return DiscoverySnapshot(
            self._filter(current, capability, model), stale=bool(retained_sources)
        )

    async def page(
        self,
        *,
        capability: str | None,
        model: str | None,
        limit: int,
        after: tuple[str, ...] = (),
        snapshot: str | None = None,
        stale: bool = False,
    ) -> DiscoveryPage:
        if snapshot is None:
            refreshed = await self.refresh()
            stale = refreshed.stale
        async with self.store.transaction() as transaction:
            generation = await transaction.offer_generation()
            if snapshot is not None and snapshot != generation:
                raise StaleCursor("the discovery snapshot changed; restart from the first page")
            page = await transaction.list_offers(
                now=self.clock(),
                limit=limit,
                after=after,
                capability=capability,
                model=model,
            )
        return DiscoveryPage(page, stale, generation)

    @staticmethod
    def _filter(
        offers: Sequence[CapabilityOffer], capability: str | None, model: str | None
    ) -> tuple[CapabilityOffer, ...]:
        return tuple(
            offer
            for offer in offers
            if (capability is None or offer.capability == capability)
            and (model is None or offer.model == model)
        )

    async def offers(self) -> Sequence[CapabilityOffer]:
        async with self.store.transaction() as transaction:
            return await self._all_offers(transaction, now=self.clock())

    @staticmethod
    async def _all_offers(transaction, *, now: datetime) -> tuple[CapabilityOffer, ...]:  # type: ignore[no-untyped-def]
        result: list[CapabilityOffer] = []
        after: tuple[str, ...] = ()
        while True:
            page = await transaction.list_offers(now=now, limit=200, after=after)
            result.extend(page.items)
            if page.next_key is None:
                return tuple(result)
            after = page.next_key

    @staticmethod
    def _source(offer: CapabilityOffer) -> str:
        return dict(offer.constraints).get("orchestrator_url", offer.orchestrator_url)


def sdk_discovery(offers: Sequence[CapabilityOffer]) -> list[dict[str, object]]:
    """Reconstruct the discovery envelope consumed by livepeer-python-gateway."""

    grouped: dict[str, list[dict[str, object]]] = {}
    for offer in offers:
        constraints = dict(offer.constraints)
        orchestrator = constraints.get("orchestrator_url", offer.orchestrator_url)
        runner: dict[str, object] = {
            "url": offer.orchestrator_url,
            "app": offer.capability,
            "runner_id": constraints.get("runner_id", ""),
            "mode": constraints.get("mode", ""),
            "price_info": {
                "price": str(Decimal(offer.price.numerator) / Decimal(offer.price.denominator)),
                "currency": offer.price.currency,
                "unit": offer.price.quantity_unit,
            },
        }
        gpu = constraints.get("gpu")
        if gpu:
            runner["gpu"] = {"name": gpu}
        grouped.setdefault(orchestrator, []).append(runner)
    return [{"address": address, "runners": runners} for address, runners in grouped.items()]
