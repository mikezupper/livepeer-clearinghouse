"""Build and start an isolated, secret-safe Compose qualification fixture."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Final
from urllib.request import Request, urlopen

ROOT: Final = Path(__file__).resolve().parents[2]
BASE_COMPOSE: Final = ROOT / "compose.yaml"
OVERLAY_COMPOSE: Final = ROOT / "deploy/qualification.compose.yaml"
BUILT_SERVICES: Final = (
    "api",
    "remote-signer",
    "admin-web",
    "user-web",
    "edge",
    "ops-database",
)
STARTED_SERVICES: Final = (
    "fake-resend",
    "postgres",
    "redpanda",
    "redpanda-init",
    "migrate",
    "bootstrap-operator",
    "api",
    "consumer",
    "admin-web",
    "user-web",
    "edge",
)
HEALTHY_SERVICES: Final = frozenset(
    {"fake-resend", "postgres", "redpanda", "api", "consumer", "admin-web", "user-web", "edge"}
)
COMPLETED_SERVICES: Final = frozenset({"redpanda-init", "migrate", "bootstrap-operator"})
MAX_DIAGNOSTIC_BYTES: Final = 65_536
PROJECT_PATTERN: Final = re.compile(r"^och-qualification-[a-z0-9-]{8,64}$")
DOCKER: Final = shutil.which("docker") or "/usr/bin/docker"


class QualificationFailure(RuntimeError):
    """Qualification failed without carrying secret-bearing command output."""


def reserve_loopback_port(excluded: frozenset[int] = frozenset()) -> int:
    """Ask the kernel for a currently free loopback port."""
    for _attempt in range(10):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port not in excluded:
            return port
    raise QualificationFailure("could not reserve distinct loopback ports")


def write_private(path: Path, value: str) -> None:
    """Create a fresh owner-only file, refusing accidental replacement."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
        handle.write("\n")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise QualificationFailure("runtime secret permissions are not mode 0600")


def redact(text: str, secret_values: tuple[str, ...]) -> str:
    """Bound and redact known and structurally recognizable credentials."""
    bounded = text[:MAX_DIAGNOSTIC_BYTES]
    for value in sorted((item for item in secret_values if item), key=len, reverse=True):
        bounded = bounded.replace(value, "[REDACTED]")
    patterns = (
        (r"AGE-SECRET-KEY-[A-Z0-9]+", "[REDACTED_AGE_IDENTITY]"),
        (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,\"']+", r"\1[REDACTED]"),
        (
            r"(?i)([\"\']?(?:code|otp|token|secret|password)[\"\']?\s*[:=]\s*)[^\s,}]+",
            r"\1[REDACTED]",
        ),
        (r"(?<![0-9])[0-9]{6}(?![0-9])", "[REDACTED_OTP]"),
    )
    for pattern, replacement in patterns:
        bounded = re.sub(pattern, replacement, bounded)
    return bounded[:MAX_DIAGNOSTIC_BYTES]


def parse_json_stream(raw: str) -> list[dict[str, Any]]:
    """Accept Compose's JSON array and historical one-object-per-line forms."""
    stripped = raw.strip()
    if not stripped:
        return []
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line in stripped.splitlines():
            item = json.loads(line)
            if not isinstance(item, dict):
                raise QualificationFailure(
                    "Compose returned an unexpected JSON status shape"
                ) from None
            rows.append(item)
        return rows
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    raise QualificationFailure("Compose returned an unexpected JSON status shape")


