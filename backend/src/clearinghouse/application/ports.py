"""Store contracts consumed by application workflows."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self, runtime_checkable


@runtime_checkable
class UnitOfWork(Protocol):
    """Atomic transaction boundary for one application operation."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@runtime_checkable
class Store(Protocol):
    """PostgreSQL-backed capabilities exposed to workflows."""

    def unit_of_work(self) -> UnitOfWork: ...

    async def readiness(self) -> bool: ...

    async def close(self) -> None: ...
