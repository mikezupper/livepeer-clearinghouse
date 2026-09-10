"""Database adapter behavior tests without a live database."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from clearinghouse.infrastructure.config import Settings
from clearinghouse.infrastructure.database import PostgresStore, SqlAlchemyUnitOfWork


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def unit_of_work(session: AsyncMock) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(MagicMock(return_value=session))


async def test_unit_of_work_commits_and_closes(
    unit_of_work: SqlAlchemyUnitOfWork, session: AsyncMock
) -> None:
    async with unit_of_work:
        await unit_of_work.commit()
    session.commit.assert_awaited_once()
    session.close.assert_awaited_once()


async def test_unit_of_work_rolls_back_exception(
    unit_of_work: SqlAlchemyUnitOfWork, session: AsyncMock
) -> None:
    with pytest.raises(ValueError, match="failure"):
        async with unit_of_work:
            raise ValueError("failure")
    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


async def test_inactive_unit_of_work_rejects_commit_and_rollback(
    unit_of_work: SqlAlchemyUnitOfWork,
) -> None:
    with pytest.raises(RuntimeError, match="not active"):
        await unit_of_work.commit()
    with pytest.raises(RuntimeError, match="not active"):
        await unit_of_work.rollback()


async def test_explicit_rollback(unit_of_work: SqlAlchemyUnitOfWork, session: AsyncMock) -> None:
    async with unit_of_work:
        await unit_of_work.rollback()
    session.rollback.assert_awaited_once()


async def test_store_readiness_returns_true() -> None:
    connection = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = connection
    engine = MagicMock()
    engine.connect.return_value = context
    store = PostgresStore(engine)
    assert await store.readiness() is True
    connection.execute.assert_awaited_once()


async def test_store_readiness_returns_false_for_sqlalchemy_error() -> None:
    context = AsyncMock()
    context.__aenter__.side_effect = OperationalError("SELECT 1", {}, Exception("down"))
    engine = MagicMock()
    engine.connect.return_value = context
    store = PostgresStore(engine)
    assert await store.readiness() is False


async def test_store_close_disposes_engine() -> None:
    engine = AsyncMock()
    store = PostgresStore(engine)
    await store.close()
    engine.dispose.assert_awaited_once()


def test_store_factory_and_unit_of_work() -> None:
    store = PostgresStore.from_settings(Settings(_env_file=None))
    assert isinstance(store.unit_of_work(), SqlAlchemyUnitOfWork)
