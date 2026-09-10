"""Construct a PostgreSQL URL without placing its password in process arguments."""

from __future__ import annotations

import os
from urllib.parse import quote


def build_database_url(user: str, password: str, host: str, port: str, database: str) -> str:
    """Return an async SQLAlchemy URL with the password encoded as userinfo."""
    return f"postgresql+asyncpg://{user}:{quote(password, safe='')}@{host}:{port}/{database}"


if __name__ == "__main__":
    print(
        build_database_url(
            os.environ["CLEARINGHOUSE_DATABASE_USER"],
            os.environ["CLEARINGHOUSE_DATABASE_PASSWORD"],
            os.environ["CLEARINGHOUSE_DATABASE_HOST"],
            os.environ["CLEARINGHOUSE_DATABASE_PORT"],
            os.environ["CLEARINGHOUSE_DATABASE_NAME"],
        )
    )
