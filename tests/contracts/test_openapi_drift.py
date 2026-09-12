from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.check_contract_drift import assert_openapi_matches, generated_openapi

ROOT = Path(__file__).resolve().parents[2]


def test_canonical_openapi_matches_the_composed_core() -> None:
    canonical = json.loads((ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"))
    runtime = generated_openapi()
    assert_openapi_matches(canonical, runtime)
    expected = {
        "/v1/auth/email/code",
        "/v1/auth/email/verify",
        "/v1/auth/session",
        "/v1/offers",
        "/v1/discovery",
        "/v1/workloads",
        "/v1/usage",
        "/v1/costs",
        "/v1/summary",
        "/v1/compat/go-livepeer/authorize",
        "/v1/admin/overview",
        "/v1/admin/users",
        "/v1/admin/workloads",
        "/v1/admin/global-stop",
    }
    assert expected <= set(runtime["paths"])
    assert runtime["info"]["version"] == "0.2.0"


def test_openapi_drift_is_rejected() -> None:
    runtime = generated_openapi()
    changed = {**runtime, "info": {**runtime["info"], "title": "drift"}}
    with pytest.raises(RuntimeError, match="differs"):
        assert_openapi_matches(changed, runtime)
