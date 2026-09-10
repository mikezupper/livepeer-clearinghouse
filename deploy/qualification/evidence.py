"""Emit bounded release qualification evidence for the reference distribution."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast
from urllib.request import urlopen

import yaml

from deploy.qualification.run import (
    BUILT_SERVICES,
    ROOT,
    STARTED_SERVICES,
    Harness,
    QualificationFailure,
    parse_json_stream,
    redact,
    validate_service_states,
    write_private,
)

SCHEMA_VERSION: Final = 1
PROBE_COUNT: Final = 32
P95_LIMIT_MS: Final = 1_000
MIN_THROUGHPUT_RPS: Final = 2.0
MAX_PROBE_ERRORS: Final = 0
MAX_COMMAND_OUTPUT_BYTES: Final = 65_536
IDENTITY_SERVICES: Final = (
    "postgres",
    "redpanda",
    "api",
    "consumer",
    "remote-signer",
    "admin-web",
    "user-web",
    "edge",
    "ops-database",
)


def sha256_file(path: Path) -> str:
    """Hash a repository contract without reading it into an evidence payload."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile_nearest_rank(values: list[float], percentile: float) -> float:
    """Return a deterministic nearest-rank percentile for a non-empty sample."""
    if not values:
        raise ValueError("a percentile requires at least one sample")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def bounded_command_evidence(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """Record command outcome without copying potentially sensitive diagnostics."""
    output = (result.stdout + result.stderr)[:MAX_COMMAND_OUTPUT_BYTES]
    return {
        "exit_code": result.returncode,
        "output_bytes": len(output.encode("utf-8")),
        "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
        "truncated": len(result.stdout + result.stderr) > MAX_COMMAND_OUTPUT_BYTES,
    }


def markdown_report(evidence: dict[str, Any]) -> str:
    """Render the allow-listed, secret-free operator summary."""
    capacity = evidence["capacity"]
    versions = evidence["compatibility"]["components"]
    recovery = evidence["durability"]
    lines = [
        "# Open Clearinghouse qualification evidence",
        "",
        f"Status: **{evidence['status']}**",
        "",
        "This is a bounded single-node reference-Compose gate. It is not a production "
        "capacity or sizing claim.",
        "",
        "## Capacity gate",
        "",
        f"- Workload: {capacity['workload']}",
        f"- Requests / concurrency: {capacity['requests']} / {capacity['concurrency']}",
        f"- p95: {capacity['results']['p95_ms']} ms (limit {capacity['thresholds']['p95_ms']} ms)",
        f"- Throughput: {capacity['results']['throughput_rps']} req/s "
        f"(minimum {capacity['thresholds']['minimum_throughput_rps']} req/s)",
        f"- Errors: {capacity['results']['errors']} "
        f"(maximum {capacity['thresholds']['max_errors']})",
        "",
        "## Compatibility",
        "",
    ]
    for name in sorted(versions):
        lines.append(f"- {name}: `{versions[name]}`")
    lines.extend(
        [
            "",
            "## Durability",
            "",
            f"- Migration upgrade/rollback/upgrade: {recovery['migration_cycle']['status']}",
            f"- Populated downgrade guards: {recovery['migration_cycle']['populated_guards']}",
            f"- Encrypted isolated restore: {recovery['backup_restore']['status']}",
            f"- Recovery fault qualification: {recovery['restart_and_metering']['status']}",
            "",
            "## Boundaries",
            "",
            "- Funded go-livepeer signing is operator-only and was not exercised.",
            "- The results qualify this exact manifest and reference topology only.",
            "- Secrets, OTPs, cookies, bearer tokens, and raw logs are excluded.",
            "",
        ]
    )
    return "\n".join(lines)


class EvidenceHarness(Harness):
    """Run the compatibility, capacity, migration, and restore fixture."""

    def generate_age_identity(self) -> None:
        if self.temp is None or self.env_file is None:
            raise QualificationFailure("qualification environment was not initialized")
        result = subprocess.run(  # noqa: S603 -- fixed Docker image and entrypoint.
            [
                shutil.which("docker") or "/usr/bin/docker",
                "run",
                "--rm",
                "--entrypoint",
                "age-keygen",
                "livepeer/clearinghouse-ops:local",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        recipient_match = re.search(r"Public key:\s*(age1[0-9a-z]+)", result.stderr)
        identity = result.stdout.strip()
        contains_secret = any(line.startswith("AGE-SECRET-KEY-") for line in identity.splitlines())
        if result.returncode or not recipient_match or not contains_secret:
            raise QualificationFailure("could not create the disposable backup identity")
        identity_path = Path(self.temp.name) / "age-identity"
        write_private(identity_path, identity)
        self.secrets = (*self.secrets, identity)
        additions = {
            "CLEARINGHOUSE_BACKUP_AGE_IDENTITY_HOST_FILE": str(identity_path),
            "CLEARINGHOUSE_BACKUP_AGE_RECIPIENT": recipient_match.group(1),
            "CLEARINGHOUSE_BACKUP_VOLUME": f"{self.project}-encrypted-backups",
            "OPS_BACKUP_KEY_ID": "qualification-ephemeral-v1",
        }
        with self.env_file.open("a", encoding="utf-8") as target:
            for key, value in additions.items():
                target.write(f"{key}={value}\n")

    def capacity_gate(self) -> dict[str, object]:
        url = f"http://127.0.0.1:{self.edge_port}/health/ready"
        elapsed_ms: list[float] = []
        errors = 0
        started = time.perf_counter()
        for _request in range(PROBE_COUNT):
            request_started = time.perf_counter()
            try:
                with urlopen(url, timeout=5) as response:  # noqa: S310 -- loopback-only fixture.
                    body = response.read(4097)
                    if response.status != 200 or not body or len(body) > 4096:
                        errors += 1
            except OSError:
                errors += 1
            elapsed_ms.append((time.perf_counter() - request_started) * 1_000)
        wall_seconds = time.perf_counter() - started
        p95_ms = percentile_nearest_rank(elapsed_ms, 0.95)
        throughput = PROBE_COUNT / wall_seconds
        passed = (
            errors <= MAX_PROBE_ERRORS
            and p95_ms <= P95_LIMIT_MS
            and throughput >= MIN_THROUGHPUT_RPS
        )
        result = {
            "scope": "single_node_reference_compose",
            "workload": "sequential_edge_readiness_requests",
            "production_sizing_supported": False,
            "requests": PROBE_COUNT,
            "concurrency": 1,
            "thresholds": {
                "p95_ms": P95_LIMIT_MS,
                "minimum_throughput_rps": MIN_THROUGHPUT_RPS,
                "max_errors": MAX_PROBE_ERRORS,
            },
            "results": {
                "p50_ms": round(percentile_nearest_rank(elapsed_ms, 0.50), 3),
                "p95_ms": round(p95_ms, 3),
                "p99_ms": round(percentile_nearest_rank(elapsed_ms, 0.99), 3),
                "throughput_rps": round(throughput, 3),
                "errors": errors,
            },
            "status": "passed" if passed else "failed",
        }
        if not passed:
            raise QualificationFailure("bounded capacity gate missed an explicit threshold")
        return result

    def runtime(self, service: str, *command: str) -> str:
        result = self.compose("exec", "-T", service, *command, check=False)
        value = redact(result.stdout + result.stderr, self.secrets).strip()
        if result.returncode or not value or len(value) > 4096:
            raise QualificationFailure(f"could not read the {service} runtime version")
        return " ".join(value.split())

    def compatibility(self) -> dict[str, object]:
        package = json.loads((ROOT / "frontend/package.json").read_text())
        openapi_path = ROOT / "contracts/openapi.yaml"
        asyncapi_path = ROOT / "contracts/asyncapi.yaml"
        openapi_raw = yaml.safe_load(openapi_path.read_text())
        asyncapi_raw = yaml.safe_load(asyncapi_path.read_text())
        if not isinstance(openapi_raw, dict) or not isinstance(asyncapi_raw, dict):
            raise QualificationFailure("contract documents have an invalid shape")
        openapi = cast(dict[str, Any], openapi_raw)
        asyncapi = cast(dict[str, Any], asyncapi_raw)
        revision = self.compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-XAt",
            "-U",
            "clearinghouse_qualification",
            "-d",
            "clearinghouse_qualification",
            "-c",
            "select version_num from alembic_version",
        ).stdout.strip()
        components = {
            "python": self.runtime("api", "python", "--version"),
            "vite": package["devDependencies"]["vite"],
            "vitest": package["devDependencies"]["vitest"],
            "typescript": package["devDependencies"]["typescript"],
            "postgresql": self.runtime("postgres", "postgres", "--version"),
            "redpanda": self.runtime("redpanda", "rpk", "version"),
            "caddy": self.runtime("edge", "caddy", "version"),
        }
        if not re.fullmatch(r"[0-9A-Za-z_]{1,80}", revision):
            raise QualificationFailure("database migration revision is invalid")
        return {
            "components": components,
            "contracts": {
                "openapi_version": openapi["openapi"],
                "api_version": openapi["info"]["version"],
                "openapi_sha256": sha256_file(openapi_path),
                "asyncapi_version": asyncapi["asyncapi"],
                "event_api_version": asyncapi["info"]["version"],
                "asyncapi_sha256": sha256_file(asyncapi_path),
                "alembic_revision": revision,
            },
        }

    def exact_images(self) -> list[dict[str, object]]:
        model = json.loads(self.compose("--profile", "ops", "config", "--format", "json").stdout)
        images: list[dict[str, object]] = []
        docker = shutil.which("docker") or "/usr/bin/docker"
        for service in IDENTITY_SERVICES:
            configured = str(model["services"][service]["image"])
            result = subprocess.run(  # noqa: S603 -- fixed inspection command.
                [docker, "image", "inspect", configured],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise QualificationFailure(f"image identity for {service} is unavailable")
            inspected = json.loads(result.stdout)[0]
            images.append(
                {
                    "service": service,
                    "configured_image": configured,
                    "image_id": inspected["Id"],
                    "repo_digests": sorted(inspected.get("RepoDigests") or []),
                }
            )
        return images

    def migration_cycle(self) -> dict[str, object]:
        project = f"{self.project}-migrations"
        started = time.perf_counter()
        make = shutil.which("make") or "/usr/bin/make"
        result = subprocess.run(  # noqa: S603 -- fixed Make target and generated project.
            [make, "--no-print-directory", "test-migrations", f"TEST_PROJECT={project}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        command = bounded_command_evidence(result)
        command["duration_seconds"] = round(time.perf_counter() - started, 3)
        if result.returncode:
            raise QualificationFailure("isolated migration qualification failed")
        return {
            "status": "passed",
            "sequence": "fresh head -> base -> head",
            "populated_guards": "0005 -> 0006 -> 0007 -> 0008 with atomic downgrade refusals",
            "command_evidence": command,
        }

    def backup_restore(self) -> dict[str, object]:
        if self.env_file is None:
            raise QualificationFailure("qualification environment was not initialized")
        name = "qualification-backup.dump.age"
        retention = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        backup = self.compose(
            "--profile",
            "ops",
            "run",
            "--rm",
            "-e",
            "OPS_BACKUP_CONFIRM=backup",
            "-e",
            f"OPS_BACKUP_NAME={name}",
            "-e",
            f"OPS_BACKUP_RETENTION_UNTIL={retention}",
            "ops-database",
            "backup",
            check=False,
        )
        if backup.returncode:
            diagnostic = redact(backup.stdout + backup.stderr, self.secrets)
            raise QualificationFailure(f"encrypted backup failed: {diagnostic}")
        manifest = json.loads(backup.stdout.strip().splitlines()[-1])
        restore_project = f"{self.project}-restore"
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(ROOT),
            "-f",
            str(ROOT / "deploy/ops/restore.compose.yaml"),
            "-p",
            restore_project,
            "--env-file",
            str(self.env_file),
        ]
        try:
            up = subprocess.run(  # noqa: S603 -- fixed Docker command and scoped project.
                [*command, "up", "-d", "--wait", "restore-postgres"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )  # noqa: S603
            if up.returncode:
                raise QualificationFailure("isolated restore database did not start")
            restored = subprocess.run(  # noqa: S603 -- generated scoped project and fixed args.
                [
                    *command,
                    "run",
                    "--rm",
                    "-e",
                    "OPS_RESTORE_CONFIRM=isolated-restore",
                    "-e",
                    f"OPS_BACKUP_NAME={name}",
                    "ops-restore",
                    "restore-verify",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if restored.returncode:
                raise QualificationFailure("encrypted backup restore verification failed")
            result = json.loads(restored.stdout.strip().splitlines()[-1])
        finally:
            subprocess.run(  # noqa: S603 -- fixed Docker command and scoped project.
                [*command, "down", "--volumes", "--remove-orphans"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )  # noqa: S603
        return {
            "status": "passed",
            "encryption": "age",
            "artifact_sha256": manifest["sha256"],
            "revision": result["revision"],
            "restored_tables": result["tables"],
            "snapshot_invariants": "verified",
            "target": "new_ephemeral_postgresql_volume",
        }

    def cleanup(self) -> bool:
        """Remove the explicit backup volume in addition to Compose-owned resources."""
        super().cleanup()
        volume = f"{self.project}-encrypted-backups"
        docker = shutil.which("docker") or "/usr/bin/docker"
        inspected = subprocess.run(  # noqa: S603 -- exact generated volume name.
            [docker, "volume", "inspect", volume],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if inspected.returncode == 0:
            removed = subprocess.run(  # noqa: S603 -- exact generated volume name.
                [docker, "volume", "rm", "--force", volume],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if removed.returncode:
                return False
        return super().cleanup()


def run_child(module: str, evidence_path: Path) -> dict[str, Any]:
    """Run one existing qualification and require its sanitized manifest."""
    result = subprocess.run(  # noqa: S603 -- current interpreter and fixed local module.
        [sys.executable, "-m", module, "--evidence", str(evidence_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise QualificationFailure(f"{module.rsplit('.', 1)[-1]} qualification failed")
    parsed = json.loads(evidence_path.read_text())
    if not isinstance(parsed, dict):
        raise QualificationFailure(f"{module.rsplit('.', 1)[-1]} evidence has invalid shape")
    evidence = cast(dict[str, Any], parsed)
    if evidence.get("status") != "passed" or evidence.get("cleanup") != "verified":
        raise QualificationFailure(f"{module.rsplit('.', 1)[-1]} evidence is incomplete")
    return evidence


def run(output: Path, human_output: Path) -> int:
    output = output.resolve()
    human_output = human_output.resolve()
    child_dir = output.parent / "components"
    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "failed",
        "scope": "single_node_reference_compose_release_qualification",
        "production_sizing_supported": False,
    }
    harness = EvidenceHarness(output)
    try:
        journey = run_child("deploy.qualification.journey", child_dir / "journey.json")
        recovery = run_child("deploy.qualification.recovery", child_dir / "recovery.json")
        evidence["journey"] = journey["journey"]
        durability: dict[str, Any] = {
            "restart_and_metering": {
                "status": recovery["status"],
                "restarts": recovery["restarts"],
                "transport_gap": recovery["transport_gap"],
                "signer_boundary": recovery["signer_boundary"],
            }
        }
        evidence["durability"] = durability
        harness.prepare()
        harness.compose("--profile", "ops", "build", *BUILT_SERVICES)
        harness.compose("--profile", "ops", "build", "ops-database")
        harness.generate_age_identity()
        harness.started = True
        harness.compose("up", "-d", "--wait", "--wait-timeout", "180", *STARTED_SERVICES)
        states = validate_service_states(
            parse_json_stream(harness.compose("ps", "-a", "--format", "json").stdout)
        )
        evidence["services"] = states
        evidence["capacity"] = harness.capacity_gate()
        compatibility = harness.compatibility()
        signer_binary = recovery["signer_boundary"]["binary"]
        if not isinstance(signer_binary, dict) or not signer_binary.get("version"):
            raise QualificationFailure("go-livepeer binary identity is incomplete")
        components = compatibility.get("components")
        if not isinstance(components, dict):
            raise QualificationFailure("component compatibility identity is incomplete")
        cast(dict[str, Any], components)["go-livepeer"] = " ".join(
            str(signer_binary[key])
            for key in ("version", "go_runtime", "operating_system", "architecture")
        )
        evidence["compatibility"] = compatibility
        evidence["images"] = harness.exact_images()
        durability["migration_cycle"] = harness.migration_cycle()
        durability["backup_restore"] = harness.backup_restore()
        evidence["component_manifests"] = {
            "journey_sha256": sha256_file(child_dir / "journey.json"),
            "recovery_sha256": sha256_file(child_dir / "recovery.json"),
        }
        evidence["status"] = "passed"
    except (
        QualificationFailure,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as error:
        evidence["failure"] = redact(str(error), harness.secrets)
        if harness.started:
            evidence["diagnostics"] = harness.diagnostics()
    finally:
        cleaned = harness.cleanup()
        evidence["cleanup"] = "verified" if cleaned else "failed"
        if not cleaned:
            evidence["status"] = "failed"
            evidence.setdefault("failure", "scoped cleanup failed")
            evidence["cleanup_failure"] = "scoped cleanup failed"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        if evidence["status"] == "passed":
            human_output.parent.mkdir(parents=True, exist_ok=True)
            human_output.write_text(markdown_report(evidence))
        elif human_output.exists():
            human_output.unlink()
        if harness.temp is not None:
            harness.temp.cleanup()
    print(
        json.dumps(
            {"status": evidence["status"], "evidence": str(output), "report": str(human_output)}
        )
    )
    return 0 if evidence["status"] == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "tmp/qualification/release.json")
    parser.add_argument("--report", type=Path, default=ROOT / "tmp/qualification/release.md")
    args = parser.parse_args(argv)
    if shutil.which("docker") is None:
        print("qualification requires Docker", file=sys.stderr)
        return 2
    return run(args.evidence, args.report)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
