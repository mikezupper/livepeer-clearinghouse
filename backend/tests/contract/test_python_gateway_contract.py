"""Executable contract with the official livepeer-python-gateway checkout."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from clearinghouse.application.gateway_access import GatewayAccess

DEFAULT_GATEWAY = Path(__file__).resolve().parents[4] / "livepeer-python-gateway"


def test_workload_access_round_trips_through_sdk_token_parser() -> None:
    gateway_root = Path(os.environ.get("LIVEPEER_PYTHON_GATEWAY_ROOT", DEFAULT_GATEWAY))
    if not (gateway_root / "pyproject.toml").exists():
        pytest.skip("set LIVEPEER_PYTHON_GATEWAY_ROOT to run the cross-repository contract")
    access = GatewayAccess(
        "workload-secret",
        "https://clearinghouse.example.test",
        "https://clearinghouse.example.test/v1/discovery",
        "work_1",
        "2026-09-11T13:00:00+00:00",
    )
    program = """
import json, sys
from livepeer_gateway import parse_token
print(json.dumps(parse_token(sys.argv[1]), sort_keys=True))
"""
    uv = shutil.which("uv")
    assert uv is not None
    completed = subprocess.run(  # noqa: S603 - fixed executable and argument vector
        [uv, "run", "python", "-c", program, access.sdk_token()],
        cwd=gateway_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert json.loads(completed.stdout) == {
        "discovery": access.discovery_url,
        "discovery_headers": {"Authorization": "Bearer workload-secret"},
        "orchestrators": None,
        "signer": access.signer_url,
        "signer_headers": {"Authorization": "Bearer workload-secret"},
    }


def test_static_orchestrators_are_supported_by_the_same_sdk_token() -> None:
    access = GatewayAccess(
        "secret",
        "https://signer.test",
        "https://discovery.test",
        "work",
        "later",
        ("https://orch-a.test", "https://orch-b.test"),
    )
    encoded = access.sdk_token()
    assert encoded.isascii()
