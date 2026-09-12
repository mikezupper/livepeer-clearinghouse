# Backend

The core's optional `CLEARINGHOUSE_LV2V_OFFERS_FILE` adapter reads an
operator-reviewed `livepeer.clearinghouse.lv2v-offers.v1` JSON artifact. It is
combined atomically with priced runner discovery; invalid or incomplete input
cannot replace a previously safe snapshot. The default deployment leaves the
adapter disabled.

The backend is a Python 3.14 FastAPI service packaged and locked with UV. Its
source follows the inward dependency rule documented in the root architecture:
domain and application code have no knowledge of FastAPI, SQLite, Kafka, or
provider adapters.

```shell
uv sync --frozen
uv run clearinghouse
```

Configuration is read from `CLEARINGHOUSE_*` environment variables. SQLite is
initialized transactionally at startup and defaults to
`./data/clearinghouse.db`; Compose stores it in the persistent `sqlite-data`
volume. See [`.env.example`](../.env.example) and the
[deployment guide](../docs/operations/deployment.md) for the complete reference
configuration. The API and Kafka consumer run in the same supervised process in
the default single-writer profile.

## Verification

`uv run pytest --cov --cov-report=json:backend/coverage.json` records statement,
line, and branch execution. `uv run python backend/scripts/check_coverage.py`
then enforces 85% independently for lines/statements, branches, and functions.
A function counts as covered when coverage records execution of at least one
executable statement in its body. The architecture test rejects imports that
point from domain or application code toward infrastructure or HTTP adapters.
Storage integration tests use real temporary SQLite files; no external database
is required for backend unit or integration tests.
