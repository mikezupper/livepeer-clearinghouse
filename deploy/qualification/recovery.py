"""Qualify durable recovery and metering failures in disposable Compose."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError
from urllib.request import urlopen

from deploy.qualification.run import (
    HEALTHY_SERVICES,
    ROOT,
    STARTED_SERVICES,
    Harness,
    QualificationFailure,
    parse_json_stream,
    redact,
    write_private,
)

PROBE_PATH: Final = "/app/deploy/qualification/recovery_probe.py"
RUN_ID_PATTERN: Final = re.compile(r"^[a-z0-9]{12}$")
PINNED_SIGNER_REVISION: Final = "e8dcf7a3"


def last_json_object(raw: str) -> dict[str, Any]:
    """Read one bounded JSON result without depending on entrypoint chatter."""
    if len(raw) > 65_536:
        raise QualificationFailure("recovery probe output exceeded the bounded limit")
    for line in reversed(raw.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise QualificationFailure("recovery probe did not return a JSON object")


def service_state(rows: list[dict[str, Any]], service: str) -> tuple[str, str]:
    """Return normalized state and health for exactly one Compose service."""
    matches = [row for row in rows if str(row.get("Service", "")) == service]
    if len(matches) != 1:
        raise QualificationFailure(f"Compose state for {service} was not unique")
    return str(matches[0].get("State", "")).lower(), str(matches[0].get("Health", "")).lower()


def signer_identity(raw: str) -> dict[str, str]:
    """Extract only allowlisted, non-environment identity from upstream output."""
    fields: dict[str, str] = {}
    prefixes = {
        "Livepeer Node Version: ": "version",
        "Golang runtime version: ": "go_runtime",
        "Architecture: ": "architecture",
        "Operating system: ": "operating_system",
    }
    for line in raw.splitlines():
        for prefix, key in prefixes.items():
            if line.startswith(prefix):
                fields[key] = line.removeprefix(prefix).strip()
    if (
        PINNED_SIGNER_REVISION not in fields.get("version", "")
        or fields.get("architecture") != "amd64"
        or fields.get("operating_system") != "linux"
        or not fields.get("go_runtime", "").startswith("gc go")
    ):
        raise QualificationFailure("pinned go-livepeer identity was missing or unexpected")
    return fields


class RecoveryQualification(Harness):
    """Add reversible fault exercises to the clean-volume base harness."""

    def __init__(self, evidence_path: Path) -> None:
        super().__init__(evidence_path)
        self.run_id = secrets.token_hex(6)
        if not RUN_ID_PATTERN.fullmatch(self.run_id):
            raise QualificationFailure("generated recovery run ID is unsafe")

    def prepare(self) -> None:
        super().prepare()
        if self.temp is None or self.env_file is None:
            raise QualificationFailure("qualification environment was not initialized")
        directory = Path(self.temp.name)
        password = secrets.token_urlsafe(32)
        address = "1" * 40
        keystore = json.dumps(
            {
                "address": address,
                "crypto": {
                    "cipher": "aes-128-ctr",
                    "cipherparams": {"iv": secrets.token_hex(16)},
                    "ciphertext": secrets.token_hex(32),
                    "kdf": "scrypt",
                    "kdfparams": {
                        "dklen": 32,
                        "n": 2,
                        "p": 1,
                        "r": 1,
                        "salt": secrets.token_hex(32),
                    },
                    "mac": secrets.token_hex(32),
                },
                "id": "00000000-0000-4000-8000-000000000000",
                "version": 3,
            },
            separators=(",", ":"),
        )
        key_path = directory / "signer-keystore.json"
        password_path = directory / "signer-password"
        write_private(key_path, keystore)
        write_private(password_path, password)
        # The base harness randomizes every secret file. Local Redpanda has no
        # SASL user, so the optional paired password must remain empty.
        (directory / "kafka-password").write_text("", encoding="utf-8")
        with self.env_file.open("a", encoding="utf-8") as handle:
            handle.write(f"SIGNER_KEYSTORE_HOST_FILE={key_path}\n")
            handle.write(f"SIGNER_PASSWORD_HOST_FILE={password_path}\n")
            handle.write("CLEARINGHOUSE_SIGNER_GLOBAL_EXPOSURE_CAP=1000\n")
        self.secrets = (*self.secrets, password, keystore)

    def probe_command(self, command: str) -> dict[str, Any]:
        result = self.compose(
            "run",
            "--rm",
            "--no-deps",
            "consumer",
            "python",
            PROBE_PATH,
            command,
            "--run-id",
            self.run_id,
            check=False,
        )
        if result.returncode != 0:
            output = redact(result.stdout + result.stderr, self.secrets)
            raise QualificationFailure(f"recovery probe {command} failed: {output}")
        return last_json_object(result.stdout)

    def state(self, service: str) -> tuple[str, str]:
        rows = parse_json_stream(self.compose("ps", "-a", "--format", "json").stdout)
        return service_state(rows, service)

    def restart(self, service: str) -> dict[str, str]:
        self.compose("stop", "--timeout", "10", service)
        stopped_state, _stopped_health = self.state(service)
        if stopped_state not in {"exited", "stopped"}:
            raise QualificationFailure(f"fault injection did not stop {service}")
        self.compose("up", "-d", "--wait", "--wait-timeout", "120", service)
        restored_state, restored_health = self.state(service)
        if restored_state != "running" or restored_health != "healthy":
            raise QualificationFailure(f"fault restoration did not recover {service}")
        return {"fault": "stopped", "restoration": "healthy"}

    def signer_boundary(self) -> dict[str, object]:
        common = (
            "-e",
            "SIGNER_MODE=evaluation",
            "-e",
            "SIGNER_NETWORK=custom",
            "-e",
            "SIGNER_CHAIN_ID=1",
            "-e",
            "SIGNER_CONTROLLER=0x" + "2" * 40,
            "-e",
            "SIGNER_ETH_ADDR=0x" + "1" * 40,
            "-e",
            "ETH_RPC_URL=http://rpc:8545",
            "-e",
            "REMOTE_SIGNER_WEBHOOK_URL=http://api:8000/v1/compat/go-livepeer/authorize",
            "-e",
            "KAFKA_BROKERS=redpanda:9092",
            "-e",
            "KAFKA_GATEWAY_TOPIC=livepeer-gateway-events",
        )
        for _attempt in range(2):
            result = self.compose(
                "run",
                "--rm",
                "--no-deps",
                *common,
                "remote-signer",
                "--validate-only",
                check=False,
            )
            if result.returncode != 0 or "signer configuration valid" not in result.stdout:
                output = redact(result.stdout + result.stderr, self.secrets)
                raise QualificationFailure(f"remote signer wrapper validation failed: {output}")
        version = self.compose(
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "/usr/local/bin/livepeer",
            "remote-signer",
            "-version",
            check=False,
        )
        if version.returncode != 0:
            output = redact(version.stdout + version.stderr, self.secrets)
            raise QualificationFailure(f"pinned go-livepeer binary invocation failed: {output}")
        identity = signer_identity(version.stdout + version.stderr)
        protocol_statuses: dict[str, int] = {}
        for path in (
            "/generate-live-payment",
            "/sign-orchestrator-info",
            "/discover-orchestrators",
        ):
            try:
                with urlopen(f"http://127.0.0.1:{self.edge_port}{path}", timeout=5) as response:  # noqa: S310
                    status = response.status
            except HTTPError as error:
                status = error.code
                error.close()
            if status != 502:
                raise QualificationFailure("unfunded signer protocol route did not fail closed")
            protocol_statuses[path] = status
        return {
            "image": "pinned_unmodified_upstream",
            "binary": identity,
            "wrapper_validation_runs": 2,
            "protocol_routes": protocol_statuses,
            "funded_signing": "operator_only_not_exercised",
            "runtime_qualified": False,
        }

    def restore_all(self) -> bool:
        result = self.compose(
            "up", "-d", "--wait", "--wait-timeout", "180", *STARTED_SERVICES, check=False
        )
        if result.returncode != 0:
            return False
        rows = parse_json_stream(self.compose("ps", "-a", "--format", "json").stdout)
        for service in HEALTHY_SERVICES:
            if service_state(rows, service) != ("running", "healthy"):
                return False
        return True

    def run(self) -> int:
        evidence: dict[str, object] = {
            "schema_version": 1,
            "status": "failed",
            "scope": "disposable_compose_recovery",
            "project": self.project,
            "funded_signing": "operator_only_not_exercised",
        }
        restored = False
        try:
            self.prepare()
            self.compose("build", "api", "remote-signer")
            self.started = True
            self.compose("up", "-d", "--wait", "--wait-timeout", "180", *STARTED_SERVICES)
            evidence["initial_metering"] = self.probe_command("seed")
            evidence["restarts"] = {
                "postgres": self.restart("postgres"),
                "redpanda": self.restart("redpanda"),
                "consumer": self.restart("consumer"),
            }
            evidence["durable_after_restarts"] = self.probe_command("verify")
            evidence["transport_gap"] = self.probe_command("gap")
            evidence["signer_boundary"] = self.signer_boundary()
            restored = self.restore_all()
            evidence["faults_restored_before_cleanup"] = restored
            if not restored:
                raise QualificationFailure("not every injected service fault was restored")
            evidence["status"] = "passed"
        except (QualificationFailure, OSError, ValueError, json.JSONDecodeError) as error:
            evidence["failure"] = redact(str(error), self.secrets)
            if self.started:
                evidence["diagnostics"] = self.diagnostics()
        finally:
            if self.started and not restored:
                restored = self.restore_all()
                evidence["faults_restored_before_cleanup"] = restored
            cleaned = self.cleanup()
            evidence["cleanup"] = "verified" if cleaned else "failed"
            if not cleaned or not restored:
                evidence["status"] = "failed"
            self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
            self.evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
            if self.temp is not None:
                self.temp.cleanup()
        print(json.dumps({"status": evidence["status"], "evidence": str(self.evidence_path)}))
        return 0 if evidence["status"] == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=ROOT / "tmp/qualification/recovery.json",
        help="sanitized JSON evidence path",
    )
    arguments = parser.parse_args(argv)
    return RecoveryQualification(arguments.evidence.resolve()).run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