def validate_service_states(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Require every named fixture service to be healthy or successfully completed."""
    by_service = {str(row.get("Service", "")): row for row in rows}
    states: dict[str, str] = {}
    for service in STARTED_SERVICES:
        row = by_service.get(service)
        if row is None:
            raise QualificationFailure(f"required service {service} is absent")
        state = str(row.get("State", "")).lower()
        health = str(row.get("Health", "")).lower()
        exit_code = int(row.get("ExitCode", 0) or 0)
        if service in HEALTHY_SERVICES:
            if state != "running" or health != "healthy":
                raise QualificationFailure(f"required service {service} is not healthy")
            states[service] = "healthy"
        elif service in COMPLETED_SERVICES:
            if state not in {"exited", "stopped"} or exit_code != 0:
                raise QualificationFailure(f"one-shot service {service} did not complete")
            states[service] = "completed"
    return states


def validate_loopback_publishers(
    rows: list[dict[str, Any]], edge_port: int, resend_port: int
) -> dict[str, int]:
    """Require only the edge and protected mailbox to have real host bindings."""
    expected = {"edge": (8080, edge_port), "fake-resend": (8081, resend_port)}
    found: dict[str, int] = {}
    for row in rows:
        service = str(row.get("Service", ""))
        for publisher in row.get("Publishers", []) or []:
            if not isinstance(publisher, dict):
                raise QualificationFailure("Compose returned an invalid publisher shape")
            host = str(publisher.get("URL", ""))
            published = int(publisher.get("PublishedPort", 0) or 0)
            if not host or not published:
                continue
            if service not in expected:
                raise QualificationFailure(f"private service {service} has a host publisher")
            target, wanted = expected[service]
            if host != "127.0.0.1" or int(publisher.get("TargetPort", 0) or 0) != target:
                raise QualificationFailure(f"service {service} is not loopback-only")
            if published != wanted:
                raise QualificationFailure(f"service {service} has an unexpected host port")
            found[service] = published
    if set(found) != set(expected):
        raise QualificationFailure("required loopback publishers are missing")
    return found


class Harness:
    """Own one uniquely named Compose project and all of its temporary inputs."""

    def __init__(self, evidence_path: Path) -> None:
        suffix = secrets.token_hex(6)
        self.project = f"och-qualification-{suffix}"
        if not PROJECT_PATTERN.fullmatch(self.project):
            raise QualificationFailure("generated Compose project name is unsafe")
        self.evidence_path = evidence_path
        self.secrets: tuple[str, ...] = ()
        self.temp: tempfile.TemporaryDirectory[str] | None = None
        self.env_file: Path | None = None
        self.edge_port = reserve_loopback_port()
        self.resend_port = reserve_loopback_port(frozenset({self.edge_port}))
        self.started = False

    def compose(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if self.env_file is None:
            raise QualificationFailure("qualification environment was not initialized")
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(ROOT),
            "-f",
            str(BASE_COMPOSE),
            "-f",
            str(OVERLAY_COMPOSE),
            "-p",
            self.project,
            "--env-file",
            str(self.env_file),
            *arguments,
        ]
        result = subprocess.run(  # noqa: S603 -- fixed executable and validated arguments.
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise QualificationFailure(f"Compose command {arguments[0]!r} failed")
        return result

    def prepare(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="och-qualification-")
        directory = Path(self.temp.name)
        directory.chmod(0o700)
        names = (
            "postgres-password",
            "auth-pepper",
            "credential-pepper",
            "resend-api-key",
            "google-client-secret",
            "github-client-secret",
            "signer-webhook-secret",
            "signer-session-pepper",
            "operator-bootstrap-secret",
            "kafka-password",
            "qualification-mailbox-token",
        )
        values = {name: secrets.token_urlsafe(48) for name in names}
        values["resend-api-key"] = "re_" + secrets.token_urlsafe(40)
        values["google-client-secret"] = ""
        values["github-client-secret"] = ""
        values["operator-bootstrap-email"] = "operator@qualification.example.com"
        for name, value in values.items():
            write_private(directory / name, value)
        self.secrets = tuple(values.values())
        env = {
            "COMPOSE_PROJECT_NAME": self.project,
            "CLEARINGHOUSE_EDGE_PORT": str(self.edge_port),
            "QUALIFICATION_RESEND_PORT": str(self.resend_port),
            "POSTGRES_USER": "clearinghouse_qualification",
            "POSTGRES_DB": "clearinghouse_qualification",
            "CLEARINGHOUSE_SECRET_OWNER_UID": str(os.getuid()),
            "CLEARINGHOUSE_ENVIRONMENT": "test",
            "CLEARINGHOUSE_LOG_LEVEL": "INFO",
            "CLEARINGHOUSE_AUTH_RESEND_FROM": "qualification@invalid.example",
            "CLEARINGHOUSE_AUTH_COOKIE_SECURE": "false",
            "CLEARINGHOUSE_AUTH_ALLOWED_ORIGINS": f"http://127.0.0.1:{self.edge_port}",
            "CLEARINGHOUSE_AUTH_SUCCESS_REDIRECT_URL": f"http://127.0.0.1:{self.edge_port}/",
            "CLEARINGHOUSE_AUTH_GOOGLE_ENABLED": "false",
            "CLEARINGHOUSE_AUTH_GITHUB_ENABLED": "false",
            "CLEARINGHOUSE_SIGNER_GLOBAL_EXPOSURE_CAP": "0",
            "CLEARINGHOUSE_SIGNER_ID": "signer_qualification0000",
            "CLEARINGHOUSE_SIGNER_URL": f"http://127.0.0.1:{self.edge_port}",
            "CLEARINGHOUSE_SIGNER_DISCOVERY_URL": f"http://127.0.0.1:{self.edge_port}",
            "CLEARINGHOUSE_KAFKA_BOOTSTRAP_SERVERS_INTERNAL": "redpanda:9092",
            "CLEARINGHOUSE_KAFKA_METERING_TOPIC": "livepeer-gateway-events",
            "CLEARINGHOUSE_KAFKA_METERING_GROUP_ID": "clearinghouse-qualification-v1",
            "CLEARINGHOUSE_KAFKA_METERING_CLIENT_ID": "clearinghouse-qualification",
            "CLEARINGHOUSE_KAFKA_RETENTION_MS": "604800000",
            "POSTGRES_PASSWORD_HOST_FILE": str(directory / "postgres-password"),
            "CLEARINGHOUSE_AUTH_PEPPER_HOST_FILE": str(directory / "auth-pepper"),
            "CLEARINGHOUSE_CREDENTIAL_PEPPER_HOST_FILE": str(directory / "credential-pepper"),
            "CLEARINGHOUSE_AUTH_RESEND_API_KEY_HOST_FILE": str(directory / "resend-api-key"),
            "CLEARINGHOUSE_AUTH_GOOGLE_CLIENT_SECRET_HOST_FILE": str(
                directory / "google-client-secret"
            ),
            "CLEARINGHOUSE_AUTH_GITHUB_CLIENT_SECRET_HOST_FILE": str(
                directory / "github-client-secret"
            ),
            "CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET_HOST_FILE": str(
                directory / "signer-webhook-secret"
            ),
            "CLEARINGHOUSE_SIGNER_SESSION_PEPPER_HOST_FILE": str(
                directory / "signer-session-pepper"
            ),
            "CLEARINGHOUSE_OPERATOR_BOOTSTRAP_EMAIL_HOST_FILE": str(
                directory / "operator-bootstrap-email"
            ),
            "CLEARINGHOUSE_OPERATOR_BOOTSTRAP_SECRET_HOST_FILE": str(
                directory / "operator-bootstrap-secret"
            ),
            "LP_KAFKAPASSWORD_HOST_FILE": str(directory / "kafka-password"),
            "QUALIFICATION_MAILBOX_TOKEN_HOST_FILE": str(directory / "qualification-mailbox-token"),
        }
        self.env_file = directory / "qualification.env"
        write_private(self.env_file, "\n".join(f"{key}={value}" for key, value in env.items()))

    def image_manifest(self) -> list[dict[str, object]]:
        model = json.loads(self.compose("--profile", "ops", "config", "--format", "json").stdout)
        manifest: list[dict[str, object]] = []
        for service in BUILT_SERVICES:
            configured = str(model["services"][service]["image"])
            result = subprocess.run(  # noqa: S603 -- fixed Docker inspection command.
                [DOCKER, "image", "inspect", configured],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                raise QualificationFailure(f"built image for {service} is unavailable")
            image = json.loads(result.stdout)[0]
            manifest.append(
                {
                    "service": service,
                    "configured_image": configured,
                    "image_id": image["Id"],
                    "repo_digests": sorted(image.get("RepoDigests") or []),
                    "runtime": "built_only_unqualified"
                    if service == "remote-signer"
                    else "fixture",
                }
            )
        return manifest

    def probe(self, url: str, *, token: str | None = None) -> None:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        request = Request(url, headers=headers)  # noqa: S310 -- loopback fixture URL.
        with urlopen(request, timeout=5) as response:  # noqa: S310 -- loopback fixture URL.
            body = response.read(4097)
            if response.status != 200 or not body or len(body) > 4096:
                raise QualificationFailure("HTTP readiness response was invalid")

    def diagnostics(self) -> dict[str, object]:
        diagnostics: dict[str, object] = {}
        for name, args in (
            ("compose_ps", ("ps", "-a", "--format", "json")),
            ("compose_logs", ("logs", "--no-color", "--tail", "100")),
        ):
            result = self.compose(*args, check=False)
            diagnostics[name] = redact(result.stdout + result.stderr, tuple(self.secrets))
        return diagnostics

    def cleanup(self) -> bool:
        if self.env_file is None:
            return True
        result = self.compose(
            "down", "--volumes", "--remove-orphans", "--timeout", "10", check=False
        )
        self.started = False
        containers = subprocess.run(  # noqa: S603 -- fixed Docker query and validated label.
            [
                DOCKER,
                "container",
                "ls",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        volumes = subprocess.run(  # noqa: S603 -- fixed Docker query and validated label.
            [
                DOCKER,
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        networks = subprocess.run(  # noqa: S603 -- fixed Docker query and validated label.
            [
                DOCKER,
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        return (
            result.returncode
            == containers.returncode
            == volumes.returncode
            == networks.returncode
            == 0
            and not containers.stdout.strip()
            and not volumes.stdout.strip()
            and not networks.stdout.strip()
        )

    def run(self) -> int:
        evidence: dict[str, object] = {
            "schema_version": 1,
            "status": "failed",
            "project": self.project,
            "runtime_scope": {
                "started": list(STARTED_SERVICES),
                "built_only": ["remote-signer", "ops-database"],
                "signer_runtime_qualified": False,
                "signer_runtime_note": (
                    "funded signer startup and protocol are owned by och-u8d.19.3"
                ),
            },
        }
        try:
            self.prepare()
            self.compose("--profile", "ops", "build", *BUILT_SERVICES)
            evidence["images"] = self.image_manifest()
            self.started = True
            self.compose("up", "-d", "--wait", "--wait-timeout", "180", *STARTED_SERVICES)
            rows = parse_json_stream(self.compose("ps", "-a", "--format", "json").stdout)
            evidence["services"] = validate_service_states(rows)
            evidence["loopback_publishers"] = validate_loopback_publishers(
                rows, self.edge_port, self.resend_port
            )
            self.probe(f"http://127.0.0.1:{self.edge_port}/health/ready")
            self.probe(f"http://127.0.0.1:{self.resend_port}/health/ready")
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
            if not re.fullmatch(r"[0-9A-Za-z_]{1,80}", revision):
                raise QualificationFailure("database migration revision is missing or invalid")
            evidence["migration_revision"] = revision
            evidence["public_endpoints"] = {"edge": f"http://127.0.0.1:{self.edge_port}"}
            evidence["mailbox_endpoint"] = "loopback_token_protected"
            evidence["status"] = "passed"
        except (QualificationFailure, OSError, ValueError, json.JSONDecodeError) as error:
            evidence["failure"] = redact(str(error), tuple(self.secrets))
            if self.started:
                evidence["diagnostics"] = self.diagnostics()
        finally:
            cleaned = self.cleanup()
            evidence["cleanup"] = "verified" if cleaned else "failed"
            if not cleaned:
                evidence["status"] = "failed"
                evidence["failure"] = "scoped Compose cleanup could not be verified"
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
        default=ROOT / "tmp/qualification/harness.json",
        help="sanitized JSON evidence path",
    )
    args = parser.parse_args(argv)
    if shutil.which("docker") is None:
        print("qualification requires Docker", file=sys.stderr)
        return 2
    return Harness(args.evidence.resolve()).run()


if __name__ == "__main__":
    raise SystemExit(main())
