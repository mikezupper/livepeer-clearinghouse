from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.qualification_cases import (
    choose_cases,
    load_cases,
    plan_cases,
    plan_document,
)
from scripts.qualify_gateway import QualificationError


def manifest(tmp_path: Path, cases: list[dict[str, object]]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps({"schema": "livepeer.clearinghouse.qualification-cases.v1", "cases": cases}),
        encoding="utf-8",
    )
    return path


def case(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "short",
        "source": "live",
        "action": "reserve",
        "capability": "app",
        "model": None,
        "orchestrators": ["https://orch.example"],
        "price": {
            "currency": "wei",
            "unit": "seconds",
            "max_numerator": 5,
            "max_denominator": 2,
        },
        "max_quantity": 10,
        "max_authorized_wei": 25,
        "duration_seconds": 2,
        "minimum_events": 1,
    }
    value.update(overrides)
    return value


def offer(*, numerator: str = "5", denominator: str = "2") -> dict[str, object]:
    return {
        "id": "offer-1",
        "capability": "app",
        "model": None,
        "constraints": {"orchestrator_url": "https://orch.example"},
        "price": {
            "currency": "wei",
            "quantity_unit": "seconds",
            "numerator": numerator,
            "denominator": denominator,
        },
    }


def test_manifest_loads_exact_bounds_and_explicit_selection(tmp_path: Path) -> None:
    cases = load_cases(
        manifest(
            tmp_path,
            [
                case(),
                case(
                    id="controlled",
                    source="controlled",
                    action="scenario",
                    scenario="replay",
                    orchestrators=[],
                ),
            ],
        )
    )

    assert cases[0].max_authorized_wei == 25
    assert choose_cases(cases, "controlled,short") == (cases[1], cases[0])
    with pytest.raises(QualificationError, match="unknown"):
        choose_cases(cases, "missing")


@pytest.mark.parametrize(
    "document",
    (
        {},
        {"schema": "wrong", "cases": []},
        {"schema": "livepeer.clearinghouse.qualification-cases.v1", "cases": []},
    ),
)
def test_manifest_rejects_invalid_document(tmp_path: Path, document: object) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(QualificationError):
        load_cases(path)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"id": ""}, "ids"),
        ({"source": "controlled", "action": "reserve"}, "pair"),
        ({"price": {}}, "max_numerator"),
        ({"max_authorized_wei": 24}, "below"),
        ({"duration_seconds": -1}, "duration"),
        ({"orchestrators": "bad"}, "orchestrators"),
        ({"requires": [1]}, "requirements"),
    ),
)
def test_manifest_rejects_unsafe_cases(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(QualificationError, match=message):
        load_cases(manifest(tmp_path, [case(**overrides)]))


def test_planner_classifies_live_controlled_ambiguous_and_limits(tmp_path: Path) -> None:
    cases = load_cases(
        manifest(
            tmp_path,
            [
                case(),
                case(
                    id="controlled",
                    source="controlled",
                    action="scenario",
                    scenario="replay",
                    orchestrators=[],
                ),
                case(id="missing", capability="other"),
                case(id="requires", requires=["INPUT"]),
                case(id="duration", duration_seconds=31),
                case(id="large", max_authorized_wei=101, max_quantity=1),
            ],
        )
    )
    planned = plan_cases(
        cases,
        [offer()],
        {},
        max_total_wei=1000,
        max_case_wei=100,
        max_duration_seconds=30,
        allow_lv2v=False,
    )

    assert [value.status for value in planned] == [
        "runnable",
        "runnable",
        "unavailable",
        "unavailable",
        "over-budget",
        "over-budget",
    ]
    assert plan_document(planned, 1000)["planned_maximum_authorized_wei"] == "50"


def test_planner_rejects_ambiguous_aggregate_and_disabled_lv2v(tmp_path: Path) -> None:
    cases = load_cases(
        manifest(
            tmp_path,
            [
                case(id="ambiguous", orchestrators=[]),
                case(id="aggregate"),
                case(id="lv2v", action="lv2v"),
            ],
        )
    )
    planned = plan_cases(
        cases,
        [
            offer(),
            {
                **offer(),
                "id": "offer-2",
                "constraints": {"orchestrator_url": "https://other.example"},
            },
        ],
        {},
        max_total_wei=20,
        max_case_wei=100,
        max_duration_seconds=30,
        allow_lv2v=False,
    )
    assert [value.status for value in planned] == [
        "ambiguous",
        "over-budget",
        "unavailable",
    ]
