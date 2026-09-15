from __future__ import annotations

from pathlib import Path

import pytest
from scripts.qualify_gateway import (
    QualificationError,
    Settings,
    load_env,
    select_offer,
    validate_accounting,
)


def settings(tmp_path: Path, **overrides: str) -> Settings:
    (tmp_path / "pyproject.toml").touch()
    values = {
        "QUAL_CLEARINGHOUSE_URL": "http://localhost:8080",
        "QUAL_GATEWAY_ROOT": str(tmp_path),
        "QUAL_API_CREDENTIAL": "och_live_test",
        "QUAL_CAPABILITY": "livepeer-example/flux-klein",
        "QUAL_ORCHESTRATOR": "https://orch.test:8936",
        "QUAL_EXPECT_CURRENCY": "wei",
        "QUAL_EXPECT_UNIT": "seconds",
        "QUAL_MAX_PRICE_NUMERATOR": "2000",
    }
    values.update(overrides)
    return Settings.from_values(values)


def offer(
    *, numerator: str = "1000", orchestrator: str = "https://orch.test:8936"
) -> dict[str, object]:
    return {
        "id": "price_1",
        "capability": "livepeer-example/flux-klein",
        "model": None,
        "constraints": {"orchestrator_url": orchestrator},
        "price": {
            "numerator": numerator,
            "denominator": "1",
            "currency": "wei",
            "quantity_unit": "seconds",
        },
    }


def test_load_env_preserves_process_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "qualification.env"
    path.write_text("QUAL_EXECUTE=false\nQUAL_CAPABILITY='from file'\n", encoding="utf-8")
    monkeypatch.setenv("QUAL_EXECUTE", "true")

    values = load_env(path)

    assert values["QUAL_EXECUTE"] == "true"
    assert values["QUAL_CAPABILITY"] == "from file"


def test_select_offer_applies_orchestrator_and_exact_price_ceiling(tmp_path: Path) -> None:
    selected = select_offer(
        [
            offer(orchestrator="https://other.test:8936"),
            offer(numerator="2001"),
            offer(numerator="1999"),
        ],
        settings(tmp_path),
    )

    assert selected["price"]["numerator"] == "1999"


def test_select_offer_rejects_ambiguous_selection(tmp_path: Path) -> None:
    with pytest.raises(QualificationError, match="ambiguous"):
        select_offer(
            [offer(orchestrator="https://one.test"), offer(orchestrator="https://two.test")],
            settings(tmp_path, QUAL_ORCHESTRATOR=""),
        )


def test_settings_require_api_credential_prefix(tmp_path: Path) -> None:
    with pytest.raises(QualificationError, match="och_live_"):
        settings(tmp_path, QUAL_API_CREDENTIAL="not-a-clearinghouse-key")


def test_accounting_rejects_nonzero_fee_with_zero_quantity() -> None:
    event = {"status": "matched", "quantity": "0", "computed_fee": "10"}
    cost = {"quoted_fee": "0", "computed_fee": "10", "event_count": 1}

    with pytest.raises(QualificationError, match="zero measured usage"):
        validate_accounting(event, cost)


def test_accounting_accepts_reconciled_usage() -> None:
    event = {"status": "matched", "quantity": "10000000000", "computed_fee": "10"}
    cost = {"quoted_fee": "11", "computed_fee": "10", "event_count": 1}

    validate_accounting(event, cost)


def test_accounting_validates_enforced_spend_reconciliation() -> None:
    event = {"status": "matched", "quantity": "1", "computed_fee": "3"}
    cost = {
        "quoted_fee": "3",
        "computed_fee": "3",
        "event_count": 1,
        "spend_ceiling": "7",
        "authorized_fee": "6",
        "pending_fee": "3",
        "remaining_spend": "1",
    }
    validate_accounting(event, cost, 7)
    cost["remaining_spend"] = "2"
    with pytest.raises(QualificationError, match="remaining spend"):
        validate_accounting(event, cost, 7)
