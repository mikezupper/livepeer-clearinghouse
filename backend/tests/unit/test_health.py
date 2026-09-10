"""HTTP health contract tests."""

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from clearinghouse.infrastructure.config import Settings
from clearinghouse.main import create_app, runtime_openapi


@pytest.fixture
def store() -> AsyncMock:
    fake = AsyncMock()
    fake.readiness.return_value = True
    return fake


@pytest.fixture
def client(store: AsyncMock) -> Generator[TestClient]:
    app = create_app(Settings(environment="test", _env_file=None), store=store)
    with TestClient(app) as test_client:
        yield test_client


def test_liveness_does_not_probe_dependencies(client: TestClient, store: AsyncMock) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    store.readiness.assert_not_awaited()


def test_readiness_succeeds_when_store_is_usable(client: TestClient, store: AsyncMock) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    store.readiness.assert_awaited_once()


def test_readiness_fails_closed(client: TestClient, store: AsyncMock) -> None:
    store.readiness.return_value = False
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    payload = response.json()
    assert payload["type"] == "urn:livepeer:clearinghouse:dependency-unavailable"
    assert payload["title"] == "Required dependency unavailable"
    assert payload["status"] == 503
    assert payload["request_id"]


def test_readiness_requires_private_operational_aggregate_without_exposing_it(
    client: TestClient, store: AsyncMock
) -> None:
    operational = AsyncMock()
    operational.status.return_value.status = "degraded"
    client.app.state.operational_readiness = operational  # type: ignore[attr-defined]
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert "migration" not in response.text
    assert "heartbeat" not in response.text
    operational.status.assert_awaited_once_with("20260910_0008")
    store.readiness.assert_awaited_once()


def test_all_private_api_responses_disable_caching(client: TestClient) -> None:
    response = client.get("/v1/not-a-real-route")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_openapi_schema_is_cached_and_health_is_public(client: TestClient) -> None:
    first = client.app.openapi()  # type: ignore[attr-defined]
    assert client.app.openapi() is first  # type: ignore[attr-defined]
    assert first["paths"]["/health/live"]["get"]["security"] == []


@pytest.mark.parametrize(
    ("generated", "message"),
    [
        ({"components": []}, "components"),
        ({"components": {"schemas": []}}, "schemas"),
        ({"components": {}, "paths": []}, "paths"),
        ({"components": {}, "paths": {1: {}}}, "path entries"),
        ({"components": {}, "paths": {"/x": {"get": []}}}, "operations"),
        ({"components": {}, "paths": {"/x": {"get": {"responses": []}}}}, "responses"),
    ],
)
def test_runtime_openapi_rejects_invalid_generator_shapes(
    monkeypatch: pytest.MonkeyPatch, generated: dict[str, Any], message: str
) -> None:
    monkeypatch.setattr("clearinghouse.main.get_openapi", lambda **_arguments: generated)
    with pytest.raises(TypeError, match=message):
        runtime_openapi(cast(Any, SimpleNamespace(routes=[])))
