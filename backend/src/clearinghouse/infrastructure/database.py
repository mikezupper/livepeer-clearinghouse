"""PostgreSQL engine, readiness, and transaction boundary."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy import MetaData, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from clearinghouse.application.ports import UnitOfWork
from clearinghouse.infrastructure.config import Settings
from clearinghouse.infrastructure.telemetry import record_database_result

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)


class SqlAlchemyUnitOfWork(UnitOfWork):
    """One explicitly committed SQLAlchemy session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        try:
            if exception_type is not None:
                await self.session.rollback()
        finally:
            await self.session.close()
            self.session = None

    async def commit(self) -> None:
        """Commit the current transaction."""
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        await self.session.commit()

    async def rollback(self) -> None:
        """Roll back the current transaction."""
        if self.session is None:
            raise RuntimeError("unit of work is not active")
        await self.session.rollback()


class PostgresStore:
    """Fixed reference store implementation."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> PostgresStore:
        """Build the store without connecting until first use."""
        engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            pool_timeout=settings.database_pool_timeout_seconds,
        )
        return cls(engine)

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        """Create a fresh unit of work for one application operation."""
        return SqlAlchemyUnitOfWork(self._sessions)

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Expose the shared factory to concrete PostgreSQL repositories."""
        return self._sessions

    async def readiness(self) -> bool:
        """Return whether PostgreSQL can execute a minimal query."""
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            record_database_result(False)
            return False
        record_database_result(True)
        return True

    async def close(self) -> None:
        """Release pooled database resources."""
        await self.engine.dispose()
