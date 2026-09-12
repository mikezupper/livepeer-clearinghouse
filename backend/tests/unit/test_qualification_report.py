from __future__ import annotations

import json
from pathlib import Path

from scripts.qualification_report import build_report, latest_evidence


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_report_combines_latest_evidence_with_plan_statuses(tmp_path: Path) -> None:
    write(
        tmp_path / "plan-latest.json",
        {
            "cases": [
                {"id": "live", "source": "live", "status": "runnable", "reason": "offer"},
                {
                    "id": "missing",
                    "source": "live",
                    "status": "unavailable",
                    "reason": "no offer",
                },
                {
                    "id": "pending",
                    "source": "controlled",
                    "status": "runnable",
                    "reason": "fixture",
                },
            ]
        },
    )
    write(
        tmp_path / "gateway-live-1.json",
        {
            "schema": "livepeer.clearinghouse.gateway-qualification.v1",
            "case_id": "live",
            "status": "passed",
            "cost": {"quoted_fee": "7", "computed_fee": "8"},
        },
    )
    report, counts = build_report(tmp_path)

    assert counts == {"passed": 1, "failed": 0, "not-runnable": 1, "not-run": 1}
    assert "Live quote-derived total: 7 wei" in report
    assert "Live signer-computed total: 8 wei" in report
    assert "| missing | live | not-runnable | no offer |" in report


def test_evidence_reader_ignores_invalid_and_maps_legacy_gateway(tmp_path: Path) -> None:
    (tmp_path / "invalid.json").write_text("not-json", encoding="utf-8")
    write(
        tmp_path / "gateway-old.json",
        {"schema": "livepeer.clearinghouse.gateway-qualification.v1", "status": "passed"},
    )
    write(tmp_path / "unrelated.json", {"schema": "other"})

    assert latest_evidence(tmp_path)["persistent-short"]["status"] == "passed"
