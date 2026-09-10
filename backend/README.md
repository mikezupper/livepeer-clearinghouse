# Backend

The backend is a Python 3.14 FastAPI service packaged and locked with UV. Its
source follows the inward dependency rule documented in the root architecture:
domain and application code have no knowledge of FastAPI, SQLAlchemy, or other
adapters.

```shell
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn clearinghouse.main:create_app --factory
```

Configuration is read from `CLEARINGHOUSE_*` environment variables. The
PostgreSQL DSN is required outside tests; see `.env.example` for deployment
configuration.

## Verification

`uv run pytest --cov --cov-report=json:backend/coverage.json` records statement,
line, and branch execution. `uv run python backend/scripts/check_coverage.py`
then enforces 85% independently for lines/statements, branches, and functions.
A function counts as covered when coverage records execution of at least one
executable statement in its body. The architecture test rejects imports that
point from domain or application code toward infrastructure or HTTP adapters.
