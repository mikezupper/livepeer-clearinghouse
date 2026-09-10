"""Exit successfully only while the configured metering worker heartbeat is fresh."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine


async def healthy() -> bool:
    database_url = os.environ["CLEARINGHOUSE_DATABASE_URL"]
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            result = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM metering_worker_heartbeats "
                    "WHERE consumer_group=:consumer_group AND topic=:topic "
                    "AND signer_id=:signer_id AND last_seen_at >= CURRENT_TIMESTAMP "
                    "- make_interval(secs => :stale))"
                ),
                {
                    "consumer_group": os.environ["CLEARINGHOUSE_KAFKA_METERING_GROUP_ID"],
                    "topic": os.environ["CLEARINGHOUSE_KAFKA_METERING_TOPIC"],
                    "signer_id": os.environ["CLEARINGHOUSE_SIGNER_ID"],
                    "stale": int(os.environ["CLEARINGHOUSE_METERING_HEARTBEAT_STALE_SECONDS"]),
                },
            )
            return result is True
    finally:
        await engine.dispose()


def main() -> int:
    try:
        return 0 if asyncio.run(healthy()) else 1
    except KeyError, OSError, SQLAlchemyError, ValueError:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
