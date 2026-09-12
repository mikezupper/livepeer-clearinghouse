#!/usr/bin/env python3
"""Run deterministic billing scenarios one-by-one and record sanitized evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts.qualification_cases import DEFAULT_MANIFEST, choose_cases, load_cases
from scripts.qualify_gateway import QualificationError, load_env

ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = "backend/tests/integration/test_billing_scenarios.py"
SCENARIOS = {
    "fixed-charged-failure": "test_fixed_success_charged_failure_and_prepayment_failure",
    "persistent-interrupted": "test_persistent_multi_cycle_and_interruption",
    "runtime-price-policy": "test_runtime_price_policy_uses_exact_rational_comparison",
    "signer-event-replay": "test_duplicate_delayed_and_foreign_events",
    "lv2v-pixel-accounting": "test_lv2v_pixel_accounting_and_unit_rejection",
}


def run(selected: str | None, manifest: Path, evidence_root: Path) -> int:
    cases = choose_cases(load_cases(manifest), selected)
    controlled = [case for case in cases if case.source == "controlled"]
    if not controlled:
        raise QualificationError("selection contains no controlled scenarios")
    evidence_root.mkdir(parents=True, exist_ok=True)
    failed = False
    for case in controlled:
        test_name = SCENARIOS.get(case.scenario or "")
        if test_name is None:
            raise QualificationError(f"case {case.id} has no controlled test mapping")
        started = time.monotonic()
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and reviewed node IDs
            [
                sys.executable,
                "-m",
                "pytest",
                f"{TEST_FILE}::{test_name}",
                "-q",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        status = "passed" if completed.returncode == 0 else "failed"
        failed = failed or completed.returncode != 0
        document = {
            "schema": "livepeer.clearinghouse.billing-qualification.v1",
            "case_id": case.id,
            "source": "controlled",
            "status": status,
            "completed_at": datetime.now(UTC).isoformat(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "maximum_authorized_wei": str(case.max_authorized_wei),
            "actual_authorized_wei": "0",
            "test": f"{TEST_FILE}::{test_name}",
            "result_tail": (completed.stdout + completed.stderr)[-1000:],
        }
        (evidence_root / f"controlled-{case.id}.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"{case.id}: {status}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("qualification.env"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cases")
    arguments = parser.parse_args()
    try:
        values = load_env(arguments.env_file)
        root = Path(values.get("QUAL_EVIDENCE_ROOT", "tmp/qualification")).expanduser()
        return run(arguments.cases, arguments.manifest, root)
    except QualificationError as error:
        print(f"controlled qualification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
