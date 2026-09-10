"""Full semantic OpenAPI drift and security regression tests."""

# ruff: noqa: S101 -- contract assertions use pytest's native assertion diagnostics.

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.check_contract_drift import (
    assert_expressive_openapi,
    assert_openapi_matches,
    generated_openapi,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def canonical() -> dict[str, Any]:
    return json.loads((ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"))


def test_canonical_is_the_complete_deterministic_runtime_schema(
    canonical: dict[str, Any],
) -> None:
    runtime = generated_openapi()
    assert_openapi_matches(canonical, runtime)
    assert runtime["components"]["securitySchemes"] == {
        "bearerAuth": {"bearerFormat": "opaque", "scheme": "bearer", "type": "http"},
        "cookieAuth": {"in": "cookie", "name": "och_session", "type": "apiKey"},
        "signerSecret": {
            "description": "Private deployment credential used only by the configured signer.",
            "scheme": "bearer",
            "type": "http",
        },
    }
    assert runtime["paths"]["/health/live"]["get"]["security"] == []
    assert runtime["paths"]["/v1/accounts"]["get"]["security"] == [{"cookieAuth": []}]
    assert runtime["paths"]["/v1/sessions"]["post"]["security"] == [
        {"bearerAuth": []},
        {"cookieAuth": []},
    ]
    assert runtime["paths"]["/v1/authorize"]["post"]["security"] == [{"signerSecret": []}]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("info", "title"), "drifted title"),
        (("paths", "/v1/accounts", "get", "operationId"), "driftedOperation"),
        (("paths", "/v1/accounts", "get", "security"), []),
        (("paths", "/v1/accounts", "get", "responses", "599"), {"description": "drift"}),
        (
            (
                "components",
                "schemas",
                "CreateAccount",
                "properties",
                "display_name",
                "maxLength",
            ),
            999,
        ),
    ],
)
def test_full_comparison_rejects_every_semantic_layer(
    canonical: dict[str, Any], path: tuple[str, ...], value: object
) -> None:
    changed = copy.deepcopy(canonical)
    target: dict[str, Any] = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(RuntimeError, match="runtime OpenAPI differs"):
        assert_openapi_matches(changed, canonical)


def test_expressiveness_rejects_generic_success_missing_security_and_wrong_problem_media() -> None:
    operation = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {"schema": {"type": "object", "additionalProperties": True}}
                }
            },
            "401": {"content": {"application/json": {"schema": {"type": "object"}}}},
        }
    }
    with pytest.raises(RuntimeError) as failure:
        assert_expressive_openapi({"paths": {"/v1/example": {"get": operation}}})
    message = str(failure.value)
    assert "security must be explicit" in message
    assert "generic schema is forbidden" in message
    assert "application/problem+json is required" in message


def test_expressiveness_accepts_typed_json_empty_success_and_problem_detail() -> None:
    typed = {"$ref": "#/components/schemas/Example"}
    problem = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"status": {"type": "integer"}},
    }
    assert_expressive_openapi(
        {
            "paths": {
                "/v1/example": {
                    "get": {
                        "security": [{"cookieAuth": []}],
                        "responses": {
                            "200": {"content": {"application/json": {"schema": typed}}},
                            "401": {"content": {"application/problem+json": {"schema": problem}}},
                            "422": {"content": {"application/json": {"schema": typed}}},
                        },
                    },
                    "delete": {
                        "security": [{"cookieAuth": []}],
                        "responses": {
                            "204": {},
                            "401": {"content": {"application/problem+json": {"schema": problem}}},
                        },
                    },
                }
            }
        }
    )
