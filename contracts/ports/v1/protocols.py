"""Dependency-free v1 protocols implemented by built-in and external adapters.

Boundary values are already decoded when passed to a port. Integer amounts are
unbounded Python integers and exact rates are numerator/denominator pairs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class PortName(StrEnum):
    IDENTITY = "identity"
    SIGNER = "signer"
    CUSTODY = "custody"
    METERING = "metering"
    PRICING = "pricing"
    COLLECTION = "collection"
    EVENTS_OUT = "events_out"


@dataclass(frozen=True, slots=True)
class PortDescriptor:
    name: PortName
    contract_version: str
    capabilities: frozenset[str]


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    name: str
    version: str
    source: str
    ports: tuple[PortDescriptor, ...]


@dataclass(frozen=True, slots=True)
class ExactRate:
    numerator: int
    denominator: int
    charge_unit: str
    quantity_unit: str


@dataclass(frozen=True, slots=True)
class PrincipalIdentity:
    tenant_id: str
    account_id: str
    principal_id: str


@dataclass(frozen=True, slots=True)
class SignerEndpoint:
    signer_id: str
    signer_url: str
    discovery_url: str


@dataclass(frozen=True, slots=True)
class UsageObservation:
    producer_id: str
    transport_event_id: str
    payload: Mapping[str, object]


@runtime_checkable
class IdentityPort(Protocol):
    async def resolve_bearer(self, bearer: str) -> PrincipalIdentity: ...

    async def resolve_provider_subject(self, provider: str, subject: str) -> PrincipalIdentity: ...


@runtime_checkable
class SignerPort(Protocol):
    async def endpoints_for(self, capability: str, model: str | None) -> SignerEndpoint: ...

    async def capabilities(self) -> tuple[Mapping[str, object], ...]: ...


@runtime_checkable
class CustodyPort(Protocol):
    async def readiness(self) -> bool: ...

    async def public_address(self, signer_id: str) -> str: ...


@runtime_checkable
class MeteringPort(Protocol):
    def observations(self) -> AsyncIterator[UsageObservation]: ...

    async def acknowledge(self, observation: UsageObservation) -> None: ...

    async def quarantine(self, observation: UsageObservation, reason: str) -> None: ...


@runtime_checkable
class PricingPort(Protocol):
    async def quote(
        self, account_id: str, capability: str, model: str | None, quantity_unit: str
    ) -> ExactRate: ...


@runtime_checkable
class CollectionPort(Protocol):
    async def validate_grant_reference(
        self, account_id: str, external_reference: str | None
    ) -> None: ...


@runtime_checkable
class EventsOutPort(Protocol):
    async def publish(
        self, event_type: str, event_id: str, payload: Mapping[str, object]
    ) -> None: ...
