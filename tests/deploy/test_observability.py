from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*command: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed repository scripts and fixture arguments.
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class ObservabilityDeploymentTests(unittest.TestCase):
    def test_observability_image_is_exactly_pinned_and_private(self) -> None:
        result = run(
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "--profile",
            "observability",
            "config",
            "--format",
            "json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        model = json.loads(result.stdout)
        service = model["services"]["observability"]
        self.assertEqual(
            service["image"],
            "grafana/otel-lgtm:0.32.1@sha256:"
            "7fd8eaad6bb64897ad5f644c8e15ee67c3204c97168f4bdba122adbf8f60e3c4",
        )
        self.assertEqual(service["profiles"], ["observability"])
        self.assertNotIn("ports", service)
        self.assertTrue(model["networks"]["observability"]["internal"])
        self.assertEqual(set(service["networks"]), {"observability"})
        self.assertNotIn("/var/run/docker.sock", json.dumps(service))
        self.assertIn("healthcheck", service)
        self.assertIn("3000/api/health", " ".join(service["healthcheck"]["test"]))
        for backend in ("api", "consumer"):
            self.assertIn("observability", model["services"][backend]["networks"])
            self.assertEqual(
                model["services"][backend]["environment"][
                    "CLEARINGHOUSE_OTEL_EXPORTER_OTLP_ENDPOINT"
                ],
                "",
            )
        ops = model["services"].get("ops-control")
        self.assertIsNone(ops)

        ops_result = run(
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "--profile",
            "ops",
            "config",
            "--format",
            "json",
        )
        self.assertEqual(ops_result.returncode, 0, ops_result.stderr)
        ops_model = json.loads(ops_result.stdout)["services"]["ops-control"]
        self.assertEqual({item["source"] for item in ops_model["secrets"]}, {"postgres-password"})
        self.assertIn("CLEARINGHOUSE_OPERATIONS_ACTOR_ID", ops_model["environment"])
        self.assertNotIn("/var/run/docker.sock", json.dumps(ops_model))

    def test_age_is_built_only_from_official_checksummed_assets(self) -> None:
        source = (ROOT / "deploy/ops/Dockerfile").read_text()
        self.assertIn("age/releases/download/v1.3.2/age-v1.3.2-linux-amd64.tar.gz", source)
        self.assertIn(
            "sha256:cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10",
            source,
        )
        self.assertIn("age/releases/download/v1.3.2/age-v1.3.2-linux-arm64.tar.gz", source)
        self.assertIn(
            "sha256:6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4",
            source,
        )
        self.assertNotIn("FROM filippo", source.lower())

    def test_observability_probe_queries_application_telemetry_without_docker_access(self) -> None:
        source = (ROOT / "deploy/observability/qualify.sh").read_text()
        self.assertIn("http://api:8000/health/live", source)
        self.assertIn("http://127.0.0.1:9090/api/v1/label/__name__/values", source)
        self.assertIn("http://127.0.0.1:3200/api/search", source)
        self.assertIn("http://127.0.0.1:3100/loki/api/v1/query_range", source)
        self.assertIn("clearinghouse_http_requests", source)
        self.assertIn('"rootServiceName":"clearinghouse-api"', source)
        self.assertNotIn("docker", source.lower())

    def test_control_wrapper_preserves_json_cli_and_guards_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_python = Path(directory) / "python"
            fake_python.write_text("#!/bin/sh\nprintf '<%s>\\n' \"$@\"\n")
            fake_python.chmod(0o755)
            env = os.environ | {"PATH": f"{directory}:{os.environ['PATH']}"}
            check = run(
                "sh",
                "deploy/ops/control.sh",
                "reconcile",
                "--mode",
                "check",
                env=env | {"OPS_REASON": "scheduled check"},
            )
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(
                check.stdout.splitlines(),
                [
                    "<-m>",
                    "<clearinghouse.operations_worker>",
                    "<reconcile>",
                    "<--mode>",
                    "<check>",
                    "<--reason>",
                    "<scheduled check>",
                ],
            )
            refused = run("sh", "deploy/ops/control.sh", "reconcile", "--mode", "repair", env=env)
            self.assertEqual(refused.returncode, 3)
            self.assertEqual(json.loads(refused.stderr)["status"], "refused")
            allowed = run(
                "sh",
                "deploy/ops/control.sh",
                "retention",
                "--mode=apply",
                env=env
                | {
                    "OPS_MUTATION_CONFIRM": "retention",
                    "OPS_REASON": "approved cleanup",
                    "OPS_BATCH_SIZE": "25",
                    "OPS_CATEGORY": "auth_ephemeral",
                    "OPS_RETENTION_DAYS": "90",
                    "OPS_IDEMPOTENCY_KEY": "retention-test-key-0001",
                },
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertIn("<approved cleanup>", allowed.stdout)
            self.assertIn("<retention-test-key-0001>", allowed.stdout)
            marker = Path(directory) / "must-not-exist"
            injected = run(
                "sh",
                "deploy/ops/control.sh",
                "backup",
                env=env
                | {
                    "OPS_MUTATION_CONFIRM": "backup-record",
                    "OPS_BACKUP_ACTION": "created",
                    "OPS_BACKUP_ARTIFACT_ID": "safe.dump.age",
                    "OPS_BACKUP_LOCATION_SHA256": "a" * 64,
                    "OPS_BACKUP_CHECKSUM_SHA256": "b" * 64,
                    "OPS_BACKUP_KEY_ID": "key-1",
                    "OPS_BACKUP_HIGH_WATERMARK": "0/1E77200",
                    "OPS_BACKUP_RETENTION_UNTIL": "2027-01-01T00:00:00Z",
                    "OPS_REASON": f"safe; touch {marker}",
                    "OPS_IDEMPOTENCY_KEY": "backup-test-key-0001",
                },
            )
            self.assertEqual(injected.returncode, 0, injected.stderr)
            self.assertFalse(marker.exists())
            self.assertIn(f"<safe; touch {marker}>", injected.stdout)
            rotation = run(
                "sh",
                "deploy/ops/control.sh",
                "rotation",
                env=env
                | {
                    "OPS_MUTATION_CONFIRM": "rotation-record",
                    "OPS_ROTATION_ID": "rotation-0001",
                    "OPS_ROTATION_PURPOSE": "backup_encryption",
                    "OPS_ROTATION_ACTION": "started",
                    "OPS_ROTATION_KEY_ID": "key-2",
                    "OPS_ROTATION_PRIOR_KEY_ID": "key-1",
                    "OPS_REASON": "scheduled rotation",
                    "OPS_IDEMPOTENCY_KEY": "rotation-test-key-0001",
                },
            )
            self.assertEqual(rotation.returncode, 0, rotation.stderr)
            self.assertIn("<--prior-key-id>", rotation.stdout)

    def test_fault_guard_requires_confirmation_and_disposable_project(self) -> None:
        base = os.environ | {"OPS_FAULT_TARGET": "redpanda"}
        unconfirmed = run("sh", "deploy/ops/fault-guard.sh", env=base)
        self.assertEqual(unconfirmed.returncode, 3)
        unsafe = run(
            "sh",
            "deploy/ops/fault-guard.sh",
            env=base
            | {"OPS_FAULT_CONFIRM": "disposable-only", "COMPOSE_PROJECT_NAME": "production"},
        )
        self.assertEqual(unsafe.returncode, 3)
        safe = run(
            "sh",
            "deploy/ops/fault-guard.sh",
            env=base
            | {
                "OPS_FAULT_CONFIRM": "disposable-only",
                "COMPOSE_PROJECT_NAME": "och-ops-test-broker",
            },
        )
        self.assertEqual(safe.returncode, 0, safe.stderr)
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("select coalesce(max(next_offset),0) from metering_checkpoints", makefile)
        self.assertIn('project="och-ops-test-broker-$$(date +%s)-$$$$"', makefile)
        self.assertNotIn("FAULT_PROJECT", makefile)
        self.assertIn('test "$$after" -gt "$$before"', makefile)
        self.assertNotIn("session_replication_role=replica", makefile)
        self.assertIn("SMOKE_PUBLISH_ONLY=1", makefile)
        self.assertIn("run --rm --no-deps", makefile)
        self.assertIn('test "$$checkpoint_while_stopped" -eq "$$before"', makefile)
        self.assertIn("rpk group seek", makefile)
        self.assertIn("--to end --topics", makefile)
        self.assertIn("*ERROR*|*INVALID_*", makefile)
        self.assertIn('test "$$replayed" -gt "$$before"', makefile)
        self.assertIn('test "$$observations_replayed" -gt "$$observations_before"', makefile)
        self.assertIn("--force-recreate --wait consumer", makefile)
        self.assertIn("reconcile --mode check", makefile)
        self.assertIn("rpk topic describe", makefile)
        self.assertIn("-c'", makefile)

    def test_backup_and_restore_contracts_are_encrypted_guarded_and_isolated(self) -> None:
        source = (ROOT / "deploy/ops/database.sh").read_text()
        self.assertIn("default_transaction_isolation=serializable", source)
        self.assertIn("--compress=zstd:9", source)
        self.assertIn("age --recipient", source)
        self.assertIn("OPS_BACKUP_CONFIRM", source)
        self.assertIn("OPS_RESTORE_CONFIRM", source)
        self.assertIn("OPS_RESTORE_ISOLATED", source)
        self.assertIn("pg_restore --single-transaction --exit-on-error", source)
        self.assertIn("pg_export_snapshot", source)
        self.assertIn('--snapshot="$snapshot"', source)
        self.assertIn("snapshot=$candidate", source)
        self.assertIn('ln "$temporary" "$artifact"', source)
        self.assertIn("sync -f", source)
        self.assertIn("manifest.jsonl", source)
        self.assertIn("backup_not_completed", source)
        self.assertIn("backup_not_in_manifest", source)
        self.assertIn("metadata_sha256=", source)
        self.assertIn("manifest_sha256=", source)
        self.assertIn("invalid_backup_metadata", source)
        self.assertIn("invalid_backup_manifest", source)
        self.assertIn('ln "$completion_temporary" "$completion"', source)
        self.assertIn("sha256sum --check --status", source)
        self.assertIn("restore_target_not_empty", source)
        self.assertIn("restored_invariant_drift", source)
        restore = (ROOT / "deploy/ops/restore.compose.yaml").read_text()
        self.assertIn("external: true", restore)
        self.assertIn("internal: true", restore)
        self.assertIn("/var/lib/postgresql:uid=999,gid=999,mode=0700", restore)

    def test_json_field_parser_is_bounded_and_allowlisted(self) -> None:
        document = json.dumps({"artifact": "backup.dump.age", "unexpected": "value"})
        accepted = subprocess.run(  # noqa: S603, S607 -- fixed repository helper.
            ["uv", "run", "python", "deploy/ops/json_field.py", "artifact"],  # noqa: S607
            cwd=ROOT,
            input=document,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(accepted.stdout.strip(), "backup.dump.age")
        refused = subprocess.run(  # noqa: S603, S607 -- fixed repository helper.
            ["uv", "run", "python", "deploy/ops/json_field.py", "unexpected"],  # noqa: S607
            cwd=ROOT,
            input=document,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(refused.returncode, 3)

    def test_backup_child_idempotency_keys_are_bounded_and_deterministic(self) -> None:
        helper = ["uv", "run", "python", "deploy/ops/idempotency_key.py"]
        base = "a" * 200
        first = run(*helper, base, "restore")
        second = run(*helper, base, "restore")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertLessEqual(len(first.stdout.strip()), 200)
        self.assertRegex(first.stdout.strip(), r"^ops\.[0-9a-f]{64}\.restore$")
        self.assertEqual(run(*helper, "short", "created").returncode, 3)
        self.assertEqual(run(*helper, "valid-key-0000001", "bad/suffix").returncode, 3)
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("deploy/ops/idempotency_key.py", makefile)

    def test_ops_secret_boundary_drops_all_privileges(self) -> None:
        source = (ROOT / "deploy/ops/entrypoint.sh").read_text()
        self.assertIn("/run/secrets/postgres-password", source)
        self.assertIn("/run/secrets/age-identity", source)
        self.assertIn("400|440|600|640", source)
        self.assertIn("! -L", source)
        self.assertIn("--bounding-set=-all", source)
        self.assertIn("--no-new-privs", source)


if __name__ == "__main__":
    unittest.main()
