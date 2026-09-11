from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "signer_preflight", ROOT / "deploy/signer/preflight.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("signer preflight module is missing")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class SignerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.key = self.directory / "key.json"
        self.password = self.directory / "password"
        self.key.write_text(
            json.dumps(
                {
                    "version": 3,
                    "address": "1" * 40,
                    "crypto": {"cipher": "aes-128-ctr", "ciphertext": "fixture", "kdf": "scrypt"},
                }
            )
        )
        self.password.write_text("fixture-password\n")
        self.password.chmod(0o600)
        self.env = {
            "PATH": os.environ["PATH"],
            "SIGNER_MODE": "evaluation",
            "SIGNER_NETWORK": "arbitrum-one-mainnet",
            "SIGNER_CHAIN_ID": "42161",
            "SIGNER_CONTROLLER": preflight.CONTROLLER,
            "SIGNER_ETH_ADDR": "0x" + "1" * 40,
            "ETH_RPC_URL": "https://rpc.invalid/rpc",
            "REMOTE_SIGNER_WEBHOOK_URL": "http://api:8000/v1/compat/go-livepeer/authorize",
            "WEBHOOK_SECRET": "test_fixture_not_a_real_secret_123456",
            "KAFKA_BROKERS": "redpanda:9092",
            "KAFKA_GATEWAY_TOPIC": "livepeer-gateway-events",
            "KAFKA_RETENTION_MS": "604800000",
            "SIGNER_ETH_KEYSTORE_PATH": str(self.key),
            "SIGNER_PASSWORD_FILE": str(self.password),
            "SIGNER_DATA_DIR": str(self.directory),
            "SIGNER_MIN_GAS_WEI": "1",
            "SIGNER_MIN_DEPOSIT_WEI": "2",
            "SIGNER_MIN_RESERVE_WEI": "3",
        }

    def shell(
        self, updates: dict[str, str] | None = None, argument: str = "--validate-only"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 — fixed test executable and fixture inputs.
            ["/bin/sh", str(ROOT / "deploy/signer/entrypoint.sh"), argument],
            env=self.env | (updates or {}),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_valid_configuration_and_modes(self) -> None:
        preflight.validate(self.env)
        preflight.validate(self.env | {"SIGNER_MODE": "production"})
        preflight.validate(self.env | {"SIGNER_NETWORK": "custom", "SIGNER_CHAIN_ID": "1337"})

    def test_launch_uses_upstream_with_mandatory_controls_and_no_argv_secrets(self) -> None:
        source = (ROOT / "deploy/signer/entrypoint.sh").read_text()
        self.assertIn("/usr/local/bin/livepeer", source)
        self.assertIn("-remoteSignerAllowNoAuth=false", source)
        self.assertIn("-monitor=true", source)
        self.assertIn("-cliAddr=127.0.0.1:4935", source)
        self.assertIn("unset WEBHOOK_SECRET ETH_RPC_URL", source)
        capability_drop = "-chown,-dac_override,-setpcap,-setgid,-setuid"
        self.assertIn(f"--inh-caps={capability_drop}", source)
        self.assertIn(f"--ambient-caps={capability_drop}", source)
        self.assertIn(f"--bounding-set={capability_drop}", source)
        self.assertNotIn("-caps=-all", source)
        self.assertIn("SIGNER_ETH_KEYSTORE_PATH must be /run/secrets/signer-keystore.json", source)
        self.assertIn('"-orchAddr=${SIGNER_ORCH_ADDR:-}"', source)
        self.assertIn('install -m 0400 "$SIGNER_ETH_KEYSTORE_PATH"', source)
        self.assertIn('chown 10001:10001 "$runtime_secret_dir/signer-keystore.json"', source)
        self.assertNotIn("install -m 0400 -o 10001", source)
        self.assertIn("SIGNER_ETH_KEYSTORE_DIR=$runtime_secret_dir", source)
        self.assertIn('"-ethKeystorePath=$SIGNER_ETH_KEYSTORE_DIR"', source)
        self.assertNotIn('"-ethKeystorePath=$SIGNER_ETH_KEYSTORE_PATH"', source)

    def test_diagnostics_copy_secrets_before_dropping_explicit_capabilities(self) -> None:
        source = (ROOT / "deploy/signer/diagnostic-entrypoint.sh").read_text()
        capability_drop = "-chown,-dac_override,-setpcap,-setgid,-setuid"
        self.assertIn('install -m 0400 "$source_path" "$target_path"', source)
        self.assertIn('chown 10001:10001 "$target_path"', source)
        self.assertNotIn("install -m 0400 -o 10001", source)
        self.assertIn(f"--inh-caps={capability_drop}", source)
        self.assertIn(f"--ambient-caps={capability_drop}", source)
        self.assertIn(f"--bounding-set={capability_drop}", source)
        self.assertNotIn("-caps=-all", source)

    def test_static_orchestrator_addresses_are_validated(self) -> None:
        valid = "https://orch-a.example:8935,orch-b.example:8936"
        preflight.validate(self.env | {"SIGNER_ORCH_ADDR": valid})
        # Fixture paths intentionally fail the wrapper's fixed container-mount
        # boundary after the orchestrator list has passed shell validation.
        valid_shell = self.shell({"SIGNER_ORCH_ADDR": valid})
        self.assertIn("SIGNER_ETH_KEYSTORE_PATH must be", valid_shell.stderr)
        preflight.validate(
            self.env
            | {
                "SIGNER_MODE": "production",
                "SIGNER_ORCH_ADDR": "https://orch-a.example:8935",
            }
        )

        invalid = (
            "orch-a.example",
            "orch-a.example:0",
            "orch-a.example:65536",
            "orch-a.example:abc",
            "orch-a.example:8935,",
            "orch-a.example:8935, orch-b.example:8935",
            "ftp://orch-a.example:8935",
            "https://user:secret@orch-a.example:8935",
            "https://orch-a.example:8935/path",
            "https://orch-a.example:8935?token=secret",
            "https://orch_name.example:8935",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(preflight.InvalidConfiguration):
                    preflight.validate(self.env | {"SIGNER_ORCH_ADDR": value})
                result = self.shell({"SIGNER_ORCH_ADDR": value})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(value, result.stderr)

        for values in (
            {"SIGNER_ORCH_ADDR": "orch-a.example:8935", "SIGNER_REMOTE_DISCOVERY": "false"},
            {"SIGNER_MODE": "production", "SIGNER_ORCH_ADDR": "http://orch-a.example:8935"},
        ):
            with self.assertRaises(preflight.InvalidConfiguration):
                preflight.validate(self.env | values)
            self.assertNotEqual(self.shell(values).returncode, 0)

    def test_invalid_configurations_fail_without_echoing_secrets(self) -> None:
        cases = {
            "SIGNER_MODE": ["", "test"],
            "SIGNER_NETWORK": ["", "offchain"],
            "SIGNER_CHAIN_ID": ["", "0", "1", "NaN"],
            "SIGNER_CONTROLLER": ["", "0x" + "0" * 40],
            "SIGNER_ETH_ADDR": ["", "0x" + "z" * 40, "0x" + "0" * 40, "1" * 42],
            "ETH_RPC_URL": [
                "",
                "file:///secret",
                "https://user:private-secret@rpc.invalid/",
                "https://rpc.invalid/#private-secret",
            ],
            "REMOTE_SIGNER_WEBHOOK_URL": [
                "",
                "https://api/authorize",
                "http://user:private-secret@api/v1/compat/go-livepeer/authorize",
            ],
            "WEBHOOK_SECRET": ["", "short", "x" * 32 + ",Authorization:bad"],
            "SIGNER_PORT": ["abc", "1023", "65536", "4935"],
            "KAFKA_BROKERS": [
                "",
                "broker",
                "broker:9092,other:9092",
                ":9092",
                "broker:0",
                "broker:99999",
                "broker:abc",
                "broker:",
            ],
            "KAFKA_GATEWAY_TOPIC": ["", "bad topic", ".", "..", "x" * 250],
            "SIGNER_REMOTE_DISCOVERY": ["1"],
            "SIGNER_ETH_KEYSTORE_PATH": ["/does/not/exist"],
            "SIGNER_PASSWORD_FILE": ["/does/not/exist"],
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    env = self.env | {name: value}
                    with self.assertRaises(preflight.InvalidConfiguration):
                        preflight.validate(env)
                    result = self.shell({name: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("signer configuration:", result.stderr)
                    self.assertNotIn("private-secret", result.stderr)
                    self.assertNotIn(self.env["WEBHOOK_SECRET"], result.stderr)

    def test_pairing_ports_and_custom_production(self) -> None:
        for values in (
            {"SIGNER_MODE": "production", "SIGNER_NETWORK": "custom"},
            {"LP_KAFKAUSER": "user"},
            {"LP_KAFKAPASSWORD": "secret"},
        ):
            with self.assertRaises(preflight.InvalidConfiguration):
                preflight.validate(self.env | values)
            self.assertNotEqual(self.shell(values).returncode, 0)
        preflight.validate(self.env | {"LP_KAFKAUSER": "user", "LP_KAFKAPASSWORD": "secret"})
        for values in (
            {"SIGNER_HOST_PORT": "4935"},
            {"KAFKA_BROKERS": "broker:99999"},
            {"SIGNER_MIN_GAS_WEI": "0"},
        ):
            with self.assertRaises(preflight.InvalidConfiguration):
                preflight.validate(self.env | values)
        self.assertNotEqual(self.shell({"SIGNER_DATA_DIR": "/does/not/exist"}).returncode, 0)
        self.assertNotEqual(self.shell(argument="-remoteSignerAllowNoAuth").returncode, 0)

    def test_credential_bearing_rpc_paths_and_queries_are_allowed_with_safe_errors(self) -> None:
        for rpc_url in (
            "https://rpc.invalid/provider-key",
            "https://rpc.invalid/v1/provider-key",
            "https://rpc.invalid/?token=provider-key",
        ):
            with self.subTest(rpc_url=rpc_url):
                preflight.validate(self.env | {"ETH_RPC_URL": rpc_url})
                result = self.shell({"ETH_RPC_URL": rpc_url})
                self.assertNotIn(rpc_url, result.stderr)
                self.assertIn(
                    "may print the complete credential-bearing ETH_RPC_URL", result.stderr
                )

    def test_file_failures_and_v3_address_binding(self) -> None:
        for content in ("", "  \n"):
            self.password.write_text(content)
            with self.assertRaises(preflight.InvalidConfiguration):
                preflight.validate(self.env)
            self.assertNotEqual(self.shell().returncode, 0)
        self.password.write_text("fixture")
        self.password.chmod(0o644)
        with self.assertRaises(preflight.InvalidConfiguration):
            preflight.validate(self.env)
        self.password.chmod(0o600)
        for key in (
            "not-json",
            "[]",
            "{}",
            json.dumps(
                {
                    "version": 3,
                    "address": "2" * 40,
                    "crypto": {"cipher": "aes-128-ctr", "ciphertext": "fixture", "kdf": "scrypt"},
                }
            ),
        ):
            self.key.write_text(key)
            with self.assertRaises(preflight.InvalidConfiguration):
                preflight.validate(self.env)
        self.key.write_text("plaintext-secret")
        self.assertNotEqual(self.shell().returncode, 0)

    def test_signer_custody_files_must_be_outside_checkout(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            key = Path(directory) / "signer-keystore.json"
            password = Path(directory) / "signer-password"
            key.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "address": "1" * 40,
                        "crypto": {
                            "cipher": "aes-128-ctr",
                            "ciphertext": "fixture",
                            "kdf": "scrypt",
                        },
                    }
                )
            )
            password.write_text("fixture-password\n")
            password.chmod(0o600)
            with self.assertRaisesRegex(
                preflight.InvalidConfiguration, "outside the repository build context"
            ):
                preflight.validate(
                    self.env
                    | {
                        "SIGNER_ETH_KEYSTORE_PATH": str(key),
                        "SIGNER_PASSWORD_FILE": str(password),
                    }
                )

    def test_literal_env_never_executes_or_interpolates(self) -> None:
        envfile = self.directory / ".env"
        envfile.write_text("# comment\n\nA=\"$(false)\"\nB='literal'\nC=abc\n")
        self.assertEqual(preflight.read_env(envfile), {"A": "$(false)", "B": "literal", "C": "abc"})
        for content in ("export A=secret", "A", 'A="unterminated'):
            envfile.write_text(content)
            with self.assertRaises(preflight.InvalidConfiguration):
                preflight.read_env(envfile)

    def test_canonical_compose_environment_maps_to_signer_contract(self) -> None:
        webhook = self.directory / "webhook"
        webhook.write_text("canonical_webhook_secret_value_123456\n")
        envfile = self.directory / "canonical.env"
        canonical = {
            key: value
            for key, value in self.env.items()
            if key
            not in {
                "REMOTE_SIGNER_WEBHOOK_URL",
                "WEBHOOK_SECRET",
                "KAFKA_BROKERS",
                "KAFKA_GATEWAY_TOPIC",
                "SIGNER_ETH_KEYSTORE_PATH",
                "SIGNER_PASSWORD_FILE",
            }
        }
        canonical.update(
            {
                "SIGNER_KEYSTORE_HOST_FILE": str(self.key),
                "SIGNER_PASSWORD_HOST_FILE": str(self.password),
                "CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET_HOST_FILE": str(webhook),
                "CLEARINGHOUSE_KAFKA_METERING_TOPIC": "canonical-topic",
                "CLEARINGHOUSE_KAFKA_BOOTSTRAP_SERVERS_INTERNAL": "redpanda:9092",
            }
        )
        envfile.write_text("\n".join(f"{key}={value}" for key, value in canonical.items()))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(preflight.main(["--env-file", str(envfile)]), 0)

    def test_rpc_readiness_fails_on_wrong_chain_code_or_funding(self) -> None:
        values = ["0xa4b1", "0x1234", "0x10", "0x100"]
        with patch.object(preflight, "rpc", side_effect=values):
            preflight.check_rpc(self.env)
        for invalid in (
            ["0x1"],
            ["0xa4b1", "0x"],
            ["0xa4b1", "0x1234", "0x0"],
            values[:3] + ["0x0"],
        ):
            with (
                patch.object(preflight, "rpc", side_effect=invalid),
                self.assertRaises(preflight.InvalidConfiguration),
            ):
                preflight.check_rpc(self.env)
        with patch.object(preflight, "request_json", return_value={"result": "0xa4b1"}):
            self.assertEqual(preflight.rpc(self.env, "eth_chainId", []), "0xa4b1")
        with (
            patch.object(
                preflight, "request_json", return_value={"error": {"secret": "never print"}}
            ),
            self.assertRaises(preflight.InvalidConfiguration),
        ):
            preflight.rpc(self.env, "eth_chainId", [])

    def test_broker_probe_requires_configured_topic_and_closes_client(self) -> None:
        admin = MagicMock()
        admin.start = AsyncMock()
        admin.close = AsyncMock()
        admin.list_topics = AsyncMock(return_value={self.env["KAFKA_GATEWAY_TOPIC"]})
        admin.describe_topics = AsyncMock(
            return_value=[
                {
                    "topic": self.env["KAFKA_GATEWAY_TOPIC"],
                    "partitions": [{"partition": 0, "replicas": [0]}],
                }
            ]
        )
        response = MagicMock()
        response.resources = [
            (
                0,
                None,
                2,
                self.env["KAFKA_GATEWAY_TOPIC"],
                [
                    ("cleanup.policy", "delete"),
                    ("retention.ms", self.env["KAFKA_RETENTION_MS"]),
                ],
            )
        ]
        admin.describe_configs = AsyncMock(return_value=[response])
        with patch.object(preflight, "AIOKafkaAdminClient", return_value=admin):
            asyncio.run(preflight.check_broker(self.env))
        admin.start.assert_awaited_once()
        admin.close.assert_awaited_once()
        admin.list_topics = AsyncMock(return_value={"different-topic"})
        with (
            patch.object(preflight, "AIOKafkaAdminClient", return_value=admin),
            self.assertRaises(preflight.InvalidConfiguration),
        ):
            asyncio.run(preflight.check_broker(self.env))

    def test_http_request_is_bounded(self) -> None:
        with patch.object(
            preflight, "urlopen", return_value=io.BytesIO(b'{"result":"ok"}')
        ) as call:
            self.assertEqual(
                preflight.request_json("https://rpc.invalid", {"method": "eth_chainId"}),
                {"result": "ok"},
            )
            self.assertEqual(call.call_args.kwargs["timeout"], 6)
            request = call.call_args.args[0]
            self.assertEqual(request.get_header("Accept"), "application/json")
            self.assertEqual(request.get_header("Content-type"), "application/json")
            self.assertEqual(request.get_header("User-agent"), "go-ethereum/rpc")
        with (
            patch.object(preflight, "urlopen", return_value=io.BytesIO(b"x" * 1_048_577)),
            self.assertRaises(preflight.InvalidConfiguration),
        ):
            preflight.request_json("https://rpc.invalid")

    def test_admin_readiness_is_private_and_checks_deposit_reserve_withdrawal(self) -> None:
        sender = {"Deposit": 2, "Reserve": {"FundsRemaining": 3}, "WithdrawRound": 0}
        address = self.env["SIGNER_ETH_ADDR"].encode()
        with (
            patch.object(preflight, "urlopen", return_value=io.BytesIO(address)),
            patch.object(preflight, "request_json", side_effect=[42161, sender]),
        ):
            preflight.check_admin(self.env, "http://127.0.0.1:4935")
        for endpoint in ("http://remote-signer:4935", "http://127.0.0.1:8935"):
            with self.assertRaises(preflight.InvalidConfiguration):
                preflight.check_admin(self.env, endpoint)
        for invalid in (
            {"Deposit": 1},
            {"Reserve": {"FundsRemaining": 2}},
            {"WithdrawRound": 1},
            {"Reserve": None},
        ):
            with (
                patch.object(preflight, "urlopen", return_value=io.BytesIO(address)),
                patch.object(preflight, "request_json", side_effect=[42161, sender | invalid]),
                self.assertRaises(preflight.InvalidConfiguration),
            ):
                preflight.check_admin(self.env, "http://localhost:4935")
        with (
            patch.object(preflight, "urlopen", return_value=io.BytesIO(b"wrong")),
            self.assertRaises(preflight.InvalidConfiguration),
        ):
            preflight.check_admin(self.env, "http://localhost:4935")
        with (
            patch.object(preflight, "urlopen", return_value=io.BytesIO(address)),
            patch.object(preflight, "request_json", return_value=1),
            self.assertRaises(preflight.InvalidConfiguration),
        ):
            preflight.check_admin(self.env, "http://localhost:4935")

    def test_cli_outputs_safe_errors_and_runs_requested_checks(self) -> None:
        envfile = self.directory / ".env"
        envfile.write_text("\n".join(f"{key}={value}" for key, value in self.env.items()))
        with (
            patch.dict(os.environ, self.env, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(preflight.main([]), 0)
            with (
                patch.object(preflight, "check_rpc") as rpc,
                patch.object(preflight, "check_admin") as admin,
                patch.object(preflight, "check_webhook") as webhook,
            ):
                self.assertEqual(
                    preflight.main(
                        [
                            "--env-file",
                            str(envfile),
                            "--rpc",
                            "--admin-url",
                            "http://localhost:4935",
                            "--webhook",
                        ]
                    ),
                    0,
                )
                rpc.assert_called_once()
                admin.assert_called_once()
                webhook.assert_called_once()
        for error in (
            preflight.InvalidConfiguration("fix configuration"),
            OSError("private-secret"),
        ):
            output = io.StringIO()
            with (
                patch.object(preflight, "validate", side_effect=error),
                contextlib.redirect_stderr(output),
            ):
                self.assertEqual(preflight.main([]), 1)
            self.assertNotIn("private-secret", output.getvalue())

    def test_webhook_probe_requires_typed_denial_not_merely_http_success(self) -> None:
        with patch.object(
            preflight,
            "request_json",
            return_value={"status": 401, "reason": "unknown_credential", "expiry": 0},
        ) as request:
            preflight.check_webhook(self.env)
            self.assertEqual(
                request.call_args.args[2], {"Authorization": "Bearer " + self.env["WEBHOOK_SECRET"]}
            )
        invalid_responses: tuple[object, ...] = (
            {"status": 200},
            {"status": 503, "reason": "ledger_unavailable"},
            {"status": 401},
            {"status": 401, "reason": "unknown_credential", "expiry": 99999},
            [],
        )
        for response in invalid_responses:
            with (
                patch.object(preflight, "request_json", return_value=response),
                self.assertRaises(preflight.InvalidConfiguration),
            ):
                preflight.check_webhook(self.env)


if __name__ == "__main__":
    unittest.main()
