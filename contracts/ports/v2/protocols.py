"""Dependency-free extension ports for the simplified Clearinghouse core.

Adapters are reviewed, immutable deployment dependencies selected explicitly by
the composition root. The core never scans annotations or imports arbitrary
modules named by an environment variable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


class DiscoverySnapshotUnavailable(RuntimeError):
    """A discovery source could not provide a complete priced snapshot."""


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    provider: str
    subject: str
    verified_email: str


@dataclass(frozen=True, slots=True)
class DiscoveredPrice:
    orchestrator_url: str
    orchestrator_address: str | None
    capability: str
    model: str | None
    constraints: Mapping[str, str]
    numerator: int
    denominator: int
    currency: str
    quantity_unit: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class CoreEvent:
    id: str
    type: str
    occurred_at: datetime
    subject_id: str
    payload: Mapping[str, object]


@runtime_checkable
class IdentityProvider(Protocol):
    async def resolve_callback(self, values: Mapping[str, str]) -> ProviderIdentity: ...


@runtime_checkable
class NetworkDiscoveryProvider(Protocol):
    async def discover(
        self, capability: str | None, model: str | None
    ) -> Sequence[DiscoveredPrice]: ...


@runtime_checkable
class AuthorizationPolicy(Protocol):
    async def permits(
        self,
        *,
        account_id: str,
        workload_id: str,
        capability: str,
        advertised_price: DiscoveredPrice,
    ) -> bool: ...


@runtime_checkable
class CoreEventSink(Protocol):
    async def publish(self, event: CoreEvent) -> None: ...
