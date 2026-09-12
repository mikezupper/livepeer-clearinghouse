#!/usr/bin/env python3
"""Plan guarded, case-driven Clearinghouse billing qualifications."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from scripts.qualify_gateway import QualificationError, load_env

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "qualification" / "cases.v1.json"


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    source: Literal["live", "controlled"]
    action: Literal["reserve", "interrupt", "call", "lv2v", "scenario"]
    capability: str
    model: str | None
    orchestrators: tuple[str, ...]
    currency: str
    unit: str
    max_numerator: int
    max_denominator: int
    max_quantity: int
    max_authorized_wei: int
    duration_seconds: float
    minimum_events: int
    scenario: str | None
    requires: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlannedCase:
    case: Case
    status: Literal["runnable", "unavailable", "ambiguous", "over-budget"]
    reason: str
    offer: dict[str, Any] | None = None


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise QualificationError(f"{name} must be an integer")
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise QualificationError(f"{name} must be an integer") from error
    if result < minimum:
        raise QualificationError(f"{name} must be at least {minimum}")
    return result


def load_cases(path: Path) -> tuple[Case, ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"case manifest could not be loaded: {error}") from error
    if not isinstance(document, dict) or document.get("schema") != (
        "livepeer.clearinghouse.qualification-cases.v1"
    ):
        raise QualificationError("unsupported qualification case schema")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise QualificationError("case manifest must contain cases")
    cases: list[Case] = []
    identifiers: set[str] = set()
    for index, raw in enumerate(raw_cases):
        if not isinstance(raw, dict):
            raise QualificationError(f"cases[{index}] must be an object")
        identifier = str(raw.get("id", "")).strip()
        if not identifier or identifier in identifiers:
            raise QualificationError("case ids must be non-empty and unique")
        identifiers.add(identifier)
        price = raw.get("price")
        if not isinstance(price, dict):
            raise QualificationError(f"case {identifier} requires a price object")
        source = raw.get("source")
        action = raw.get("action")
        if source not in {"live", "controlled"} or action not in {
            "reserve",
            "interrupt",
            "call",
            "lv2v",
            "scenario",
        }:
            raise QualificationError(f"case {identifier} has invalid source or action")
        if (source == "controlled") != (action == "scenario"):
            raise QualificationError(
                f"case {identifier} must pair controlled source with scenario action"
            )
        numerator = _integer(price.get("max_numerator"), "max_numerator")
        denominator = _integer(price.get("max_denominator"), "max_denominator", minimum=1)
        quantity = _integer(raw.get("max_quantity"), "max_quantity")
        computed_max = (quantity * numerator + denominator - 1) // denominator
        authorized = _integer(raw.get("max_authorized_wei"), "max_authorized_wei")
        if authorized < computed_max:
            raise QualificationError(
                f"case {identifier} maximum authorization is below its price/quantity bound"
            )
        try:
            duration = float(raw.get("duration_seconds", 0))
        except (TypeError, ValueError) as error:
            raise QualificationError(f"case {identifier} duration must be numeric") from error
        if duration < 0:
            raise QualificationError(f"case {identifier} duration must be non-negative")
        orchestrators = raw.get("orchestrators", [])
        requires = raw.get("requires", [])
        if not isinstance(orchestrators, list) or not all(
            isinstance(value, str) and value for value in orchestrators
        ):
            raise QualificationError(f"case {identifier} orchestrators must be strings")
        if not isinstance(requires, list) or not all(
            isinstance(value, str) and value for value in requires
        ):
            raise QualificationError(f"case {identifier} requirements must be strings")
        cases.append(
            Case(
                identifier,
                source,
                action,
                str(raw.get("capability", "")).strip(),
                str(raw["model"]).strip() if raw.get("model") else None,
                tuple(orchestrators),
                str(price.get("currency", "")).lower(),
                str(price.get("unit", "")).lower(),
                numerator,
                denominator,
                quantity,
                authorized,
                duration,
                _integer(raw.get("minimum_events", 0), "minimum_events"),
                str(raw["scenario"]).strip() if raw.get("scenario") else None,
                tuple(requires),
            )
        )
    if any(not case.capability or not case.currency or not case.unit for case in cases):
        raise QualificationError("every case requires capability, currency, and unit")
    return tuple(cases)


def choose_cases(cases: tuple[Case, ...], selected: str | None) -> tuple[Case, ...]:
    if not selected:
        return cases
    names = tuple(value.strip() for value in selected.split(",") if value.strip())
    if not names:
        raise QualificationError("case selection cannot be empty")
    indexed = {case.id: case for case in cases}
    missing = sorted(set(names) - indexed.keys())
    if missing:
        raise QualificationError("unknown qualification cases: " + ", ".join(missing))
    return tuple(indexed[name] for name in names)


def _offer_matches(case: Case, offer: object) -> bool:
    if not isinstance(offer, dict) or offer.get("capability") != case.capability:
        return False
    if case.model is not None and offer.get("model") != case.model:
        return False
    price = offer.get("price")
    constraints = offer.get("constraints")
    if not isinstance(price, dict) or not isinstance(constraints, dict):
        return False
    if case.orchestrators and constraints.get("orchestrator_url") not in case.orchestrators:
        return False
    try:
        numerator = int(str(price["numerator"]))
        denominator = int(str(price["denominator"]))
    except KeyError, TypeError, ValueError:
        return False
    return (
        denominator > 0
        and numerator >= 0
        and str(price.get("currency", "")).lower() == case.currency
        and str(price.get("quantity_unit", "")).lower() == case.unit
        and numerator * case.max_denominator <= case.max_numerator * denominator
    )


def plan_cases(
    cases: tuple[Case, ...],
    offers: object,
    values: dict[str, str],
    *,
    max_total_wei: int,
    max_case_wei: int,
    max_duration_seconds: float,
    allow_lv2v: bool,
) -> tuple[PlannedCase, ...]:
    items = offers if isinstance(offers, list) else []
    planned: list[PlannedCase] = []
    running_total = 0
    for case in cases:
        missing = [name for name in case.requires if not values.get(name, "").strip()]
        if case.action == "lv2v" and not allow_lv2v:
            planned.append(PlannedCase(case, "unavailable", "LV2V execution is disabled"))
            continue
        if (
            case.action == "lv2v"
            and values.get("QUAL_LV2V_INPUT")
            and not Path(values["QUAL_LV2V_INPUT"]).expanduser().is_file()
        ):
            planned.append(PlannedCase(case, "unavailable", "LV2V input file is unavailable"))
            continue
        if missing:
            planned.append(
                PlannedCase(case, "unavailable", "missing configuration: " + ", ".join(missing))
            )
            continue
        if case.duration_seconds > max_duration_seconds:
            planned.append(PlannedCase(case, "over-budget", "duration exceeds suite limit"))
            continue
        if case.max_authorized_wei > max_case_wei:
            planned.append(PlannedCase(case, "over-budget", "case fee exceeds suite limit"))
            continue
        matches = (
            []
            if case.source == "controlled"
            else [offer for offer in items if _offer_matches(case, offer)]
        )
        if case.source == "live" and not matches:
            planned.append(PlannedCase(case, "unavailable", "no bounded live offer"))
            continue
        if len(matches) > 1:
            matches.sort(
                key=lambda offer: (
                    Fraction(
                        int(str(offer["price"]["numerator"])),
                        int(str(offer["price"]["denominator"])),
                    ),
                    str(offer.get("id", "")),
                )
            )
            first_price = matches[0]["price"]
            second_price = matches[1]["price"]
            if int(str(first_price["numerator"])) * int(str(second_price["denominator"])) == int(
                str(second_price["numerator"])
            ) * int(str(first_price["denominator"])):
                planned.append(
                    PlannedCase(case, "ambiguous", "multiple equally priced bounded live offers")
                )
                continue
            matches = matches[:1]
        if running_total + case.max_authorized_wei > max_total_wei:
            planned.append(PlannedCase(case, "over-budget", "aggregate fee limit exceeded"))
            continue
        running_total += case.max_authorized_wei
        planned.append(
            PlannedCase(
                case,
                "runnable",
                "controlled deterministic scenario"
                if case.source == "controlled"
                else "bounded live offer",
                matches[0] if matches else None,
            )
        )
    return tuple(planned)


def plan_document(planned: tuple[PlannedCase, ...], max_total_wei: int) -> dict[str, object]:
    authorized = sum(item.case.max_authorized_wei for item in planned if item.status == "runnable")
    return {
        "schema": "livepeer.clearinghouse.qualification-plan.v1",
        "maximum_total_authorized_wei": str(max_total_wei),
        "planned_maximum_authorized_wei": str(authorized),
        "cases": [
            {
                "id": item.case.id,
                "source": item.case.source,
                "action": item.case.action,
                "status": item.status,
                "reason": item.reason,
                "maximum_authorized_wei": str(item.case.max_authorized_wei),
                "offer_id": item.offer.get("id") if item.offer else None,
            }
            for item in planned
        ],
    }


def _positive(values: dict[str, str], name: str, default: str) -> int:
    try:
        value = int(values.get(name, "") or default)
    except ValueError as error:
        raise QualificationError(f"{name} must be an integer") from error
    if value <= 0:
        raise QualificationError(f"{name} must be positive")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("qualification.env"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cases", help="comma-separated explicit case selection")
    arguments = parser.parse_args()
    try:
        values = load_env(arguments.env_file)
        cases = choose_cases(load_cases(arguments.manifest), arguments.cases)
        from scripts.qualify_gateway import Settings, request_json

        settings = Settings.from_values(values)
        response, _headers = request_json(settings, "/v1/offers")
        offers = response.get("items", []) if isinstance(response, dict) else []
        maximum_total = _positive(values, "QUALIFICATION_MAX_TOTAL_FEE_WEI", "1000000000000")
        planned = plan_cases(
            cases,
            offers,
            values,
            max_total_wei=maximum_total,
            max_case_wei=_positive(values, "QUALIFICATION_MAX_CASE_FEE_WEI", "100000000000"),
            max_duration_seconds=float(
                values.get("QUALIFICATION_MAX_DURATION_SECONDS", "30") or "30"
            ),
            allow_lv2v=values.get("QUALIFICATION_ALLOW_LV2V", "false").lower()
            in {"1", "true", "yes", "on"},
        )
        document = plan_document(planned, maximum_total)
        evidence_root = Path(values.get("QUAL_EVIDENCE_ROOT", "tmp/qualification")).expanduser()
        evidence_root.mkdir(parents=True, exist_ok=True)
        document["generated_at"] = datetime.now(UTC).isoformat()
        (evidence_root / "plan-latest.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(document, indent=2, sort_keys=True))
        return 0
    except (QualificationError, ValueError) as error:
        print(f"qualification planning failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
