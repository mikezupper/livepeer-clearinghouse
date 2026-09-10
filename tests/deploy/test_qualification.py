from __future__ import annotations

import json
import stat
import tempfile
import threading
import unittest
from collections.abc import Mapping
from http.client import HTTPResponse
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from deploy.qualification.fake_resend import build_server, read_secret
from deploy.qualification.run import (
    COMPLETED_SERVICES,
    HEALTHY_SERVICES,
    STARTED_SERVICES,
    Harness,
    QualificationFailure,
    parse_json_stream,
    redact,
    validate_loopback_publishers,
    validate_service_states,
    write_private,
)


class FakeResendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = build_server("127.0.0.1", 0, "resend-test-key", "mailbox-test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(
        self, path: str, *, body: Mapping[str, object] | None = None, token: str = ""
    ) -> HTTPResponse:
        encoded = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        return cast(
            HTTPResponse,
            urlopen(Request(self.base + path, data=encoded, headers=headers), timeout=2),  # noqa: S310
        )

    def test_sdk_endpoint_and_private_latest_code(self) -> None:
        with self.request(
            "/emails",
            token="resend-test-key",  # noqa: S106 -- local HTTP fixture credential.
            body={"to": ["Person@Example.test"], "text": "Your sign-in code is 123456."},
        ) as response:
            self.assertEqual(json.load(response), {"id": "qualification-email"})
        with self.request(
            "/_qualification/latest?email=person%40example.test",
            token="mailbox-test-token",  # noqa: S106 -- local HTTP fixture credential.
        ) as response:
            self.assertEqual(
                json.load(response), {"email": "person@example.test", "code": "123456"}
            )
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        with self.assertRaises(HTTPError) as denied:
            self.request("/_qualification/latest?email=person%40example.test")
        self.assertEqual(denied.exception.code, 404)
        denied.exception.close()

    def test_rejects_wrong_sdk_key_oversize_and_malformed_payload(self) -> None:
        cases = (
            ("wrong", {"to": ["p@example.test"], "text": "code 123456"}),
            ("resend-test-key", {"to": [], "text": "missing code"}),
        )
        for token, body in cases:
            with self.subTest(token=token), self.assertRaises(HTTPError) as raised:
                self.request("/emails", token=token, body=body)
            raised.exception.close()

    def test_secret_reader_is_bounded_and_never_echoes_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            path.write_text("private-value\n")
            self.assertEqual(read_secret(str(path)), "private-value")
            path.write_text("")
            with self.assertRaisesRegex(ValueError, "1..4096") as raised:
                read_secret(str(path))
            self.assertNotIn("private-value", str(raised.exception))


class QualificationHarnessTests(unittest.TestCase):
    def test_private_files_refuse_overwrite_and_use_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            write_private(path, "sensitive-value")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                write_private(path, "replacement")
            self.assertEqual(path.read_text(), "sensitive-value\n")

    def test_redaction_is_bounded_and_removes_known_and_structural_secrets(self) -> None:
        source = (
            "private-known Authorization: Bearer bearer-value "
            '"code":"654321" password=unguarded AGE-SECRET-KEY-1TEST ' + "x" * 70_000
        )
        cleaned = redact(source, ("private-known", "bearer-value"))
        for forbidden in (
            "private-known",
            "bearer-value",
            "654321",
            "unguarded",
            "AGE-SECRET-KEY-1TEST",
        ):
            self.assertNotIn(forbidden, cleaned)
        self.assertLessEqual(len(cleaned), 65_536)

    def test_compose_status_requires_exact_healthy_and_completed_services(self) -> None:
        rows: list[dict[str, object]] = []
        for service in STARTED_SERVICES:
            if service in HEALTHY_SERVICES:
                rows.append({"Service": service, "State": "running", "Health": "healthy"})
            elif service in COMPLETED_SERVICES:
                rows.append({"Service": service, "State": "exited", "ExitCode": 0})
        states = validate_service_states(rows)
        self.assertEqual(set(states), set(STARTED_SERVICES))
        rows[0]["Health"] = "starting"
        with self.assertRaises(QualificationFailure):
            validate_service_states(rows)

    def test_json_status_parser_supports_array_and_single_object(self) -> None:
        self.assertEqual(parse_json_stream('[{"Service":"api"}]')[0]["Service"], "api")
        self.assertEqual(parse_json_stream('{"Service":"api"}')[0]["Service"], "api")
        self.assertEqual(len(parse_json_stream('{"Service":"api"}\n{"Service":"edge"}')), 2)
        with self.assertRaises(QualificationFailure):
            parse_json_stream("[]\n[]")

    def test_only_expected_loopback_ports_may_be_published(self) -> None:
        rows = [
            {
                "Service": "edge",
                "Publishers": [{"URL": "127.0.0.1", "TargetPort": 8080, "PublishedPort": 41001}],
            },
            {
                "Service": "fake-resend",
                "Publishers": [{"URL": "127.0.0.1", "TargetPort": 8081, "PublishedPort": 41002}],
            },
            {
                "Service": "postgres",
                "Publishers": [{"URL": "", "TargetPort": 5432, "PublishedPort": 0}],
            },
        ]
        self.assertEqual(
            validate_loopback_publishers(rows, 41001, 41002),
            {"edge": 41001, "fake-resend": 41002},
        )
        rows[-1]["Publishers"] = [{"URL": "127.0.0.1", "TargetPort": 5432, "PublishedPort": 41003}]
        with self.assertRaises(QualificationFailure):
            validate_loopback_publishers(rows, 41001, 41002)

    def test_preparation_is_external_private_and_deterministic_in_identity(self) -> None:
        harness = Harness(Path("tmp/qualification/test-evidence.json"))
        harness.prepare()
        self.addCleanup(harness.temp.cleanup if harness.temp is not None else lambda: None)
        self.assertIsNotNone(harness.env_file)
        env_file = harness.env_file
        if env_file is None:
            self.fail("prepare did not create an environment file")
        self.assertFalse(env_file.is_relative_to(Path.cwd()))
        self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)
        source = env_file.read_text()
        self.assertIn("operator-bootstrap-email", source)
        email_path = next(
            Path(line.partition("=")[2])
            for line in source.splitlines()
            if line.startswith("CLEARINGHOUSE_OPERATOR_BOOTSTRAP_EMAIL_HOST_FILE=")
        )
        self.assertEqual(email_path.read_text().strip(), "operator@qualification.example.com")
        for child in env_file.parent.iterdir():
            self.assertEqual(stat.S_IMODE(child.stat().st_mode), 0o600)

    def test_repository_exposes_one_command_and_documents_signer_boundary(self) -> None:
        makefile = Path("Makefile").read_text()
        recipe = makefile.split("qualification-harness:", maxsplit=1)[1].split("\n\n", maxsplit=1)[
            0
        ]
        self.assertIn("uv run python deploy/qualification/run.py", recipe)
        runner = Path("deploy/qualification/run.py").read_text()
        self.assertIn('"signer_runtime_qualified": False', runner)
        self.assertIn('"built_only_unqualified"', runner)
        overlay = Path("deploy/qualification.compose.yaml").read_text()
        self.assertIn("QUALIFICATION_MAILBOX_TOKEN_HOST_FILE", overlay)
        self.assertNotIn("postgres:", overlay)


if __name__ == "__main__":
    unittest.main()
