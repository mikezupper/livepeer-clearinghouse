#!/usr/bin/env python3
"""Summarize sanitized billing qualification plans and evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def latest_evidence(root: Path) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime):
        document = _read(path)
        if not document or path.name == "plan-latest.json":
            continue
        case_id = document.get("case_id")
        if not isinstance(case_id, str):
            if document.get("schema") == "livepeer.clearinghouse.gateway-qualification.v1":
                case_id = "persistent-short"
            else:
                continue
        document["_path"] = str(path)
        values[case_id] = document
    return values


def build_report(root: Path) -> tuple[str, dict[str, int]]:
    plan = _read(root / "plan-latest.json") or {"cases": []}
    raw_cases = plan.get("cases")
    cases = raw_cases if isinstance(raw_cases, list) else []
    evidence = latest_evidence(root)
    rows: list[str] = []
    counts = {"passed": 0, "failed": 0, "not-runnable": 0, "not-run": 0}
    live_quoted_total = 0
    live_computed_total = 0
    controlled_quoted_total = 0
    controlled_computed_total = 0
    for raw in cases:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        case_id = raw["id"]
        observed = evidence.get(case_id)
        if observed:
            status = str(observed.get("status", "failed"))
            if status not in {"passed", "failed"}:
                status = "failed"
            cost = observed.get("cost")
            if isinstance(cost, dict):
                quoted = int(str(cost.get("quoted_fee", 0)))
                computed = int(str(cost.get("computed_fee", 0)))
                if observed.get("source") == "controlled-broker":
                    controlled_quoted_total += quoted
                    controlled_computed_total += computed
                else:
                    live_quoted_total += quoted
                    live_computed_total += computed
        elif raw.get("status") in {"unavailable", "ambiguous", "over-budget"}:
            status = "not-runnable"
        else:
            status = "not-run"
        counts[status] += 1
        rows.append(f"| {case_id} | {raw.get('source', '')} | {status} | {raw.get('reason', '')} |")
    lines = [
        "# Billing qualification report",
        "",
        "| Case | Source | Result | Planning note |",
        "|---|---|---|---|",
        *rows,
        "",
        f"- Passed: {counts['passed']}",
        f"- Failed: {counts['failed']}",
        f"- Not runnable: {counts['not-runnable']}",
        f"- Runnable but not run: {counts['not-run']}",
        f"- Live quote-derived total: {live_quoted_total} wei",
        f"- Live signer-computed total: {live_computed_total} wei",
        f"- Controlled modeled quote total: {controlled_quoted_total} wei",
        f"- Controlled modeled signer total: {controlled_computed_total} wei",
        "",
        "Controlled cases authorize no network spend. Live results are observations "
        "of the staged environment, not deterministic CI fixtures.",
        "",
    ]
    return "\n".join(lines), counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, default=Path("tmp/qualification"))
    arguments = parser.parse_args()
    report, counts = build_report(arguments.evidence_root)
    arguments.evidence_root.mkdir(parents=True, exist_ok=True)
    path = arguments.evidence_root / "report-latest.md"
    path.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"report: {path}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
