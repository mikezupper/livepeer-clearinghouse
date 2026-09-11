from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*command: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed test commands within the checkout.
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class RuntimeTests(unittest.TestCase):
    def test_compose_contract_is_pinned_private_and_ordered(self) -> None:
        result = run(
            "docker",
            "compose",
            "--profile",
            "signer-check",
            "--env-file",
            ".env.example",
            "config",
            "--format",
            "json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        model = json.loads(result.stdout)
        services = model["services"]
        expected = {
            "postgres",
            "redpanda",
            "redpanda-init",
            "migrate",
            "bootstrap-operator",
            "api",
            "consumer",
            "signer-volume-init",
            "remote-signer",
            "admin-web",
            "user-web",
            "edge",
        }
        self.assertTrue(expected.issubset(services))
        self.assertNotIn("profiles", services["remote-signer"])
        self.assertEqual(services["remote-signer"]["platform"], "linux/amd64")
        self.assertIn("healthcheck", services["remote-signer"])
        for name in ("postgres", "redpanda"):
            self.assertIn("@sha256:", services[name]["image"])
        self.assertIn("-h 127.0.0.1", services["postgres"]["healthcheck"]["test"][1])
        published = [name for name, service in services.items() if service.get("ports")]
        self.assertEqual(published, ["edge"])
        self.assertTrue(model["networks"]["database"]["internal"])
        self.assertTrue(model["networks"]["broker"]["internal"])
        self.assertNotIn("app", services["redpanda"]["networks"])
        self.assertEqual(set(services["edge"]["networks"]), {"app", "ingress"})
        self.assertFalse(model["networks"]["ingress"].get("internal", False))
        ingress_members = {
            name for name, service in services.items() if "ingress" in service.get("networks", {})
        }
        self.assertEqual(ingress_members, {"edge"})
        self.assertEqual(services["edge"]["networks"]["ingress"]["gw_priority"], 1)
        edge_config = (ROOT / "deploy/edge.Caddyfile").read_text()
        for signer_path in (
            "/generate-live-payment",
            "/sign-orchestrator-info",
            "/discover-orchestrators",
        ):
            self.assertIn(signer_path, edge_config)
        self.assertIn("reverse_proxy remote-signer:8935", edge_config)
        self.assertIn("healthcheck", services["consumer"])
        self.assertEqual(
            set(services["migrate"]["environment"]),
            {
                "CLEARINGHOUSE_DATABASE_HOST",
                "CLEARINGHOUSE_DATABASE_NAME",
                "CLEARINGHOUSE_DATABASE_PASSWORD_FILE",
                "CLEARINGHOUSE_DATABASE_PORT",
                "CLEARINGHOUSE_DATABASE_USER",
                "CLEARINGHOUSE_ENVIRONMENT",
                "CLEARINGHOUSE_SECRET_OWNER_UID",
            },
        )
        api_secrets = {item["source"] for item in services["api"]["secrets"]}
        consumer_secrets = {item["source"] for item in services["consumer"].get("secrets", [])}
        self.assertIn("google-client-secret", api_secrets)
        self.assertNotIn("google-client-secret", consumer_secrets)
        self.assertNotIn("CLEARINGHOUSE_AUTH_PEPPER", services["api"]["environment"])
        self.assertIn("CLEARINGHOUSE_AUTH_PEPPER_FILE", services["api"]["environment"])
        self.assertNotIn("CLEARINGHOUSE_DATABASE_URL", services["api"]["environment"])
        self.assertNotIn("POSTGRES_PASSWORD", services["postgres"]["environment"])
        self.assertEqual(
            services["postgres"]["environment"]["POSTGRES_PASSWORD_FILE"],
            "/run/secrets/postgres-password",
        )
        for forbidden in ("AUTH_", "CREDENTIAL_", "SESSION_PEPPER", "WEBHOOK_SECRET"):
            self.assertFalse(
                any(forbidden in name for name in services["consumer"]["environment"]), forbidden
            )
        self.assertEqual(
            {item["source"] for item in services["bootstrap-operator"]["secrets"]},
            {
                "postgres-password",
                "auth-pepper",
                "operator-bootstrap-email",
                "operator-bootstrap-secret",
            },
        )
        self.assertNotIn("secret-volume-init", services)
        self.assertNotIn("user", services["api"])
        self.assertEqual(
            set(services["api"]["cap_add"]),
            {"DAC_OVERRIDE", "SETPCAP", "SETGID", "SETUID"},
        )
        self.assertEqual(services["remote-signer"]["user"], "0:0")
        self.assertIn("SIGNER_ORCH_ADDR", services["remote-signer"]["environment"])
        self.assertIn("SIGNER_ORCH_ADDR", services["signer-diagnostics"]["environment"])
        volume_init = services["signer-volume-init"]
        self.assertEqual(volume_init["entrypoint"], ["/bin/sh", "-c"])
        self.assertEqual(volume_init["command"], ["chown 10001:10001 /data"])
        self.assertEqual(volume_init["cap_add"], ["CHOWN"])
        self.assertNotIn("secrets", volume_init)
        self.assertIn("/runtime-secrets", " ".join(services["remote-signer"]["tmpfs"]))
        signer_health = " ".join(services["remote-signer"]["healthcheck"]["test"])
        self.assertIn("--bounding-set=-chown,-dac_override,-setpcap,-setgid,-setuid", signer_health)
        self.assertNotIn("-caps=-all", signer_health)
        self.assertEqual(
            services["api"]["depends_on"]["bootstrap-operator"]["condition"],
            "service_completed_successfully",
        )
        self.assertEqual(
            services["consumer"]["depends_on"]["redpanda-init"]["condition"],
            "service_completed_successfully",
        )
        rendered = result.stdout.lower()
        for excluded in ("openmeter", "stripe", "auth0", "turnkey", "pymthouse"):
            self.assertNotIn(excluded, rendered)

    def test_make_help_exposes_required_commands(self) -> None:
        result = run("make", "help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for target in ("help", "build", "up", "down", "test", "smoke"):
            self.assertIn(target, result.stdout)

    def test_make_init_env_targets_local_env_not_runtime_fallback(self) -> None:
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("LOCAL_ENV ?= .env", makefile)
        self.assertIn(
            "ENV_FILE ?= $(if $(wildcard $(LOCAL_ENV)),$(LOCAL_ENV),.env.example)",
            makefile,
        )
        init_recipe = makefile.split("init-env:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
        self.assertIn('cp .env.example "$(LOCAL_ENV)"', init_recipe)
        self.assertNotIn('cp .env.example "$(ENV_FILE)"', init_recipe)

    def test_compose_interpolates_public_config_and_isolates_bootstrap(self) -> None:
        example = (ROOT / ".env.example").read_text()
        with tempfile.NamedTemporaryFile("w", dir=ROOT, delete=False) as handle:
            env_file = Path(handle.name)
            handle.write(example)
            handle.write("\nCLEARINGHOUSE_ENVIRONMENT=production\n")
            handle.write("CLEARINGHOUSE_AUTH_COOKIE_SECURE=true\n")
            handle.write("CLEARINGHOUSE_AUTH_ALLOWED_ORIGINS=https://clear.example\n")
            handle.write("CLEARINGHOUSE_AUTH_SUCCESS_REDIRECT_URL=https://clear.example/\n")
            handle.write("CLEARINGHOUSE_SIGNER_URL=https://clear.example/signer\n")
            handle.write("CLEARINGHOUSE_SIGNER_DISCOVERY_URL=https://clear.example/signer\n")
            handle.write("CLEARINGHOUSE_AUTH_GOOGLE_ENABLED=true\n")
            handle.write("CLEARINGHOUSE_AUTH_GOOGLE_CLIENT_ID=google-id\n")
            handle.write("CLEARINGHOUSE_AUTH_GOOGLE_REDIRECT_URI=https://clear.example/google\n")
            handle.write("CLEARINGHOUSE_AUTH_GITHUB_ENABLED=true\n")
            handle.write("CLEARINGHOUSE_AUTH_GITHUB_CLIENT_ID=github-id\n")
            handle.write("CLEARINGHOUSE_AUTH_GITHUB_REDIRECT_URI=https://clear.example/github\n")
            bootstrap_email = Path(handle.name + ".email")
            bootstrap_secret = Path(handle.name + ".secret")
            bootstrap_email.write_text("operator@example.test\n")
            bootstrap_secret.write_text("x" * 32 + "\n")
            bootstrap_email.chmod(0o600)
            bootstrap_secret.chmod(0o600)
            handle.write(f"CLEARINGHOUSE_OPERATOR_BOOTSTRAP_EMAIL_HOST_FILE={bootstrap_email}\n")
            handle.write(f"CLEARINGHOUSE_OPERATOR_BOOTSTRAP_SECRET_HOST_FILE={bootstrap_secret}\n")
        self.addCleanup(env_file.unlink, missing_ok=True)
        self.addCleanup(bootstrap_email.unlink, missing_ok=True)
        self.addCleanup(bootstrap_secret.unlink, missing_ok=True)
        result = run("docker", "compose", "--env-file", str(env_file), "config", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)["services"]
        api = services["api"]["environment"]
        self.assertEqual(api["CLEARINGHOUSE_ENVIRONMENT"], "production")
        self.assertEqual(api["CLEARINGHOUSE_AUTH_COOKIE_SECURE"], "true")
        self.assertEqual(api["CLEARINGHOUSE_AUTH_GOOGLE_CLIENT_ID"], "google-id")
        self.assertEqual(api["CLEARINGHOUSE_AUTH_GITHUB_CLIENT_ID"], "github-id")
        self.assertNotIn("CLEARINGHOUSE_OPERATOR_BOOTSTRAP_EMAIL", api)
        bootstrap = services["bootstrap-operator"]["environment"]
        self.assertEqual(
            bootstrap["CLEARINGHOUSE_OPERATOR_BOOTSTRAP_EMAIL_FILE"],
            "/run/secrets/operator-bootstrap-email",
        )
        self.assertEqual(
            bootstrap["CLEARINGHOUSE_OPERATOR_BOOTSTRAP_SECRET_FILE"],
            "/run/secrets/operator-bootstrap-secret",
        )
        self.assertNotIn("CLEARINGHOUSE_OPERATOR_BOOTSTRAP_SECRET", bootstrap)
        self.assertNotIn("CLEARINGHOUSE_AUTH_GOOGLE_ENABLED", bootstrap)

    def test_development_secret_generation_is_ignored_and_restrictive(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            result = run("sh", "deploy/prepare-development-secrets.sh", directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            for path in Path(directory).iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        ignored = run(
            "git",
            "check-ignore",
            "deploy/secrets/private-password",
            "tmp/runtime-secrets/auth-pepper",
            ".env.production",
            "prod.env",
            "operator.token",
        )
        self.assertEqual(ignored.returncode, 0, ignored.stderr)
        self.assertEqual(len(ignored.stdout.splitlines()), 5)

    def test_production_preflight_rejects_default_database_and_checkout_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory)
            safe = external / "safe"
            safe.write_text("not-a-real-secret-value\n")
            safe.chmod(0o600)
            password = external / "postgres-password"
            password.write_text("clearinghouse\n")
            password.chmod(0o600)
            required = (
                "POSTGRES_PASSWORD_HOST_FILE",
                "CLEARINGHOUSE_AUTH_PEPPER_HOST_FILE",
                "CLEARINGHOUSE_CREDENTIAL_PEPPER_HOST_FILE",
                "CLEARINGHOUSE_AUTH_RESEND_API_KEY_HOST_FILE",
                "CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET_HOST_FILE",
                "CLEARINGHOUSE_SIGNER_SESSION_PEPPER_HOST_FILE",
                "SIGNER_KEYSTORE_HOST_FILE",
                "SIGNER_PASSWORD_HOST_FILE",
            )
            env_file = external / "production.env"
            base = [
                "CLEARINGHOUSE_ENVIRONMENT=production",
                "POSTGRES_USER=clearinghouse",
                "POSTGRES_DB=clearinghouse",
                *(
                    f"{name}={password if name == 'POSTGRES_PASSWORD_HOST_FILE' else safe}"
                    for name in required
                ),
            ]
            env_file.write_text("\n".join(base))
            weak = run(
                "uv", "run", "python", "deploy/runtime_preflight.py", "--env-file", str(env_file)
            )
            self.assertNotEqual(weak.returncode, 0)
            self.assertIn("strong non-default", weak.stderr)
            password.write_text("9qZ!vN4@tR7#xK2$\n")
            with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as checkout_directory:
                checkout_secret = Path(checkout_directory) / "value"
                checkout_secret.write_text("not-a-real-secret-value\n")
                checkout_secret.chmod(0o600)
                env_file.write_text(
                    "\n".join(
                        line
                        if not line.startswith("CLEARINGHOUSE_AUTH_PEPPER_HOST_FILE=")
                        else f"CLEARINGHOUSE_AUTH_PEPPER_HOST_FILE={checkout_secret}"
                        for line in base
                    )
                )
                checkout = run(
                    "uv",
                    "run",
                    "python",
                    "deploy/runtime_preflight.py",
                    "--env-file",
                    str(env_file),
                )
                self.assertNotEqual(checkout.returncode, 0)
                self.assertIn("outside the repository", checkout.stderr)
                self.assertNotIn("not-a-real-secret-value", checkout.stderr)

            unsafe = external / "unsafe"
            unsafe.write_text("safe-length-secret-value\n")
            unsafe.chmod(0o644)
            env_file.write_text(
                "\n".join(
                    line
                    if not line.startswith("CLEARINGHOUSE_AUTH_PEPPER_HOST_FILE=")
                    else f"CLEARINGHOUSE_AUTH_PEPPER_HOST_FILE={unsafe}"
                    for line in base
                )
            )
            unsafe_mode = run(
                "uv", "run", "python", "deploy/runtime_preflight.py", "--env-file", str(env_file)
            )
            self.assertNotEqual(unsafe_mode.returncode, 0)
            self.assertIn("must use mode", unsafe_mode.stderr)

            link = external / "link"
            link.symlink_to(safe)
            env_file.write_text(
                "\n".join(
                    line
                    if not line.startswith("CLEARINGHOUSE_AUTH_PEPPER_HOST_FILE=")
                    else f"CLEARINGHOUSE_AUTH_PEPPER_HOST_FILE={link}"
                    for line in base
                )
            )
            symlink = run(
                "uv", "run", "python", "deploy/runtime_preflight.py", "--env-file", str(env_file)
            )
            self.assertNotEqual(symlink.returncode, 0)
            self.assertIn("must not be a symlink", symlink.stderr)

    def test_make_test_enforces_backend_coverage(self) -> None:
        makefile = (ROOT / "Makefile").read_text()
        test_recipe = makefile.split("\ntest:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
        for target in (
            "quality-repository",
            "quality-python",
            "quality-contracts",
            "quality-frontend",
            "test-backend-unit",
            "test-backend-live",
            "test-migrations",
            "test-admin-web",
            "test-user-web",
            "test-shared-web",
        ):
            self.assertIn(target, test_recipe)

        live_recipe = makefile.split("test-backend-live:", maxsplit=1)[1].split("\n\n", maxsplit=1)[
            0
        ]
        self.assertIn("backend-test", live_recipe)
        self.assertIn("--volumes --remove-orphans", live_recipe)
        self.assertIn('cd "$(CURDIR)"', live_recipe)
        self.assertIn("chmod 666 backend/coverage.json", live_recipe)

        compose = (ROOT / "compose.yaml").read_text()
        backend_test = compose.split("  backend-test:", maxsplit=1)[1].split(
            "\n  signer-volume-init:", maxsplit=1
        )[0]
        self.assertIn("--cov=clearinghouse", backend_test)
        self.assertIn("--cov-branch", backend_test)
        self.assertIn("backend/scripts/check_coverage.py", backend_test)
        self.assertIn("test_onboarding_postgres.py", backend_test)
        self.assertIn("test_operations_postgres.py", backend_test)
        self.assertIn('profiles: ["test"]', backend_test)
        self.assertIn("CLEARINGHOUSE_TEST_UID", backend_test)

    def test_backend_entrypoint_resolves_only_allowlisted_secret_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "pepper"
            secret.write_text("expected-value\n")
            secret.chmod(0o600)
            env = {
                "PATH": os.environ["PATH"],
                "CLEARINGHOUSE_AUTH_PEPPER": "expected-value",
            }
            result = run(
                "/bin/sh",
                "deploy/backend-entrypoint.sh",
                "--validate-only",
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            arbitrary = run(
                "/bin/sh",
                "deploy/backend-entrypoint.sh",
                "--validate-only",
                env={
                    "PATH": os.environ["PATH"],
                    "CLEARINGHOUSE_AUTH_PEPPER_FILE": str(secret),
                },
            )
            self.assertNotEqual(arbitrary.returncode, 0)
            self.assertIn("must be /run/secrets/auth-pepper", arbitrary.stderr)
            conflict = run(
                "/bin/sh",
                "deploy/backend-entrypoint.sh",
                "/bin/true",
                env=env | {"CLEARINGHOUSE_AUTH_PEPPER_FILE": str(secret)},
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertNotIn("expected-value", conflict.stderr)

        entrypoint = (ROOT / "deploy/backend-entrypoint.sh").read_text()
        self.assertIn("[ ! -L", entrypoint)
        self.assertNotIn("|444", entrypoint)
        self.assertNotIn("|644", entrypoint)
        self.assertIn("--bounding-set=-all", entrypoint)
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("/proc/1/status", makefile)
        self.assertIn("CapEff", makefile)
        self.assertIn("CapBnd", makefile)
        self.assertIn("exec -T remote-signer", makefile)

    def test_database_url_percent_encodes_reserved_password_characters(self) -> None:
        secret = "p@ss:#/%"  # noqa: S105 -- reserved-character test fixture.
        env = {
            "PATH": os.environ["PATH"],
            "CLEARINGHOUSE_DATABASE_USER": "service_user",
            "CLEARINGHOUSE_DATABASE_PASSWORD": secret,
            "CLEARINGHOUSE_DATABASE_HOST": "postgres",
            "CLEARINGHOUSE_DATABASE_PORT": "5432",
            "CLEARINGHOUSE_DATABASE_NAME": "clearinghouse",
        }
        result = run("uv", "run", "python", "deploy/database_url.py", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "postgresql+asyncpg://service_user:p%40ss%3A%23%2F%25@postgres:5432/clearinghouse",
        )
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_dockerfiles_pin_external_build_inputs(self) -> None:
        backend = (ROOT / "deploy/backend.Dockerfile").read_text()
        frontend = (ROOT / "deploy/frontend.Dockerfile").read_text()
        edge = (ROOT / "deploy/edge.Dockerfile").read_text()
        signer = (ROOT / "deploy/signer/Dockerfile").read_text()
        for source in (backend, frontend, edge, signer):
            self.assertIn("@sha256:", source)
        self.assertIn("uv sync --frozen --no-dev --no-editable", backend)
        self.assertIn('"--proxy-headers", "--forwarded-allow-ips=*"', backend)
        self.assertIn("npm ci --ignore-scripts", frontend)
        self.assertIn("USER 0:0", signer)
        self.assertIn("--reuid=10001", (ROOT / "deploy/signer/entrypoint.sh").read_text())
        self.assertIn("--reuid=10001", (ROOT / "deploy/backend-entrypoint.sh").read_text())
        self.assertIn(
            "--chmod=0555 --chown=0:0 deploy/backend-entrypoint.sh deploy/database_url.py",
            backend,
        )
        self.assertNotIn("npm install --global", frontend)


if __name__ == "__main__":
    unittest.main()
