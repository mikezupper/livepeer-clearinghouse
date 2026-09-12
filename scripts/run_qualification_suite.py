#!/usr/bin/env python3
"""Execute explicitly selected, preplanned billing qualification cases."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from scripts.qualification_cases import (
    DEFAULT_MANIFEST,
    PlannedCase,
    choose_cases,
    load_cases,
    plan_cases,
    plan_document,
)
from scripts.qualification_controlled import run as run_controlled
from scripts.qualify_gateway import (
    QualificationError,
    Settings,
    load_env,
    qualify,
    request_json,
)


def _positive(values: dict[str, str], name: str, default: int) -> int:
    try:
        value = int(values.get(name, "") or default)
    except ValueError as error:
        raise QualificationError(f"{name} must be an integer") from error
    if value <= 0:
        raise QualificationError(f"{name} must be positive")
    return value


def _write_plan(values: dict[str, str], planned: tuple[PlannedCase, ...]) -> None:
    maximum = _positive(values, "QUALIFICATION_MAX_TOTAL_FEE_WEI", 1_000_000_000_000)
    document = plan_document(planned, maximum)
    document["generated_at"] = datetime.now(UTC).isoformat()
    root = Path(values.get("QUAL_EVIDENCE_ROOT", "tmp/qualification")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    (root / "plan-latest.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("qualification.env"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cases", required=True, help="comma-separated explicit case ids")
    arguments = parser.parse_args()
    try:
        values = load_env(arguments.env_file)
        selected = choose_cases(load_cases(arguments.manifest), arguments.cases)
        base = Settings.from_values(values)
        response, _headers = request_json(base, "/v1/offers")
        offers = response.get("items", []) if isinstance(response, dict) else []
        planned = plan_cases(
            selected,
            offers,
            values,
            max_total_wei=_positive(values, "QUALIFICATION_MAX_TOTAL_FEE_WEI", 1_000_000_000_000),
            max_case_wei=_positive(values, "QUALIFICATION_MAX_CASE_FEE_WEI", 100_000_000_000),
            max_duration_seconds=float(
                values.get("QUALIFICATION_MAX_DURATION_SECONDS", "30") or "30"
            ),
            allow_lv2v=values.get("QUALIFICATION_ALLOW_LV2V", "false").lower()
            in {"1", "true", "yes", "on"},
        )
        _write_plan(values, planned)
        failures = False
        for item in planned:
            case = item.case
            if item.status != "runnable":
                print(f"{case.id}: not runnable ({item.reason})")
                continue
            if case.source == "controlled":
                failures = (
                    run_controlled(case.id, arguments.manifest, base.evidence_root) != 0
                ) or failures
                continue
            if not base.execute:
                raise QualificationError(
                    "QUAL_EXECUTE=true is required before selected live cases may run"
                )
            if item.offer is None:
                raise QualificationError(f"case {case.id} omitted its planned live offer")
            orchestrator = item.offer.get("constraints", {}).get("orchestrator_url")
            payload = None
            if case.action == "call":
                raw = values.get("QUAL_FIXED_PAYLOAD_JSON", "")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise QualificationError("QUAL_FIXED_PAYLOAD_JSON must be an object")
                payload = parsed
            if case.action == "lv2v":
                payload = {
                    "input_path": str(Path(values["QUAL_LV2V_INPUT"]).expanduser().resolve()),
                    "model": values["QUAL_LV2V_MODEL"],
                    "max_pixels": case.max_quantity,
                }
            settings = replace(
                base,
                case_id=case.id,
                capability=case.capability,
                model=case.model,
                orchestrator=str(orchestrator) if orchestrator else None,
                currency=case.currency,
                unit=case.unit,
                max_numerator=case.max_numerator,
                max_denominator=case.max_denominator,
                session_hold_seconds=case.duration_seconds,
                action=case.action,
                request_payload=payload,
                minimum_events=case.minimum_events,
            )
            evidence, path = qualify(settings)
            print(f"{case.id}: {evidence['status']} ({path})")
        return 1 if failures else 0
    except (QualificationError, json.JSONDecodeError, ValueError) as error:
        print(f"qualification suite failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
