from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deploy.qualification.recovery import (
    RecoveryQualification,
    last_json_object,
    service_state,
    signer_identity,
)
from deploy.qualification.run import QualificationFailure


class RecoveryQualificationTests(unittest.TestCase):
    def test_last_json_object_is_bounded_and_ignores_entrypoint_chatter(self) -> None:
        self.assertEqual(
            last_json_object('notice\n{"checkpoint":3,"charge_count":1}\n'),
            {
                "checkpoint": 3,
                "charge_count": 1,
            },
        )
        with self.assertRaises(QualificationFailure):
            last_json_object("not-json")
        with self.assertRaises(QualificationFailure):
            last_json_object("x" * 65_537)

    def test_service_state_requires_one_exact_service(self) -> None:
        rows = [{"Service": "consumer", "State": "running", "Health": "healthy"}]
        self.assertEqual(service_state(rows, "consumer"), ("running", "healthy"))
        with self.assertRaises(QualificationFailure):
            service_state(rows, "postgres")
        with self.assertRaises(QualificationFailure):
            service_state(rows + rows, "consumer")

    def test_signer_identity_allowlists_pinned_upstream_fields(self) -> None:
        raw = """Container sensitive-name Creating
Livepeer Node Version: 0.9.2-e8dcf7a3
Golang runtime version: gc go1.27.0
Architecture: amd64
Operating system: linux
"""
        self.assertEqual(
            signer_identity(raw),
            {
                "version": "0.9.2-e8dcf7a3",
                "go_runtime": "gc go1.27.0",
                "architecture": "amd64",
                "operating_system": "linux",
            },
        )
        with self.assertRaises(QualificationFailure):
            signer_identity(raw.replace("e8dcf7a3", "unreviewed"))

    def test_recovery_runner_uses_private_disposable_signer_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = RecoveryQualification(Path(directory) / "evidence.json")
            harness.prepare()
            self.addCleanup(harness.temp.cleanup if harness.temp is not None else lambda: None)
            self.assertIsNotNone(harness.env_file)
            env_file = harness.env_file
            if env_file is None:
                self.fail("prepare did not create an environment file")
            source = env_file.read_text()
            self.assertIn("SIGNER_KEYSTORE_HOST_FILE=", source)
            self.assertIn("SIGNER_PASSWORD_HOST_FILE=", source)
            self.assertNotIn("operator_only_not_exercised", source)

    def test_runner_has_explicit_financial_and_fault_boundaries(self) -> None:
        source = Path("deploy/qualification/recovery.py").read_text()
        probe = Path("deploy/qualification/recovery_probe.py").read_text()
        self.assertIn('"funded_signing": "operator_only_not_exercised"', source)
        self.assertIn('self.restart("postgres")', source)
        self.assertIn('self.restart("redpanda")', source)
        self.assertIn('self.restart("consumer")', source)
        self.assertIn('evidence["faults_restored_before_cleanup"]', source)
        self.assertIn("outcomes[:3]", probe)
        for outcome in ('"settled"', '"duplicate"', '"quarantined"'):
            self.assertIn(outcome, probe)
        self.assertIn("RecordsToDelete(before_offset=2)", probe)
        self.assertIn('("transport_gap", "open", "transport_gap")', probe)


if __name__ == "__main__":
    unittest.main()
