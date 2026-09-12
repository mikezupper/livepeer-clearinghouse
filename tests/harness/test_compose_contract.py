from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_default_distribution_has_exactly_four_services() -> None:
    assert set(COMPOSE["services"]) == {"redpanda", "core", "remote-signer", "edge"}
    assert "sqlite-data:/data" in COMPOSE["services"]["core"]["volumes"]


def test_signer_healthcheck_uses_libcap_ng_compatible_capability_names() -> None:
    command = COMPOSE["services"]["remote-signer"]["healthcheck"]["test"]
    assert not any(value.endswith("=-all") for value in command)
    for option in ("--inh-caps", "--ambient-caps", "--bounding-set"):
        value = next(item for item in command if item.startswith(f"{option}="))
        assert value == f"{option}=-chown,-dac_override,-setpcap,-setgid,-setuid"
